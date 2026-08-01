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
    part def Foundation;
}
