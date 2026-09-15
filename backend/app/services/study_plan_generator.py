from __future__ import annotations

from app.data.curriculum import distribute_units, get_curriculum_units
from app.schemas.study_plan import (
    DayPlan,
    GeneratedPlan,
    GenerateStudyPlanRequest,
    WeekPlan,
)
from app.services.language_helpers import get_language_name


class PlanCapacityError(ValueError):
    """The requested grid cannot give every curriculum unit a teaching slot."""


def assert_plan_capacity(units: list[object], total_weeks: int, days_per_week: int) -> None:
    """Reject grids that would leave a curriculum unit without a single lesson.

    The final grid coordinate is reserved for the level completion test, so the
    capacity available to teaching is ``total_weeks * days_per_week - 1``. A plan
    below that floor would silently omit units (issue #316), which is why both
    plan-creation entry points call this before mutating any state.

    Raises:
        PlanCapacityError: dimensions are not positive, or the grid is too short
            to cover every curriculum unit.
    """
    if total_weeks < 1 or days_per_week < 1:
        raise PlanCapacityError(
            "Plan dimensions must be positive: both duration_weeks and days_per_week "
            "must be at least 1."
        )

    teaching_slots = total_weeks * days_per_week - 1
    unit_count = len(units)
    if unit_count == 0 or teaching_slots >= unit_count:
        return

    weeks_needed = -(-(unit_count + 1) // days_per_week)  # ceil, keeping days_per_week
    raise PlanCapacityError(
        f"This plan is too short to cover all {unit_count} curriculum units: "
        f"{total_weeks} weeks × {days_per_week} days leaves {max(teaching_slots, 0)} "
        f"teaching slot(s) once the final completion test is reserved. Choose at least "
        f"{unit_count + 1} teaching days — for example {weeks_needed} weeks × "
        f"{days_per_week} days."
    )


async def generate_study_plan(
    request: GenerateStudyPlanRequest,
    target_language: str = "en-GB",
) -> GeneratedPlan:
    """
    Build a curriculum-driven study plan skeleton.
    No LLM call — purely deterministic from the static curriculum.
    LLM is called separately per-lesson when the user opens one for the first time.
    """
    lang_name = get_language_name(target_language)
    units = get_curriculum_units(request.cefr_level, target_language)
    # Defence in depth: routers reject undersized plans before touching state,
    # and the generator refuses to build a plan that would omit units.
    assert_plan_capacity(units, request.duration_weeks, request.days_per_week)
    lesson_slots = distribute_units(
        units=units,
        total_weeks=request.duration_weeks,
        days_per_week=request.days_per_week,
        target_language=target_language,
    )

    weeks_map: dict[int, list[dict]] = {}
    for slot in lesson_slots:
        w = slot["week"]
        weeks_map.setdefault(w, []).append(slot)

    weekly_plan: list[WeekPlan] = []
    for week_num in sorted(weeks_map):
        slots_in_week = weeks_map[week_num]
        theme = slots_in_week[0]["unit_title"] if slots_in_week else ""
        days = [
            DayPlan(
                day=s["day"],
                lesson_type=s["lesson_type"],
                title=s["title"],
                objectives=s["objectives"],
                estimated_minutes=s["estimated_minutes"],
                unit_id=s["unit_id"],
                grammar_points=s.get("grammar_points", []),
                vocabulary_set_ids=s.get("vocabulary_set_ids", []),
            )
            for s in slots_in_week
        ]
        weekly_plan.append(WeekPlan(week=week_num, theme=theme, days=days))

    return GeneratedPlan(
        title=f"{lang_name} {request.cefr_level} — {request.duration_weeks}-week programme",
        cefr_level=request.cefr_level,
        duration_weeks=request.duration_weeks,
        days_per_week=request.days_per_week,
        ends_with_test=True,
        weekly_plan=weekly_plan,
    )
