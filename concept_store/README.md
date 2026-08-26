# concept_store

`concepts.json` is architectural memory that survives across tasks: what a
module is for, its contracts and invariants, discovered once and then reused
instead of re-derived from scratch by the next task that touches it. It grows
the same way loop memory does — a task either updates a concept that turned
out wrong, or writes one for a module nobody had understood well enough to
describe yet.

This is architecture **per module**: one entry per file, kept in a 1:1
mapping with the file tree and enforced by `tests/test_concepts.py`. That is
what distinguishes it from `models/`, which captures structural claims and
requirements about the system as a whole, and from `ontology/`, which
captures vocabulary shared across many modules at once.

## Shape

Each entry in `concepts.concepts` is keyed by a concept name and carries:

- `module` — the file this concept describes (the anchor the coverage test
  checks against)
- `description` — what the module is for, in prose
- `contracts` — specific behavioral guarantees callers can rely on
- `invariants` — things that must stay true, often with the reasoning for why
- `evidence` — file/test references backing the claims above
- `related` — names of other concepts this one connects to
- `confidence`, `created_at`, `last_validated` — how much to trust the entry
  and how stale it might be

## Using it

- Read via `mcp__taskfw__concept__get` / `concept__search` /
  `concept__list` / `concept__modules` before touching a module you haven't
  worked in recently — it's cheaper than re-deriving its contracts from the
  source.
- Write via `concept__upsert` when a task changes a module's behavior or
  reveals something about it that wasn't captured yet. Use `concept__delete`
  when a concept no longer applies.
- `concept__uncovered` finds modules with no concept yet — the gap this file
  is meant to close over time.
- The `extract-concepts` and `update-concept-store` skills seed and refresh
  this file directly; prefer them over hand-editing the JSON.

See the "Three things worth knowing exist" section of the top-level
`CLAUDE.md` for how this relates to `models/` and `ontology/`.
