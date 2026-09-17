"""Distribution contract for ``distribute_units`` (issue #316).

The allocator spreads every curriculum unit fairly across the plan grid, fills
each unit's quota by cycling that unit's own ``lesson_types`` in order, and
reserves the final coordinate for the level completion test. Truncation is
positional: when a quota is not a multiple of the unit's type count, the final
cycle is cut off at the tail, so the types after the cut receive no slot in that
unit. A unit that is a full cycle short simply has no lesson of that type —
that is the documented policy, not a defect (see the study-plan specification).

Plans too short to give every unit a slot are rejected before any state changes
(``assert_plan_capacity``); the tests for that live here too.
"""

from __future__ import annotations

from collections import Counter

import pytest
from sqlalchemy import select

from app.data._types import CurriculumUnit
from app.data.curriculum import CEFR_LEVELS, distribute_units, get_curriculum_units
from app.models.user_language import UserLanguage
from app.services.study_plan_generator import PlanCapacityError, assert_plan_capacity

LANGUAGES = (
    "en-GB",
    "en-US",
    "de-DE",
    "es-ES",
    "fr-FR",
    "it-IT",
    "pt-PT",
    "ja-JP",
    "ko-KR",
    "zh-CN",
)
SHAPES = ((4, 5), (8, 5), (12, 4), (16, 3))
MATRIX = tuple(
    (language, level, weeks, days)
    for language in LANGUAGES
    for level in CEFR_LEVELS
    for weeks, days in SHAPES
)
COMPLETION_UNIT_ID = "completion-test"


# ── helpers ───────────────────────────────────────────────────────────────────


def _slots(language: str, level: str, weeks: int, days: int) -> tuple[list, list]:
    units = get_curriculum_units(level, language)
    slots = distribute_units(units, total_weeks=weeks, days_per_week=days, target_language=language)
    return units, slots


def _unit_counts(slots: list[dict]) -> Counter:
    return Counter(s["unit_id"] for s in slots if s["unit_id"] != COMPLETION_UNIT_ID)


def _types_for(slots: list[dict], unit_id: str) -> list[str]:
    return [s["lesson_type"] for s in slots if s["unit_id"] == unit_id]


#: Pinned teaching-slot counts per plan shape, for the eight units every shipped curriculum has.
#: Hand-derived data, not a copy of the allocator's own formula, so a change to the allocation
#: rule fails here instead of agreeing with itself.
EXPECTED_COUNTS = {
    (4, 5): [3, 3, 3, 2, 2, 2, 2, 2],  # 20 slots − 1 completion test = 19 over 8 units
    (8, 5): [5, 5, 5, 5, 5, 5, 5, 4],  # 39
    (12, 4): [6, 6, 6, 6, 6, 6, 6, 5],  # 47
    (16, 3): [6, 6, 6, 6, 6, 6, 6, 5],  # 47
}


def _unit(n: int, lesson_types: list[str]) -> CurriculumUnit:
    return CurriculumUnit(
        id=f"u{n}",
        level="B1",
        unit_number=n,
        title=f"Unit {n}",
        grammar_points=[f"g{n}-1", f"g{n}-2"],
        vocabulary_set_ids=[f"v{n}"],
        lesson_types=list(lesson_types),
        competency_checklist=[f"c{n}-1", f"c{n}-2"],
        default_weeks=2,
    )


# ── T1: the full matrix ───────────────────────────────────────────────────────


