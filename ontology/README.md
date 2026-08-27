# Ontology

This directory holds `task-domain.json`, the domain's ubiquitous language: the
nouns of task-framework (Task, Epic, Grooming, MemoryRecord, ...) and the
explicit, typed relations between them.

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
  to, and a plain-language definition of what it is (including things that
  are *not* distinct things — e.g. an Epic is just a Task with a certain
  field set, not a separate class).
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

It is also a map, not a checked claim: nothing currently tests it against the
code (unlike `concept_store/concepts.json`, which is enforced 1:1 against the
file tree). Treat it as a durable but driftable snapshot of the domain
vocabulary, kept only as accurate as whoever last updated it made it.

See the root `CLAUDE.md` ("Three things worth knowing exist") for how this
file relates to `concept_store/concepts.json` and `models/*.sysml`.
