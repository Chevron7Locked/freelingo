"""Background lesson generation.

The today endpoint must never block on the LLM: a busy engine would freeze
the page for minutes. Missing lessons are enqueued in Redis and a background
task generates and persists them; the next poll of the today endpoint
returns the finished lesson.

Tasks within one request are chained: each waits for its predecessor so a
unit's lessons keep their sequence context (lesson 2 knows what lesson 1
taught). The chain is awaited inside the task, never in the request.
"""

from __future__ import annotations

import asyncio

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.database import AsyncSessionLocal
from app.services.lesson_generator import generate_lesson

GENERATION_TTL = 6 * 60 * 60  # entries outlive the lesson row itself


def lesson_key(plan_id: int, week: int, day: int, title: str) -> str:
    return f"lesson:generating:{plan_id}:{week}:{day}:{title}"


async def generate_and_persist_lesson(
    *,
    redis: Redis,
    plan_id: int,
    title: str,
    lesson_type: str,
    cefr_level: str,
    week: int,
    day: int,
    unit_id: str,
    grammar_points: list[str],
    vocabulary_set_ids: list[str],
    target_language: str,
    native_language: str,
    chain: asyncio.Task | None = None,
) -> None:
    """Generate one lesson and persist it with its exercises.

    Runs in its own session so the request that spawned it finishes fast.
    Previous lessons for the unit are re-read here, after any predecessor in
    the chain has committed, so sequence context stays correct.
    """
    key = lesson_key(plan_id, week, day, title)
    try:
        if chain is not None:
            try:
                await chain
            except Exception:
                # The predecessor already logged its failure; still generate.
                pass

        # Sibling lessons already committed for this unit, oldest first.
        previous_lessons: list[dict] = []
        if unit_id:
            from app.models.lesson import Lesson

            async with AsyncSessionLocal() as db:
                rows = await db.execute(
                    select(Lesson)
                    .where(
                        Lesson.study_plan_id == plan_id,
                        Lesson.unit_id == unit_id,
                        Lesson.title != title,
                    )
                    .order_by(Lesson.week_number, Lesson.day_number, Lesson.id)
                )
                previous_lessons = [
                    {
                        "title": row.title,
                        "lesson_type": row.lesson_type,
                        "content": row.content,
                    }
                    for row in rows.scalars().all()
                ]

        content = await generate_lesson(
            cefr_level=cefr_level,
            lesson_type=lesson_type,
            topic=title,
            week=week,
            day=day,
            unit_id=unit_id,
            grammar_points=grammar_points,
            vocabulary_set_ids=vocabulary_set_ids,
            target_language=target_language,
            native_language=native_language,
            previous_lessons=previous_lessons,
        )
        content_dict: dict = (
            content.model_dump() if hasattr(content, "model_dump") else dict(content)
        )

        from app.models.lesson import Exercise, Lesson

        async with AsyncSessionLocal() as db:
            lesson = Lesson(
                study_plan_id=plan_id,
                title=title,
                lesson_type=lesson_type,
                cefr_level=cefr_level,
                week_number=week,
                day_number=day,
                unit_id=unit_id,
                content=content_dict,
            )
            db.add(lesson)
            await db.flush()

            exercises_data = content_dict.get("exercises") or []
            for ex in exercises_data:
                exercise = Exercise(
                    lesson_id=lesson.id,
                    exercise_type=ex.get("type", "multiple_choice"),
                    question=ex.get("question", ""),
                    options=ex.get("options"),
                    correct_answer=ex.get("correct", ""),
                    explanation=ex.get("explanation"),
                )
                db.add(exercise)

            if not exercises_data:
                await db.rollback()
                raise ValueError("Lesson generated with no exercises")

            await db.commit()
            await db.refresh(lesson)
            lesson_id = lesson.id

        await redis.delete(key)
        from app.core.app_logger import get_logger

        get_logger(__name__).info(
            "Background lesson generated for plan %s week %s day %s (lesson %s)",
            plan_id,
            week,
            day,
            lesson_id,
        )
    except IntegrityError:
        # Another task won the race and committed the row: drop the flag and
        # let the next today poll serve the winner's lesson.
        from app.core.app_logger import get_logger

        get_logger(__name__).info(
            "Lesson for plan %s week %s day %s already persisted by another task",
            plan_id,
            week,
            day,
        )
        try:
            await redis.delete(key)
        except Exception:
            pass
    except asyncio.CancelledError:
        try:
            await redis.delete(key)
        except Exception:
            pass
        raise
    except Exception:
        from app.core.app_logger import get_logger

        get_logger(__name__).exception(
            "Background lesson generation failed for plan %s week %s day %s",
            plan_id,
            week,
            day,
        )
        try:
            await redis.delete(key)
        except Exception:
            pass


def spawn_lesson_generation(
    redis: Redis, chain: asyncio.Task | None = None, **kwargs
) -> asyncio.Task:
    """Schedule generation; returns the task so callers can chain."""

    async def _runner() -> None:
        try:
            await generate_and_persist_lesson(redis=redis, chain=chain, **kwargs)
        except asyncio.CancelledError:
            raise
        except Exception:
            # generate_and_persist_lesson already logged it; never let a
            # background failure reach the event loop's default handler.
            pass

    return asyncio.create_task(_runner())
