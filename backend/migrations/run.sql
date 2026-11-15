CREATE TABLE IF NOT EXISTS run (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id uuid NOT NULL REFERENCES account (id) ON DELETE CASCADE,
    thread_id uuid,
    schedule_id uuid,
    kind text NOT NULL,
    status text NOT NULL,
    task text NOT NULL,
    question text,
    work_ms integer NOT NULL DEFAULT 0,
    attempt_count integer NOT NULL DEFAULT 0,
    delivered_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    UNIQUE (id, account_id),
    CONSTRAINT run_kind_allowed CHECK (kind IN ('research', 'schedule', 'browser', 'web')),
    CONSTRAINT run_status_allowed CHECK (
        status IN ('queued', 'working', 'waiting', 'ready', 'failed', 'cancelled', 'dead')
    ),
    CONSTRAINT run_thread_account_fkey FOREIGN KEY (thread_id, account_id)
        REFERENCES thread (id, account_id) ON DELETE SET NULL (thread_id),
    CONSTRAINT run_schedule_account_fkey FOREIGN KEY (schedule_id, account_id)
        REFERENCES schedule (id, account_id) ON DELETE SET NULL (schedule_id)
);

-- A table made before this column existed gets it here.
ALTER TABLE run ADD COLUMN IF NOT EXISTS started_at timestamptz;

CREATE INDEX IF NOT EXISTS run_status_queued
    ON run (status)
    WHERE status = 'queued';

CREATE UNIQUE INDEX IF NOT EXISTS run_one_in_flight_per_schedule
    ON run (schedule_id)
    WHERE schedule_id IS NOT NULL AND status IN ('queued', 'working', 'waiting');

-- The two keys that waited for this table. Each is added once.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'message_run_account_fkey') THEN
        ALTER TABLE message ADD CONSTRAINT message_run_account_fkey
            FOREIGN KEY (run_id, account_id) REFERENCES run (id, account_id)
            ON DELETE SET NULL (run_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'usage_run_account_fkey') THEN
        ALTER TABLE usage ADD CONSTRAINT usage_run_account_fkey
            FOREIGN KEY (run_id, account_id) REFERENCES run (id, account_id)
            ON DELETE SET NULL (run_id);
    END IF;
END $$;
