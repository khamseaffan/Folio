CREATE TABLE IF NOT EXISTS account (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email text NOT NULL UNIQUE,
    firebase_uid text UNIQUE,
    display_name text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT account_email_lowercase CHECK (email = lower(email)),
    CONSTRAINT account_display_name_length CHECK (
        display_name IS NULL OR char_length(display_name) BETWEEN 1 AND 40
    ),
    CONSTRAINT account_email_fkey FOREIGN KEY (email)
        REFERENCES email_limit (email) ON DELETE RESTRICT
);
