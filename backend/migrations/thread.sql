CREATE TABLE IF NOT EXISTS thread (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id uuid NOT NULL REFERENCES account (id) ON DELETE CASCADE,
    name text NOT NULL DEFAULT 'New thread',
    summary text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (id, account_id)
);

CREATE INDEX IF NOT EXISTS thread_account_id_created_at
    ON thread (account_id, created_at);
