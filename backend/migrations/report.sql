CREATE TABLE IF NOT EXISTS report (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id uuid NOT NULL REFERENCES account (id) ON DELETE CASCADE,
    thread_id uuid,
    run_id uuid NOT NULL,
    name text NOT NULL,
    question text NOT NULL,
    body text NOT NULL,
    stop_note text NOT NULL,
    status text NOT NULL DEFAULT 'ready',
    content_type text NOT NULL DEFAULT 'text/markdown',
    byte_size bigint NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (id, account_id),
    UNIQUE (run_id),
    CONSTRAINT report_status_allowed CHECK (status = 'ready'),
    CONSTRAINT report_thread_account_fkey FOREIGN KEY (thread_id, account_id)
        REFERENCES thread (id, account_id) ON DELETE SET NULL (thread_id),
    CONSTRAINT report_run_account_fkey FOREIGN KEY (run_id, account_id)
        REFERENCES run (id, account_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS report_account_id_created_at
    ON report (account_id, created_at);