@pytest.mark.parametrize(("language", "level", "weeks", "days"), MATRIX)
def test_matrix_invariants(language: str, level: str, weeks: int, days: int) -> None:
    """Every language × level × shape keeps the whole allocation contract."""
    units, slots = _slots(language, level, weeks, days)
    assert len(units) == 8, f"{language} {level} ships {len(units)} units, expected 8"
    unit_ids = [u.id for u in units]

    # Grid: exactly weeks × days slots, each coordinate once, in reading order.
    assert len(slots) == weeks * days
    coords = [(s["week"], s["day"]) for s in slots]
    assert coords == [(i // days + 1, i % days + 1) for i in range(weeks * days)]
    assert len(set(coords)) == len(coords)

    # Completion test: exactly one, at the final coordinate, owned by no unit.
    assert slots[-1]["unit_id"] == COMPLETION_UNIT_ID
    assert sum(1 for s in slots if s["unit_id"] == COMPLETION_UNIT_ID) == 1

    # Every unit is scheduled, quotas are fair, and remainder goes to the earliest.
    counts = _unit_counts(slots)
    assert list(counts) == unit_ids, "units must appear in curriculum order"
    assert set(counts) == set(unit_ids), "every curriculum unit must be scheduled"
    expected = EXPECTED_COUNTS[(weeks, days)]
    assert [counts[uid] for uid in unit_ids] == expected
    assert max(counts.values()) - min(counts.values()) <= 1

    # Unit blocks are contiguous (no interleaving between units).
    blocks: list[list] = []
    for uid in [s["unit_id"] for s in slots[:-1]]:
        if not blocks or blocks[-1][0] != uid:
            blocks.append([uid, 0])
        blocks[-1][1] += 1
    assert [b[0] for b in blocks] == unit_ids
    assert [b[1] for b in blocks] == expected

    # Each unit schedules only its own types, as a prefix of its own cycle.
    for unit in units:
        types = _types_for(slots, unit.id)
        cycle = unit.lesson_types or ["grammar"]
        assert types == [cycle[i % len(cycle)] for i in range(len(types))]
        assert set(types) <= set(cycle)

    # Deterministic: identical inputs produce identical output.
    again = distribute_units(units, total_weeks=weeks, days_per_week=days, target_language=language)
    assert again == slots


# ── T2: the documented deficit/truncation policy ──────────────────────────────


def test_deficit_policy_es_b1_4x5() -> None:
    """maintainer example: 20 slots cannot carry every type of 8 units."""
    units, slots = _slots("es-ES", "B1", 4, 5)
    counts = _unit_counts(slots)
    assert [counts[u.id] for u in units] == [3, 3, 3, 2, 2, 2, 2, 2]

    unit_owned_types = [s["lesson_type"] for s in slots if s["unit_id"] != COMPLETION_UNIT_ID]
    assert "review" not in unit_owned_types
    assert "writing" not in unit_owned_types

    # The single `review` lesson in the plan is the completion test itself, and it
    # sits at the final coordinate — so this assertion cannot be met by dropping it.
    reviews = [s for s in slots if s["lesson_type"] == "review"]
    assert len(reviews) == 1
    assert reviews[0]["unit_id"] == COMPLETION_UNIT_ID
    assert (reviews[0]["week"], reviews[0]["day"]) == (4, 5)


def test_deficit_policy_zh_cn_b2_12x4() -> None:
    """maintainer example: the 7-type zh cycles cannot fit 6 slots."""
    units, slots = _slots("zh-CN", "B2", 12, 4)
    counts = _unit_counts(slots)
    assert [counts[u.id] for u in units] == [6, 6, 6, 6, 6, 6, 6, 5]

    for unit in units:
        assert "review" not in _types_for(slots, unit.id)

    last = units[-1]
    assert "writing" not in _types_for(slots, last.id)


def test_unit_whose_quota_covers_its_cycle_schedules_every_type() -> None:
    units, slots = _slots("de-DE", "A2", 12, 4)
    counts = _unit_counts(slots)
    assert [counts[u.id] for u in units] == [6, 6, 6, 6, 6, 6, 6, 5]

    covered: list[str] = []
    truncated: list[str] = []
    for unit in units:
        types = _types_for(slots, unit.id)
        cycle = unit.lesson_types or ["grammar"]
        if len(types) >= len(cycle):
            # A unit whose quota reaches its type count must schedule every type it declares.
            assert set(cycle) <= set(types), f"{unit.id} did not schedule part of its own cycle"
            covered.append(unit.id)
        else:
            truncated.append(unit.id)

    # Without this the assertion above is vacuous whenever truncation bites everywhere: some
    # unit must actually reach its full cycle, and truncation must hit a suffix of the units
    # (the smallest quotas), never a unit in the middle of the schedule.
    assert covered, "no unit reached its full cycle, so the equality check proved nothing"
    assert [u.id for u in units][len(units) - len(truncated) :] == truncated


# ── T3: heterogeneous curricula (the old global type_index defect) ────────────


@pytest.mark.parametrize(
    ("language", "type_counts"),
    [("en-GB", [4, 4, 6, 4, 5, 5, 5, 5]), ("en-US", [3, 3, 5, 3, 4, 4, 5, 5])],
)
def test_heterogeneous_curricula_keep_each_units_own_cycle(
    language: str, type_counts: list[int]
) -> None:
    units, slots = _slots(language, "C2", 12, 4)
    assert [len(u.lesson_types) for u in units] == type_counts

    counts = _unit_counts(slots)
    assert [counts[u.id] for u in units] == [6, 6, 6, 6, 6, 6, 6, 5]

    for unit in units:
        types = _types_for(slots, unit.id)
        cycle = unit.lesson_types or ["grammar"]
        # A unit must never inherit a type it does not declare (old behaviour:
        # one global counter walked the concatenated type list of all units).
        assert set(types) <= set(cycle), f"{unit.id} scheduled a type it does not declare"
        assert types == [cycle[i % len(cycle)] for i in range(len(types))]


# ── T4: balanced coverage where capacity allows ───────────────────────────────


@pytest.mark.parametrize("level", ["B2", "C1", "C2"])
@pytest.mark.parametrize(("weeks", "days"), [(12, 4), (16, 3)])
def test_zh_curricula_balanced_coverage(level: str, weeks: int, days: int) -> None:
    """47 teaching slots over 8 seven-type units: floor of 5, never 6 for all."""
    units, slots = _slots("zh-CN", level, weeks, days)
    counts = _unit_counts(slots)
    assert [counts[u.id] for u in units] == [6, 6, 6, 6, 6, 6, 6, 5]
    assert min(counts.values()) == 5

    # The shared opening types reach every unit; the tail is what gets truncated.
    for unit in units:
        assert {"grammar", "vocabulary"} <= set(_types_for(slots, unit.id))

    last_cycle = units[-1].lesson_types
    assert set(last_cycle) - set(_types_for(slots, units[-1].id)) == {"writing", "review"}
    for unit in units[:-1]:
        assert set(unit.lesson_types) - set(_types_for(slots, unit.id)) == {"review"}


# ── T5: validation ────────────────────────────────────────────────────────────


def test_assert_plan_capacity_boundaries() -> None:
    units = get_curriculum_units("A1", "de-DE")
    assert_plan_capacity(units, 3, 3)  # 9 slots − 1 test = 8 teaching slots = 8 units

    for weeks, days in [(2, 4), (1, 1), (1, 4)]:
        with pytest.raises(PlanCapacityError) as exc:
            assert_plan_capacity(units, weeks, days)
        assert f"{len(units)} curriculum units" in str(exc.value)
        assert "teaching" in str(exc.value)

    for weeks, days in [(0, 4), (12, 0), (-1, 4), (12, -1)]:
        with pytest.raises(PlanCapacityError):
            assert_plan_capacity(units, weeks, days)

    # A level that resolves to no curriculum units cannot be taught at all.
    with pytest.raises(PlanCapacityError) as exc:
        assert_plan_capacity([], 12, 4)
    assert "no curriculum units" in str(exc.value)


def test_capacity_message_names_a_workable_example() -> None:
    units = get_curriculum_units("A1", "de-DE")
    with pytest.raises(PlanCapacityError) as exc:
        assert_plan_capacity(units, 2, 4)
    # 2 weeks × 4 days leaves 7 teaching slots; 3 weeks × 4 days would fit.
    assert "3 weeks × 4 days" in str(exc.value)


def test_distribute_units_without_units_returns_no_slots() -> None:
    assert distribute_units([], total_weeks=12, days_per_week=4, target_language="en-GB") == []


@pytest.mark.parametrize(
    "payload",
    [
        {"cefr_level": "A1", "duration_weeks": 0, "days_per_week": 4},
        {"cefr_level": "A1", "duration_weeks": -1, "days_per_week": 4},
        {"cefr_level": "A1", "duration_weeks": 12, "days_per_week": 0},
        {"cefr_level": "A1", "duration_weeks": 12, "days_per_week": -1},
        {"cefr_level": "Z9", "duration_weeks": 12, "days_per_week": 4},
        {"cefr_level": "", "duration_weeks": 12, "days_per_week": 4},
    ],
)
async def test_generate_rejects_invalid_requests(client, test_user, payload: dict) -> None:
    _user, headers = test_user
    resp = await client.post("/api/study-plan/generate", json=payload, headers=headers)
    assert resp.status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {"cefr_level": "A1", "duration_weeks": 0, "days_per_week": 4},
        {"cefr_level": "A1", "duration_weeks": 12, "days_per_week": -1},
        {"cefr_level": "Z9", "duration_weeks": 12, "days_per_week": 4},
    ],
)
async def test_assessment_complete_rejects_invalid_requests(
    client, test_user, payload: dict
) -> None:
    _user, headers = test_user
    resp = await client.post("/api/assessment/complete", json=payload, headers=headers)
    assert resp.status_code == 422


