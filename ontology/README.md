# Ontology

This directory holds `task-domain.json`, the domain's ubiquitous language: the
nouns of task-framework (Task, Grooming, MemoryRecord, TaskContext, ...) and
the explicit, typed relations between them.

It exists to answer one question: **what is this thing, and what is it to the
others?** Not how it's implemented — what it *is*, in the vocabulary anyone
working on this project should share. That question is worth answering
separately from the code because the terms cut across files (one term can
span several modules) and across the project's other memory stores, which
answer different questions:

- `concept_store/concepts.json` answers "what does this *module* promise" —
  architecture per file.
- `ontology/task-domain.json` answers "what is this *term*, and how does it
  relate to the rest" — vocabulary per domain concept.

## Shape

- **`bounded_contexts`** — the few distinct sub-domains the terms fall into
  (e.g. Task Tracking vs. Loop Memory), each with a one-line description of
  what separates it from the others.
- **`terms`** — one entry per domain noun: which bounded context it belongs
  to, its `form` (see below), and a plain-language definition of what it is.
  The `form` value already carries whether the term is its own record or just
  a value on / a section inside another; a definition should not restate that
  — it adds what `form` cannot say: why the shape is that way, what rule a
  flag drives, how the term differs from a sibling (e.g. `Decision`'s
  definition earns its place by saying why a decision-kind `Event` is worth
  naming; that it is "not a separate class" is already carried by
  `form: attribute`).
- **`form`** — one value per term, from a closed set of five, naming what
  *shape* of thing the term is:
  - `record` — persisted as its own row/entry, with its own identity and
    storage, independent of any other term.
  - `part` — a structured section that lives inside another record and is
    written, replaced, or appended together with it; never stored or
    addressed on its own.
  - `attribute` — a value carried on another term: a flag, or a label from a
    closed set. Not a thing in its own right (e.g. `Decision` is an `Event`
    whose `kind` is "decision"). A borderline case may not warrant a term at
    all — the `epic` boolean on `Task` is described inside the `Task` entry,
    not given its own.
  - `transient` — a value that exists only for the duration of a call or a
    process: computed on read, or held in memory, never written to the
    database.
  - `process` — a practice, pass, or capability that acts but stores no data
    of its own — the behaviour, not a record of it.

  `tests/test_ontology.py` checks that every term's `form` is one of these
  five; it does **not** check that the assigned value is *right*. The prose
  above is the only guard against a later editor reclassifying a boundary
  term (e.g. `Risk` is a `part`, not a `record` — it has identity but its
  storage is embedded in `Task.grooming`; `TaskStore` is a `process`, not a
  sixth "component" value).
- **`relations`** — typed, directional statements connecting two terms, using
  a small fixed vocabulary of predicates: `is-a`, `part-of`, `relates`,
  `persists`, `references`, `describes`. Each relation carries a note
  explaining *why* the relationship has the shape it does, not just that it
  exists.

## Writing a definition: closed vocabulary

A term's definition must express what the thing *is* using only other defined
terms — the vocabulary is a closed topology. Keep implementation detail out:
no grade-string literals, no "computed on every call", no internal function
names. Anything like that belongs in a relation `note` or in
`concept_store/concepts.json`, not the definition.

The payoff is twofold. The obvious one: definitions stay legible to a domain
reader and don't rot when code moves. The more useful one: **if you cannot
state a term's intention in domain terms, that is a signal the implementation
smells** — the code is carrying a concept the ubiquitous language has no room
for. A definition you can't write cleanly is a design finding, not just a
wording problem; fix the concept before forcing the term.

## What this file is not

It is not a schema, an API reference, or a data model — no types, no
function signatures, no storage details. Those live in the code and in
`concept_store/concepts.json`. This file only ever answers "what is the
concept, and how does it relate to the other concepts" — technical detail
belongs elsewhere.

It is also mostly a map rather than a checked claim. `tests/test_ontology.py`
enforces the shallow, mechanical parts — every term's `evidence` cites a file
that exists and a symbol still found in it, every relation joins two defined
terms and carries a note, every term's `form` is one of the five allowed
values — which catches a rename, a deleted file, or a typo'd `form`. It does
*not* check that a definition is accurate, that a relation's direction is
right, or that a term's `form` is the correct one. Treat the definitions and
`form` assignments as a durable but driftable snapshot, kept only as accurate
as whoever last updated it made it.

See the root `CLAUDE.md` ("Three things worth knowing exist") for how this
file relates to `concept_store/concepts.json` and `models/*.sysml`.
