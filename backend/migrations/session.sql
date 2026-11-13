CREATE TABLE IF NOT EXISTS session (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id uuid NOT NULL REFERENCES account (id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    last_active_at timestamptz NOT NULL DEFAULT now(),
    ended_at timestamptz
);

CREATE INDEX IF NOT EXISTS session_open_account_id
    ON session (account_id)
    WHERE ended_at IS NULL;
