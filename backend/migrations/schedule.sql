CREATE TABLE IF NOT EXISTS schedule (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id uuid NOT NULL REFERENCES account (id) ON DELETE CASCADE,
    thread_id uuid NOT NULL,
    task text NOT NULL,
    when_text text NOT NULL,
    next_run_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (id, account_id),
    CONSTRAINT schedule_thread_account_fkey FOREIGN KEY (thread_id, account_id)
        REFERENCES thread (id, account_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS schedule_next_run_at
    ON schedule (next_run_at)
    WHERE next_run_at IS NOT NULL;
