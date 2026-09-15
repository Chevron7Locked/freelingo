---
description: "Current study-plan grid, language ownership, lazy lesson generation, day progression, skip/pending behavior, lesson lifecycle, progress, and frontend integration."
applyTo: "backend/app/{data/curriculum.py,models/{study_plan,lesson}.py,schemas/{study_plan,assessment,lessons}.py,routers/{study_plan,lessons,assessment}.py,services/{study_plan_generator,lesson_generator,progress_service}.py}, frontend/src/app/(app)/{dashboard,plan,lesson}/**, frontend/src/components/{plan,assessment}/**"
---

# Study Plan and Lessons

## Core model

Each target-language track can have one active `StudyPlan`. The owning `UserLanguage` and persisted
`StudyPlan.target_language` determine isolation; switching the active client language does not reassign
existing resources.

Assessment completion normally creates the plan. `POST /api/study-plan/generate` remains available as
an explicit plan-generation endpoint.

The plan stores a deterministic week/day grid in `generated_plan`; lesson content is generated lazily
and persisted when the current day is requested.

## Plan fields and constraints

Important persisted fields are:

- `user_id` and required `user_language_id` ownership.
- `cefr_level` and denormalized `target_language`.
- goals, `duration_weeks`, and `days_per_week`.
- `progress_day`, the zero-based count of completed/passed plan days.
- `generated_plan`, the complete scheduled grid.
- `is_active` and completion-test result fields.
- `current_unit`, initialized to the first curriculum unit.

The partial unique index on `user_language_id` where `is_active=true` enforces one active plan per
language track.

The UI offers duration presets 4, 8, 12, and 16 weeks and derives 5, 5, 4, and 3 days per week. Current
request schemas accept integers and do not enforce those closed sets.

`current_unit` is not advanced by current production flows and normally remains the first unit for the
life of a plan. Frontend uses it for active-unit presentation, so it must not be treated as reliable
progress-derived state elsewhere.

## Deterministic generation

`study_plan_generator.py` loads the selected language/level curriculum and traverses each unit's
declared `lesson_types`. It distributes one lesson slot per plan day and reserves the final slot for
the end-of-level test. It does not call an LLM.

Each slot contains week/day, type, localized title/objectives, estimated duration, unit ID, grammar
slugs, and vocabulary-set IDs. The generated grid is the source of scheduled future slots.

## `progress_day`

`progress_day=N` means N plan days have been completed or passed and the current absolute day index is
N. With `days_per_week`:

```text
week = (progress_day // days_per_week) + 1
day  = (progress_day % days_per_week) + 1
total_days = duration_weeks * days_per_week
```

The plan is exhausted when `progress_day >= total_days`.

## Lazy lesson generation

`GET /api/study-plan/today` is the central learning-loop endpoint:

1. Resolve the authenticated user's active-language plan or return 404.
2. Load existing lessons and index them by plan day.
3. Advance across consecutive current days that already contain generated lessons and whose lessons
   are all complete.
4. Persist `progress_day` if it advanced.
5. Count generated incomplete lessons from passed days as pending.
6. Return no current lessons when the plan is exhausted.
7. Locate the current scheduled slot and reuse a lesson with the same stable identity when present.
8. Otherwise generate lesson content through the LLM and persist the lesson and exercises.
9. On uniqueness race, roll back and load the row created by the competing request.
10. On other generation failure, log and exclude that slot from the response.

The response contains `plan_id`, `cefr_level`, `progress_day`, `total_days`, `pending_count`, and
`lessons`. Every returned lesson contains ID, schedule metadata, objectives, unit ID, and
`is_completed`.

A generation failure does not return a placeholder lesson with `id: null`. If every current slot fails,
`lessons` is empty and a later `/today` call can retry generation.

Auto-advance never crosses a day with no persisted lessons. An unvisited day must be generated and
completed or explicitly skipped.

## Skip and pending lessons

`POST /api/study-plan/skip-day` increments `progress_day` by one up to `total_days`. It does not create,
complete, or delete lessons.

`GET /api/study-plan/pending-lessons` returns generated, incomplete lessons whose absolute schedule day
is before `progress_day`. Ungenerated slots cannot be pending because no lesson row exists.

`GET /api/study-plan/lessons` returns lightweight metadata for every persisted lesson in the active
plan. It does not generate content, auto-advance, or mutate completion.

## Lesson persistence

A lesson row is unique by `(study_plan_id, week_number, day_number, title)`. Exercises belong to the
lesson and persist question, options, correct/user answer, score, feedback, explanation, optional
free-write corrections, and answer timestamp.

Generated lesson content stores richer structures including target-language explanation, optional
native explanation, exercise native explanation/hint, vocabulary support, grammar references, and unit
metadata.

Existing lesson JSON with only the older core vocabulary/explanation fields remains readable. Runtime
response mapping must tolerate absent optional native-support and enriched-vocabulary fields.

## Lesson lifecycle

1. `/today` materializes the current slot as lesson and exercise rows.
2. `GET /api/lessons/{id}` verifies ownership and returns detail.
3. The frontend submits answers through `/api/lessons/exercises/{id}/answer`.
4. Optional native explanation/hint and invalid-exercise regeneration endpoints update generated JSON
   and, for regeneration, the existing exercise row.
5. `POST /api/lessons/{id}/complete` locks the lesson and atomically persists first completion,
   progress, XP, and competencies.
6. A later `/today` call advances the completed day.

`POST /api/lessons/{id}/start` exists but only verifies ownership and returns the lesson; the current
lesson page does not call it and no in-progress state is persisted.

Completion retries are idempotent. An already-completed row is returned before freemium quota checks
and does not change timestamps, progress, XP, competencies, or quota. Freemium usage is recorded
best-effort after the database transaction succeeds.

## Exercise evaluation

Multiple-choice answers are deterministic. Fill-blank can use normalized/LLM evaluation.
Free-write and pronunciation use LLM-backed evaluation with unavailable fallbacks defined by the
Lesson/API contracts. Usable free-write corrections persist for reload and review.

One unanswered technically invalid exercise can be regenerated in place. Regeneration must preserve
exercise type and synchronize the relational row with `lesson.content.exercises`.

## Progress and competencies

Lesson completion applies daily XP and skill progress to the lesson's owning plan. Unit competencies
use the mean answered-exercise score, or the established fallback when none is available, and update by
EMA. Detailed XP and mastery rules live in `learning-resources.instructions.md`.

## Frontend integration

Dashboard loads progress and `/today` in parallel. It chooses the first returned incomplete lesson as
the next action, exposes generated pending lessons, and offers skip when current lessons exist.

Current Dashboard code treats any non-success `/today` response as `hasPlan=false`, not only 404. This
can show Assessment after a server error and must not be interpreted as a backend absence guarantee.

My Plan combines scheduled slots from `generated_plan` with persisted `/lessons`, `/today`, and
`/pending-lessons` metadata. Current generated lessons can start, passed incomplete lessons can resume,
completed lessons open read-only review, and ungenerated future slots have no action.

The lesson page disables answer/regeneration/completion controls for completed lessons. After first
completion it refreshes `/today`, can show day-complete state, refreshes freemium status, and may trigger
the review prompt when the next returned lesson belongs to another unit or the plan is exhausted.

## Related specifications

- `multi-language.instructions.md`: active language and plan isolation.
- `learning-resources.instructions.md`: competencies, assessment, and level test.
- `database-models.instructions.md`: exact persisted fields and constraints.
- `api-endpoints.instructions.md`: endpoint contracts and errors.
- `prompts.instructions.md`: lesson generation/evaluation prompt composition.
