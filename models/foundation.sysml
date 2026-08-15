package Foundation {
    doc /* Cross-cutting: configuration, database schema, migration, logging.
         * A shared dependency of every other part, not a peer subsystem.
         *
         * Corresponds to taskfw/config.py, taskfw/db/schema.py,
         * taskfw/db/connect.py, and taskfw/log.py.
         *
         * Two properties of this part are load-bearing elsewhere in the model
         * and are recorded here because they are easy to lose in a refactor:
         *
         *   1. Schema is ONE declarative structure (schema.TABLES) that
         *      generates DDL, drives migration, and is read directly by tests.
         *      There is no second definition, so no parity check is needed to
         *      keep two definitions aligned.
         *
         *   2. Connection pragmas (WAL, busy_timeout, foreign_keys) are set in
         *      connect() and nowhere else. Several processes write the same
         *      SQLite file concurrently, so this is correctness, not tuning. */

    metadata def ModelProvenance {
        doc /* Which state of the codebase a package was written against.
             *
             * derivedFromCommit is the last commit that changed the CODE this
             * model describes — deliberately not the commit that last touched
             * the .sysml file. Stamped this way it changes exactly when the
             * model might have become wrong; stamped with the model's own
             * commit it would churn on every typo fix while never once
             * indicating staleness. It also sidesteps the fact that a commit
             * cannot contain its own sha, so no post-commit amend is needed.
             *
             * To check a package for drift, compare its stamp against the
             * last commit touching the paths its docs cite:
             *
             *     git log -1 --format=%H -- <cited paths>
             *
             * A newer result means the model has not been re-examined since
             * that code moved. It does NOT mean the model is wrong — only
             * that nobody has checked.
             *
             * modelledPaths is that list of cited paths, declared here rather
             * than hardcoded in the checker, so a package states its own
             * scope and the test only enforces what the model already
             * claims. It is also checked for existence: a path that no
             * longer resolves is itself drift, and a silent one, since a
             * requirement can go on citing a file long after it was renamed.
             *
             * Enforced by tests/test_model_provenance.py. */
        attribute derivedFromCommit : String;
        attribute derivedAt : String;
        attribute modelledPaths : String[0..*];
    }

    @ModelProvenance {
        derivedFromCommit = "25264f57b2a7afc6cfbebfacf780fe2bf019e9bd";
        derivedAt = "2026-08-15";
        modelledPaths = (
            "taskfw/config.py",
            "taskfw/db/schema.py",
            "taskfw/db/connect.py",
            "taskfw/log.py"
        );
    }

    part def Foundation {
        doc /* One declarative schema, one set of connection pragmas —
             * see the package doc for why both are load-bearing. */
    }
}
