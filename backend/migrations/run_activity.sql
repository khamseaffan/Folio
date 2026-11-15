-- What a research run did, one line at a time, so the person can follow it while it works.
CREATE TABLE IF NOT EXISTS run_activity (
    id bigserial PRIMARY KEY,
    account_id uuid NOT NULL REFERENCES account (id) ON DELETE CASCADE,
    run_id uuid NOT NULL,
    at timestamptz NOT NULL DEFAULT now(),
    kind text NOT NULL,
    text text NOT NULL,
    url text,
    CONSTRAINT run_activity_kind_allowed CHECK (kind IN ('search', 'read', 'skipped')),
    CONSTRAINT run_activity_run_account_fkey FOREIGN KEY (run_id, account_id)
        REFERENCES run (id, account_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS run_activity_by_run ON run_activity (run_id, id);
