CREATE TABLE IF NOT EXISTS knowledge_file (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id uuid NOT NULL REFERENCES account (id) ON DELETE CASCADE,
    name text NOT NULL,
    body text NOT NULL,
    byte_size bigint NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (id, account_id),
    UNIQUE (account_id, name)
);

CREATE INDEX IF NOT EXISTS knowledge_file_body_search
    ON knowledge_file USING gin (to_tsvector('english', body));
