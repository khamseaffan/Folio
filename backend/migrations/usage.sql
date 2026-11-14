CREATE TABLE IF NOT EXISTS usage (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id uuid REFERENCES account (id) ON DELETE SET NULL,
    email text NOT NULL,
    at timestamptz NOT NULL DEFAULT now(),
    provider text NOT NULL,
    model text NOT NULL,
    input_tokens bigint,
    output_tokens bigint,
    total_tokens bigint,
    expected_cost numeric,
    usage_missing boolean NOT NULL,
    message_id uuid,
    run_id uuid,
    operation text NOT NULL,
    estimated boolean NOT NULL DEFAULT false,
    CONSTRAINT usage_email_lowercase CHECK (email = lower(email)),
    CONSTRAINT usage_operation_allowed CHECK (
        operation IN ('chat', 'research', 'schedule', 'transcription', 'browser')
    ),
    CONSTRAINT usage_message_account_fkey FOREIGN KEY (message_id, account_id)
        REFERENCES message (id, account_id) ON DELETE SET NULL (message_id)
);

-- A table made before this column existed gets it here.
ALTER TABLE usage ADD COLUMN IF NOT EXISTS estimated boolean NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS usage_email
    ON usage (email);

CREATE INDEX IF NOT EXISTS usage_account_id_at
    ON usage (account_id, at)
    WHERE account_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS usage_account_id_provider_model
    ON usage (account_id, provider, model);
