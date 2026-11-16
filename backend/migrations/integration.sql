-- A connection to another service, kept for one person. The secret is encrypted with Cloud KMS.
CREATE TABLE IF NOT EXISTS integration (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id uuid NOT NULL REFERENCES account (id) ON DELETE CASCADE,
    provider text NOT NULL CHECK (provider IN ('google_drive')),
    secret bytea NOT NULL,
    label text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (account_id, provider)
);
