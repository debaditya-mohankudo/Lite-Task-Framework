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

Advisory nudges riding on a tool response (dispatcher.py) are not an
exception to this. A nudge fires only inside the response to a call the
agent already made — no nudge without a pull — and it re-surfaces what the
task already announced (its own id, title, state) rather than fetching new
facts. Nothing is selected from outside what the caller already asked about;
only the timing of the reminder is not the caller's choice.

## Structure only where it earns its keep

Two issue types. Four statuses. One hierarchy rule. That is not minimalism as
an aesthetic — it is a bet that every additional category is a decision someone
has to make on every single task, forever, and that most such decisions carry
no information.

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
