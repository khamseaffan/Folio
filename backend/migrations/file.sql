-- An uploaded file, a file the assistant wrote, or a copy imported from Google Drive. The bytes
-- are in the files bucket. A row is pending while its upload is on the way.
CREATE TABLE IF NOT EXISTS file (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id uuid NOT NULL REFERENCES account (id) ON DELETE CASCADE,
    kind text NOT NULL,
    name text NOT NULL,
    content_type text NOT NULL,
    byte_size bigint NOT NULL,
    status text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (id, account_id),
    CONSTRAINT file_kind_allowed CHECK (kind IN ('upload', 'task', 'drive_copy')),
    CONSTRAINT file_status_allowed CHECK (status IN ('pending', 'ready')),
    CONSTRAINT file_size_positive CHECK (byte_size > 0)
);

CREATE INDEX IF NOT EXISTS file_by_account ON file (account_id, created_at DESC);

CREATE INDEX IF NOT EXISTS file_pending ON file (created_at) WHERE status = 'pending';