@pytest.mark.parametrize("endpoint", ["/api/study-plan/generate", "/api/assessment/complete"])
async def test_both_entry_points_reject_undersized_plans(client, test_user, endpoint: str) -> None:
    """2 × 4 leaves 7 teaching slots for 8 units: a client error, not a plan."""
    _user, headers = test_user
    resp = await client.post(
        endpoint,
        json={
            "cefr_level": "A1",
            "duration_weeks": 2,
            "days_per_week": 4,
            "target_language": "en-US",
        },
        headers=headers,
    )
    assert resp.status_code == 400
    assert "curriculum units" in resp.json()["detail"]


async def test_rejected_request_does_not_deactivate_the_existing_plan(
    client, test_user_with_plan, db_session
) -> None:
    user, headers = test_user_with_plan
    resp = await client.post(
        "/api/study-plan/generate",
        json={
            "cefr_level": "A1",
            "duration_weeks": 2,
            "days_per_week": 4,
            "target_language": "en-US",
        },
        headers=headers,
    )
    assert resp.status_code == 400

    from app.models.study_plan import StudyPlan

    plans = (
        (
            await db_session.execute(
                select(StudyPlan).where(StudyPlan.user_id == user.id, StudyPlan.is_active.is_(True))
            )
        )
        .scalars()
        .all()
    )
    assert len(plans) == 1, "a rejected request must leave the active plan untouched"


