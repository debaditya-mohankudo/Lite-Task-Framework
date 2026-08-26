# models

These `.sysml` files are a SysML v2 model of task-framework: structural
claims — state machines, requirements, calc defs — transcribed by hand from
the code they describe, not generated from it and not bound to it at
runtime. A doc comment saying "this is computed on read, never stored" is a
claim someone has to trust; the same claim as a `calc def`, checked against
the real source by a paired test in `tests/test_models.py`, is a claim that
can be caught drifting. It exists for the same reason the loop the rest of
the framework runs exists: an assertion nobody checks decays into theatre.

## Files

- `foundation.sysml` — the cross-cutting shared dependency (config, DB
  schema, migration, logging) that every other part sits on. Defines
  `ModelProvenance`, the metadata pattern every package uses to stamp which
  commit it was last checked against.
- `task_framework_system.sysml` — the top-level structural decomposition:
  four parts over Foundation, with the reasoning for why the boundaries sit
  where they do (a rule and the data it governs must live in the same part).
- `task_lifecycle.sysml` — the task/epic state machine and status
  transitions.
- `derived_values.sysml` — values computed on read rather than stored, and
  the calc defs backing that claim.
- `mcp_interface.sysml` — the MCP tool surface as a model.
- `requirements.sysml` — the system's requirements, each satisfied by
  exactly one part of the decomposition in `task_framework_system.sysml`.

## Staying honest about drift

Every package stamps a `ModelProvenance` metadata block recording the last
commit that changed the *code* it describes (not the last commit that
touched the `.sysml` file itself). To check a package for staleness:

```
git log -1 --format=%H -- <paths the package's docs cite>
```

A newer commit than the stamp means the model hasn't been re-examined since
that code moved — not that it's wrong, only that nobody has checked.

Nothing runs these files at runtime. `tests/test_models.py` and
`tests/test_sysml.py` are what keep them honest against the real source; the
`mcp__sysml-mcp__*` tools and the `generate-sysml` / `discover-sysml-req`
skills are how you parse, validate, or query them.

This is structure over the **whole system** — state machines and
requirements, not a per-module record. See `concept_store/README.md` for the
per-module counterpart (architecture per file) and `ontology/README.md` for
the domain vocabulary shared across modules. The "Three things worth knowing
exist" section of the top-level `CLAUDE.md` covers how the three relate.
