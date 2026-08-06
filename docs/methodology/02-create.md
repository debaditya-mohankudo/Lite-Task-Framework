# 2. Create

A task is a typed object. There is no body template, no required sections, and
no validator rejecting a malformed body — the object's shape *is* the schema,
so a task that saves is well-formed by construction.

```python
tasks__create(
    title="Short, specific, and readable in a list",
    type="task",                      # or "epic"
    parent="<epic id>",               # optional
    motivation="Why this is worth doing, and what breaks without it.",
    resolution=["first step", "second step"],
    files=["path/one.py", "path/two.py"],
    tags=["area", "kind"],
    notes="Anything the schema did not anticipate.",
)
```

## What each field is for

**title** — how the task appears in every list. Specific beats short: "fix
logging" tells a future reader nothing, "logging never named the task id"
tells them the whole story.

**motivation** — why, not what. The single most valuable field six months
later, and the one most often left thin. Say what breaks if this is not done.
If the task exists because something surprised you, record the surprise.

**resolution** — a checklist of concrete steps, each naming a file or module
where possible. Progress is derived from it, so it doubles as the completion
signal. Vague items produce a task that is never quite done.

**files** — the files this task is expected to touch. Read directly by
`tasks__context` and by grooming, so it is not documentation; it is how the
system finds relevant work. Being wrong here is cheap to fix and expensive to
leave.

**tags** — free-form. This is where "bug", "refactor", "urgent" live, since the
type system deliberately does not carry them.

**notes** — the escape hatch, and it exists on purpose. Over-structuring is how
a task template becomes something people work around. If a thought does not fit
a field, it goes here rather than being lost.

## Checklist items

```python
tasks__check_item(task_id, index=0, done=True)
```

Zero-based. Progress (`done`/`total`) is computed from the list every time it
is read, so it can never disagree with the checklist it counts.

## Rules that apply

Only two, and both are structural rather than stylistic:

- An epic cannot have a parent.
- A task cannot be its own parent.

A refusal returns `{"error": ..., "rule": ...}`. The `rule` field names which
rule fired, so a caller can react to the kind of refusal without matching on
the message text.

## Linking

```python
tasks__link(from_id, to_id, rel="depends_on")   # idempotent
tasks__unlink(from_id, to_id, rel="depends_on") # rel optional — omit to remove all
```

Enforced vocabulary (`taskfw.models.TASK_EDGE_RELATIONS`, checked by
`lifecycle.check_link_rel` on `tasks__link` — not on `tasks__unlink`, which
must always be able to remove any existing edge): `depends_on`, `blocks`,
`relates_to`, `duplicates`, `removes`, `implements`, `supersedes`, `reverts`,
`writes_to`, `extends`. An unrecognised relation is rejected, not silently
accepted.

Record sequencing as edges rather than only in prose. Prose is for humans
reading one task; edges are what lets anything else reason about order.
