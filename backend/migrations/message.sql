CREATE TABLE IF NOT EXISTS message (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id uuid NOT NULL REFERENCES account (id) ON DELETE CASCADE,
    thread_id uuid NOT NULL,
    run_id uuid,
    role text NOT NULL,
    kind text NOT NULL,
    body text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (id, account_id),
    CONSTRAINT message_thread_account_fkey FOREIGN KEY (thread_id, account_id)
        REFERENCES thread (id, account_id) ON DELETE CASCADE,
    CONSTRAINT message_role_allowed CHECK (role IN ('person', 'assistant')),
    CONSTRAINT message_kind_allowed CHECK (kind IN ('chat', 'question', 'result', 'failure'))
);

CREATE INDEX IF NOT EXISTS message_thread_id_created_at
    ON message (thread_id, created_at);
