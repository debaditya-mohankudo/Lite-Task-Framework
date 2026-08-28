# The spirit of this framework

This file is about *why* the project is shaped the way it is. It contains no
instructions for using it — the methodology documents cover that, and the code
covers the rest. What is written here is the part that does not survive being
inferred from either.

Read this before changing anything. A change that satisfies the tests and
violates what follows is still the wrong change.

## The one-sentence version

**A system that gets easier to work in every time it is used.**

Not a tracker. Not a process. A loop where each pass leaves behind something
that makes the next pass cheaper — and where the cost of skipping that is
visible rather than free.

## The loop is the product

Everything that stores, validates, or serves data is plumbing. The methodology
is the asset. The plumbing exists to make the loop cheap enough that people
actually run it; it has no value of its own, and it should be recognisable as
subordinate.

The loop is: decide what you are doing, remove the uncertainty before you
start, build, then grade how well you predicted. The fourth step is the whole
point. Without it, planning is theatre — predictions nobody checks, written to
be filed rather than tested. **Grooming makes falsifiable claims. Introspection
grades them.** That single feedback edge is what separates this from a
paperwork ritual, and it is the first thing that will quietly rot, because
skipping it always feels like progress.

## Delete the failure mode; do not police it

The dominant technique here, and the one to reach for first.

If a value is computed on read, it cannot disagree with its source, so nothing
needs to reconcile it. If a routine contains no destructive path, it cannot
destroy anything, so nobody needs to review it for that. If a field is typed,
it cannot be malformed, so no validator is needed.

Before adding a check, ask whether the thing being checked should be
representable at all. Making a bad state *unrepresentable* is cheaper than
*detecting* it, forever — the check must be maintained, remembered, and trusted
by everyone downstream; the impossibility does not.

This is why the rule layer is small. Rules were not moved around during design;
three of them stopped existing because the shape of the data made them
meaningless. That is the target outcome for any rule you are tempted to add.

## Every rule lives in exactly one place

The system this replaced kept its rules in one layer and its data in another,
so taking the data meant losing the rules. That failure is the origin of this
project.

The consequence is a hard constraint: **no caller may enforce a set of rules
that another caller does not.** If two paths can validate differently, they
eventually will, and the divergence will be discovered by whoever gets the
inconsistent answer. One home per rule is not tidiness. It is the thing that
keeps the framework portable, because a rule you can reach around is not a rule.

## Context is pulled, never pushed

Nothing appears in an agent's context by itself. There is no assembled block,
no per-turn injection, no invisible hand deciding what is relevant — every
fact present had to be fetched, on purpose, by something that asked for it.

This is a deliberate trade with a real cost: an agent that does not ask gets
nothing. It is accepted because pushed context fails in worse, quieter ways —
it grows without anyone noticing, it is paid for on every turn whether or not
it was needed, and there is no moment in the transcript where you can point
and say *that* is why the agent knew this. A pull is explicit, paid for once,
and auditable — the agent knows what it read and why.

The corollary matters more than the principle: **an omission must never be
indistinguishable from an absence.** If something was left out for space, the
answer says so. Silent truncation is the one thing a pull interface cannot
afford, because the caller has no other way to find out whether nothing was
there or something was cut.

