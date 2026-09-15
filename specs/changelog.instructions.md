---
description: "Rules for current unreleased work and immutable published history in CHANGELOG.md."
applyTo: "CHANGELOG.md"
---

# Changelog Guidelines

## Purpose

`CHANGELOG.md` is the only release-history document. Specifications describe current behavior and must
not duplicate release narratives, phases, migration chronology, or validation logs.

The changelog follows Keep a Changelog categories and Semantic Versioning.

## Headers

The repository currently assigns the next version before release:

```markdown
## [X.Y.Z] - Unreleased
```

At publication, replace `Unreleased` with an ISO 8601 date:

```markdown
## [X.Y.Z] - YYYY-MM-DD
```

Only one top unreleased section should exist. Published versions follow in descending order.

## Sections

Use these headings in order and omit empty sections:

- `Added`
- `Changed`
- `Deprecated`
- `Removed`
- `Fixed`
- `Security`

## Entry style

- Use one concise bullet per independently meaningful change.
- Describe observable behavior, contract, migration, infrastructure, or contributor workflow.
- Name the affected feature or interface when useful.
- Do not include authors, commit hashes, PR numbers, validation results, test counts, coverage, or
  session notes.
- Group by change category, not implementation file or development phase.
- Document the corrected behavior, not the fact that a test was added.

## What belongs

Record user-visible features/fixes, endpoint/schema changes, migrations, security fixes, provider or
configuration changes, infrastructure changes, and contributor-facing workflow changes.

Do not record internal refactors, formatting, routine tests, or documentation-only synchronization
unless they materially change how users or contributors interact with the project.

## Immutability

The current unreleased section may be reorganized, corrected, or split before publication. Do not
delete or rewrite entries under published versions except for an explicitly approved factual
correction. Preserve historical references to files, phases, or architecture names that were accurate
for that release even if those items no longer exist.