async def test_rejected_request_creates_no_user_language_row(client, test_user, db_session) -> None:
    """The capacity check runs before ensure_user_language, which flushes a row."""
    user, headers = test_user
    resp = await client.post(
        "/api/study-plan/generate",
        json={
            "cefr_level": "A1",
            "duration_weeks": 2,
            "days_per_week": 4,
            "target_language": "de-DE",
        },
        headers=headers,
    )
    assert resp.status_code == 400

    rows = (
        (
            await db_session.execute(
                select(UserLanguage).where(
                    UserLanguage.user_id == user.id,
                    UserLanguage.target_language == "de-DE",
                )
            )
        )
        .scalars()
        .all()
    )
    assert rows == []


async def test_boundary_plan_covers_every_unit_exactly_once(client, test_user) -> None:
    """9 slots − 1 completion test = 8 teaching slots: the exact floor."""
    _user, headers = test_user
    resp = await client.post(
        "/api/study-plan/generate",
        json={
            "cefr_level": "A1",
            "duration_weeks": 3,
            "days_per_week": 3,
            "target_language": "en-US",
        },
        headers=headers,
    )
    assert resp.status_code == 200

    plan = resp.json()["generated_plan"]
    days = [day for week in plan["weekly_plan"] for day in week["days"]]
    assert len(days) == 9
    assert days[-1]["unit_id"] == COMPLETION_UNIT_ID
    unit_ids = [d["unit_id"] for d in days[:-1]]
    assert len(set(unit_ids)) == len(unit_ids) == 8


