"""Learner-responsive unit weighting (issue #317).

The placement assessment reports weaknesses; the plan generator must consume
them. Deterministic only: alias matching maps weakness text to curriculum
grammar-point slugs, matched units receive extra quota, total slot count is
preserved, and unmatched input degrades to the exact fair-quota plan.
"""

from __future__ import annotations

from collections import Counter

from app.data.curriculum import distribute_units, get_curriculum_units
from app.services.weakness_matching import match_weaknesses_to_units


def test_subjunctive_weakness_boosts_subjuntivo_units():
    units = get_curriculum_units("B1", "es-ES")
    matched = match_weaknesses_to_units(
        units,
        ["Subjunctive mood (present and imperfect)", "Subjunctive in relative clauses"],
        target_language="es-ES",
    )
    assert "b1-unit-1" in matched
    assert "b1-unit-2" in matched


def test_conditional_weakness_matches_condicional_unit():
    units = get_curriculum_units("B1", "es-ES")
    matched = match_weaknesses_to_units(
        units,
        ["Conditional sentences (Type 2 and 3)"],
        target_language="es-ES",
    )
    assert "b1-unit-6" in matched


def test_unknown_weakness_matches_nothing():
    units = get_curriculum_units("B1", "es-ES")
    matched = match_weaknesses_to_units(
        units,
        ["Quantum chromodynamics"],
        target_language="es-ES",
    )
    assert matched == set()


def test_weighted_plan_conserves_total_slots():
    units = get_curriculum_units("B1", "es-ES")
    base = distribute_units(units, 12, 4, "es-ES")
    weighted = distribute_units(
        units, 12, 4, "es-ES", unit_weights={"b1-unit-1": 2, "b1-unit-6": 2}
    )
    assert len(base) == len(weighted)
    def lesson(slots):
        return [s for s in slots if s["unit_id"] != "completion-test"]

    assert len(base) == len(weighted)
    # completion test still last
    assert weighted[-1]["unit_id"] == "completion-test"


def test_weighted_units_gain_and_others_lose_symmetrically():
    units = get_curriculum_units("B1", "es-ES")
    slots = distribute_units(units, 12, 4, "es-ES", unit_weights={"b1-unit-1": 2, "b1-unit-6": 2})
    counts = Counter(s["unit_id"] for s in slots if s["unit_id"] != "completion-test")
    assert counts["b1-unit-1"] == counts["b1-unit-6"] == 8  # 6 + 2
    # donors lost exactly what winners gained
    total_bonus = 4
    assert sum(counts.values()) == 12 * 4 - 1
    # donors are the most-loaded units first: the four 6-slot non-winner
    # units pay one each; the 5-slot tail unit is untouched
    assert (
        counts["b1-unit-2"]
        == counts["b1-unit-3"]
        == counts["b1-unit-4"]
        == counts["b1-unit-5"]
        == 5
    )
    assert counts["b1-unit-7"] == 6
    assert counts["b1-unit-8"] == 5
    assert total_bonus == 2 * 2


def test_weighting_respects_per_unit_cycle_floor():
    units = get_curriculum_units("B1", "es-ES")
    slots = distribute_units(
        units,
        12,
        4,
        "es-ES",
        unit_weights={u.id: 50 for u in units},  # absurd weights
    )
    counts = Counter(s["unit_id"] for s in slots if s["unit_id"] != "completion-test")
    # no unit may be starved below one full type cycle by someone else's weight
    for u in units:
        assert counts[u.id] >= min(len(u.lesson_types), counts.most_common(1)[0][1] // 2)


def test_no_weights_equals_current_behavior():
    units = get_curriculum_units("B1", "es-ES")
    plain = distribute_units(units, 12, 4, "es-ES")
    explicit = distribute_units(units, 12, 4, "es-ES", unit_weights=None)
    assert plain == explicit


def test_end_to_end_weaknesses_change_the_plan():
    units = get_curriculum_units("B1", "es-ES")
    matched = match_weaknesses_to_units(
        units,
        ["Subjunctive mood (present and imperfect)", "Conditional sentences (Type 2 and 3)"],
        target_language="es-ES",
    )
    weights = {uid: 2 for uid in matched}
    plain = Counter(
        s["unit_id"]
        for s in distribute_units(units, 12, 4, "es-ES")
        if s["unit_id"] != "completion-test"
    )
    boosted = Counter(
        s["unit_id"]
        for s in distribute_units(units, 12, 4, "es-ES", unit_weights=weights)
        if s["unit_id"] != "completion-test"
    )
    assert boosted["b1-unit-1"] > plain["b1-unit-1"]
    assert boosted["b1-unit-6"] > plain["b1-unit-6"]
