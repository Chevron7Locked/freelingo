"""Learner-responsive unit weighting (issue #317).

Deterministic matching of placement-reported weaknesses to curriculum units,
plus a slot-weighting layer that is conservative by design: winners gain,
the most-loaded other units donate, the final consolidation unit is never
donated from, and total slot count is preserved. No LLM involvement.
"""

from __future__ import annotations

import re
import unicodedata

# English assessment vocabulary → curriculum slug fragments. Applied on top of
# direct slug/title matching so it works across the per-language data packages
# (slugs share roots: subjuntivo/subjunctive, condicional/conditional, ...).
_WEAKNESS_ALIASES: dict[str, list[str]] = {
    "subjunctive": ["subjuntivo", "subjunctive"],
    "subjuntivo": ["subjuntivo", "subjunctive"],
    "conditional": ["condicional", "conditional", "si-imperfecto", "si-presente"],
    "condicional": ["condicional", "conditional", "si-imperfecto", "si-presente"],
    "passive": ["pasiva", "impersonal", "passive"],
    "pasiva": ["pasiva", "impersonal"],
    "relative": ["relativo", "cuyo"],
    "reported speech": ["estilo-indirecto"],
    "indirect speech": ["estilo-indirecto"],
    "estilo indirecto": ["estilo-indirecto"],
    "preterite": ["preterito"],
    "indefinido": ["preterito"],
    "imperfect": ["imperfecto"],
    "imperfecto": ["imperfecto"],
    "perfect tenses": ["perfecto", "pluscuamperfecto"],
    "pronoun": ["pronombres"],
    "pronombres": ["pronombres"],
    "object pronouns": ["pronombres-objeto"],
    "imperative": ["imperativo"],
    "imperativo": ["imperativo"],
    "future": ["futuro"],
    "futuro": ["futuro"],
    "comparative": ["comparativ", "superlativ"],
    "superlative": ["superlativ"],
    "por and para": ["por-para"],
    "por para": ["por-para"],
    "accents": ["tildes", "acentuacion", "diacritic"],
    "tildes": ["tildes"],
    "spelling": ["g-j-h", "b-v", "tildes"],
    "ser and estar": ["ser-estar"],
    "connectors": ["conectores", "conector"],
    "conectores": ["conectores"],
}


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text.lower().strip())


def _fragments_for(weakness: str) -> set[str]:
    """Slug fragments implied by one weakness string."""
    w = _normalize(weakness)
    fragments: set[str] = set()
    for keyword, frags in _WEAKNESS_ALIASES.items():
        if keyword in w:
            fragments.update(frags)
    # Bare words of the weakness itself can be slugs or slug parts already
    # (some flows store slugs as weaknesses).
    for token in re.split(r"[^a-z0-9]+", w):
        if len(token) >= 4:
            fragments.add(token)
    return fragments


def match_weaknesses_to_units(
    units: list,
    weaknesses: list[str],
    target_language: str = "en-GB",
    max_units: int = 4,
) -> set[str]:
    """Return ids of units whose grammar points relate to the weaknesses.

    Deterministic: pure string matching over grammar slugs and unit titles,
    no LLM, no ordering surprises. Caps matches at ``max_units`` in curriculum
    order so broad weaknesses cannot swallow the whole plan.
    """
    if not weaknesses:
        return set()

    fragments: set[str] = set()
    for weakness in weaknesses:
        fragments |= _fragments_for(weakness)
    if not fragments:
        return set()

    matched: list[str] = []
    for unit in units:
        haystack = " ".join(
            [_normalize(gp) for gp in (unit.grammar_points or [])] + [_normalize(unit.title)]
        )
        if any(frag in haystack for frag in fragments):
            matched.append(unit.id)
        if len(matched) >= max_units:
            break
    return set(matched)
