package DerivedValues {
    doc /* calc def models for values this framework computes on read and
         * never stores — transcribed from taskfw/models.py, taskfw/memory.py,
         * and taskfw/accuracy.py. Each backs one of DerivedProgressRequirement,
         * LoopMemoryEvidenceRequirement, and GroomingFeedbackEdgeRequirement
         * in requirements.sysml, turning the prose claim "computed on read,
         * never stored" into a named structure.
         *
         * Kept honest the same way TaskLifecycle is: not by executing these
         * calc bodies, but by TestDerivedValuesMatchCode in
         * tests/test_models.py, which imports the real Python source of
         * truth and asserts the identifiers here match it. A calc def with
         * no such test would be a second unenforced restatement of the same
         * claim — the failure mode this file exists to avoid, not repeat. */

    private import Foundation::ModelProvenance;

    @ModelProvenance {
        derivedFromCommit = "59754fa098d947451136b84179f804717cf8266e";
        derivedAt = "2026-08-23";
        modelledPaths = (
            "taskfw/models.py",
            "taskfw/memory.py",
            "taskfw/accuracy.py"
        );
    }

    calc def Progress {
        doc /* Transcribed from taskfw/models.py:Task.progress. (done, total)
             * counted from the resolution list on every read; never stored,
             * so it cannot drift from the list it counts. */
        in resolutionCount : Integer;
        in doneCount : Integer;
        return done : Integer;
        return total : Integer;
    }

    enum def MemoryStandingValue {
        superseded;
        disputed;
        contradicted;
        confirmed;
        unverified;
    }

    calc def MemoryStanding {
        doc /* Transcribed from taskfw/memory.py:_standing. One word for how
             * much weight a memory currently carries, derived from
             * confirmed_by / contradicted_by link counts and superseded_by
             * on every read. disputed is reported rather than resolved: the
             * store cannot know which side is right, and returning it as
             * settled would be an omission indistinguishable from an
             * absence. */
        in confirmed : Integer;
        in contradicted : Integer;
        in superseded : Boolean;
        return standing : MemoryStandingValue;
    }

    calc def GroomingAccuracyTally {
        doc /* Transcribed from taskfw/accuracy.py:grooming_accuracy. Tallies
             * risk grades — materialized, avoided, wrong — across finished
             * tasks' introspection reports, plus ungraded and unrecognised
             * counts. Never reads a self-reported total back; the tally is
             * the derivation, not a stored count of it. */
        in materialized : Integer;
        in avoided : Integer;
        in wrong : Integer;
        in ungraded : Integer;
        in unrecognised : Integer;
    }
}