Advisory nudges riding on a tool response (taskfw/dispatcher/) are not an
exception to this. A nudge pushes no new fact — it only points back at
context the agent already pulled, asking it to look again (e.g. "this
memory is stale," "this task has no linked lessons"). Every nudge fires
solely inside the response to a call the agent already made, and only about
the record that call just touched, never about some other task or memory the
caller didn't ask about — the timing is the caller's choice in every case.
An earlier active-task reminder that rode on the host's PostToolUse event
instead (`drift_reflection_nudge`, via a `taskfw/drift_hook.py` subprocess)
was removed (task:00d9483f): it fired on calls that touched nothing taskfw
owned, and dragged a cross-repo call-count contract along to do it. The
active task is announced once at `tasks__set_active` and not re-surfaced.

## Structure only where it earns its keep

One `epic` boolean. Four statuses. One hierarchy rule. That is not minimalism
as an aesthetic — it is a bet that every additional category is a decision
someone has to make on every single task, forever, and that most such decisions
carry no information.

Where the schema does not anticipate a thought, there is somewhere to put it
anyway. That escape hatch is deliberate and should survive future tidying.
**Over-structuring is how a template becomes something people work around**, and
a system people route around teaches nothing at all, however correct it is.

## Honesty over shape

The values that keep the loop worth running:

**State outcomes plainly.** If tests fail, say so. If a step was skipped, say
that. A summary that overstates what landed is worse than no summary, because
it will be believed.

**Verify the premise.** One concrete check beats an hour of reasoning on top of
an assumption — including, especially, an assumption you wrote yourself an hour
ago. Self-authored premises are exactly as unexamined as inherited ones and
feel more trustworthy, which makes them more dangerous.

**Record reasoning at the moment it happens.** Reconstructed reasoning is not
recovered; it is invented, and it comes out looking sensible in a way the real
decision never did.

**Write down surprises immediately.** A surprise is the cheapest knowledge
available, and it evaporates within the hour.

**Do not invent findings to fill a shape.** If a pass produced nothing, the
honest report is one line saying so. Padding teaches the next reader to skim,
and once they skim, the loop is decoration.

## A groomed plan is a framing, not the answer

Grooming resolves uncertainty about the plan in front of you. It does nothing
about the plan that was never in front of you — the one that would have come
from starting somewhere else. Those are different failures. The first shows up
as an open question you can name. The second shows up as a clean
implementation of the wrong shape, discovered late, when the return on
revisiting it is smallest.

Before implementation starts — and only once grooming has produced a plan you
believe — spend one deliberate pass trying to make a *different* plan. Not to
find flaws in this one; to see if a different starting point lands somewhere
else entirely. A few reliable levers:

- **Change what's assumed fixed.** If the plan treats the current schema, API
  shape, or file boundary as given, ask what the task would look like if that
  boundary moved instead of everything around it.
- **Solve for the opposite constraint.** If the plan optimises for minimal
  diff, sketch the version that optimises for the cleanest resulting shape,
  and vice versa. The gap between the two is information, even when you keep
  the first one.
- **Ask what a different role would object to.** A reviewer, an operator six
  months from now, someone porting this to another host — each notices
  something the author's own framing makes invisible, because that framing is
  exactly what sits inside the author's blind spot.

Most of the time the second pass agrees with the first, and that agreement is
worth having in its own right — a plan that survived being re-derived, not
merely a plan that survived being read. When it disagrees, the disagreement is
a `risk` or an `open_question`, not a private doubt: write it where grooming
already looks, or it is exactly as lost as if the second pass never happened.

This is not a second grooming pass and it does not earn its own checklist —
turning it into a step would make it precisely the kind of ritual doc 05 warns
against skipping once the work "went well." It is one deliberate act of
distrust in your own first framing, sized to the task, owned by whoever is
about to start implementing rather than handed to a separate reviewer.

## Optional means optional

Convenience features are allowed to be absent. Anything host-specific is
additive, independently switchable, and fails silently rather than blocking
work — the worst outcome of a broken convenience must be a missing convenience.

And nothing may be recoverable only through it. If an automatic capture can be
missed, there must be a way to rebuild what was missed from the durable record.
Failing open is only acceptable when it does not mean losing data quietly.

## How to tell you are drifting

- A rule got a second implementation "just for this caller".
- A check was added where the bad state could have been made impossible.
- Something started appearing in context that nobody asked for.
- A new type, status, or required field was added to express something a tag
  would have carried.
- A summary described intent rather than what actually happened.
- Introspection got skipped because the work went well.

The last one is the one that actually happens.

## Three things worth knowing exist

`models/*.sysml` holds structural claims — state machines, requirements,
calc defs — transcribed by hand from the code they describe, not generated
from it or bound to it at runtime. A doc comment saying "this is computed
on read, never stored" is a claim someone has to trust; the same claim as a
`calc def`, checked against the real source by a paired test in
`tests/test_models.py`, is a claim that can be caught drifting. It exists
for the same reason the loop exists: an assertion nobody checks decays into
theatre.

`concept_store/concepts.json` is architectural memory that survives across
tasks — what a module is for, its contracts and invariants, discovered once
and then reusable instead of re-derived from scratch by the next task that
touches it. It grows the same way loop memory does: a task either updates a
concept that turned out wrong, or writes one for a module nobody had
understood well enough to describe yet.

`ontology/task-domain.json` is the domain's ubiquitous language: the nouns
(Task, Epic, Link, TaskStore, MemoryRecord, …) and the explicit typed
relations between them — is-a, part-of, relates, persists, references,
describes. It exists separately from concept_store because the two answer
different questions and don't share a shape: concept_store is architecture
PER MODULE, one summary per file, enforced 1:1 against the file tree by
tests/test_concepts.py; the ontology is vocabulary PER TERM, and a single
term routinely spans several modules while a module can define several
terms. Forcing the ontology into concept_store's per-file schema would have
meant either a fake module anchor for a cross-cutting idea or silently
bending the coverage test's guarantee — so it gets its own file and its own,
looser shape instead. tests/test_ontology.py does check it against the
code, but only shallowly: every term's `evidence` file must exist and the
cited symbol must appear in it as a substring, and every relation must
join two defined terms with a note. Definitions, `note` prose, and the
accuracy of a relation's direction are not checked — so treat it as a map
that is caught drifting only on a rename or a deleted file, not a claim
that's verified.

None of the three is instructions for using it — the introspection
methodology and the tool schemas cover that. What is worth knowing here is
only that they exist and why: each is a way of not re-deriving the same fact
twice.

Read in that order, the three form one chain, coarse to fine: this file says
*why* the project is shaped as it is, `ontology/task-domain.json` names the
*terms* that shape produces and how they relate, and `concept_store/concepts.json`
pins what each *module* promises in service of those terms. A term the
ontology introduces should be traceable down to the module(s) that embody it
in concept_store, and a module's concept should be traceable up to the term
it exists to serve — the chain is only worth keeping if both directions
still resolve.

## Memory

Cross-session memory lives in `~/.claude/MEMORY.sqlite`, shared across projects. Use the `mcp__claude-hooks__memory__*` tools to read/write it — `memory__add` / `memory__add_batch` to save, `memory__search` to recall. Tag entries with domain `task-framework` for this project.
