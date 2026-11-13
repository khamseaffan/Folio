CREATE TABLE IF NOT EXISTS email_limit (
    email text PRIMARY KEY,
    token_cap bigint NOT NULL DEFAULT 10000000,
    stopped boolean NOT NULL DEFAULT false,
    CONSTRAINT email_limit_email_lowercase CHECK (email = lower(email))
);