# ── T6: the full-unit generation context is preserved ─────────────────────────


async def test_today_passes_complete_unit_context_to_generate_lesson(
    client, test_user, db_session, monkeypatch
) -> None:
    """Lesson generation keeps receiving the unit's complete grammar/vocabulary."""
    user, headers = test_user
    created = await client.post(
        "/api/study-plan/generate",
        json={
            "cefr_level": "A1",
            "duration_weeks": 12,
            "days_per_week": 4,
            "target_language": "de-DE",
        },
        headers=headers,
    )
    assert created.status_code == 200

    rows = (
        (await db_session.execute(select(UserLanguage).where(UserLanguage.user_id == user.id)))
        .scalars()
        .all()
    )
    for row in rows:
        row.is_active = row.target_language == "de-DE"
    await db_session.commit()

    captured: dict = {}

    class _FakeContent:
        def model_dump(self) -> dict:
            return {
                "explanation": "x",
                "exercises": [
                    {
                        "type": "multiple_choice",
                        "question": "q",
                        "options": ["a", "b"],
                        "correct": "a",
                        "explanation": "e",
                    }
                ],
                "vocabulary": [],
            }

    async def _fake_generate_lesson(**kwargs):
        captured.update(kwargs)
        return _FakeContent()

    monkeypatch.setattr("app.routers.study_plan.generate_lesson", _fake_generate_lesson)

    resp = await client.get("/api/study-plan/today", headers=headers)
    assert resp.status_code == 200

    units = get_curriculum_units("A1", "de-DE")
    unit = next(u for u in units if u.id == captured["unit_id"])
    assert captured["grammar_points"] == unit.grammar_points
    assert captured["vocabulary_set_ids"] == unit.vocabulary_set_ids


# ── T7: slot titles stay a safe join key ──────────────────────────────────────


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("level", CEFR_LEVELS)
def test_slot_titles_are_unique_within_a_plan(language: str, level: str) -> None:
    _units, slots = _slots(language, level, 12, 4)
    titles = [s["title"] for s in slots]
    assert len(set(titles)) == len(titles), "lesson titles are the join key for /today lookups"


# ── T8: the reported #316 shapes cannot recur ─────────────────────────────────


def test_reported_de_a2_shape_cannot_recur() -> None:
    """12×4 German A2 shipped as 15/5/5/5/5/5/5/2; the tail unit lost its lessons."""
    units, slots = _slots("de-DE", "A2", 12, 4)
    counts = _unit_counts(slots)
    assert counts.most_common(1)[0][1] <= 7
    assert min(counts.values()) >= 4
    assert [counts[u.id] for u in units] != [15, 5, 5, 5, 5, 5, 5, 2]


# ── T9: purity ────────────────────────────────────────────────────────────────


def test_distribute_units_does_not_mutate_its_input() -> None:
    units = [_unit(i, ["grammar", "vocabulary", "review"]) for i in range(1, 4)]
    snapshot = [
        (u.id, list(u.lesson_types), list(u.grammar_points), list(u.vocabulary_set_ids))
        for u in units
    ]

    distribute_units(units, total_weeks=4, days_per_week=5, target_language="en-GB")

    assert [
        (u.id, list(u.lesson_types), list(u.grammar_points), list(u.vocabulary_set_ids))
        for u in units
    ] == snapshot


def test_synthetic_small_plan_matches_the_quota_rule() -> None:
    units = [_unit(i, ["grammar", "vocabulary"]) for i in range(1, 4)]
    slots = distribute_units(units, total_weeks=1, days_per_week=4, target_language="en-GB")
    # 4 slots − 1 completion test = 3 teaching slots over 3 units: one each.
    assert _unit_counts(slots) == Counter({"u1": 1, "u2": 1, "u3": 1})
