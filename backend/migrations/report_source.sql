CREATE TABLE IF NOT EXISTS report_source (
    account_id uuid NOT NULL REFERENCES account (id) ON DELETE CASCADE,
    report_id uuid NOT NULL,
    position integer NOT NULL,
    kind text NOT NULL,
    title text NOT NULL,
    url text,
    used_for text NOT NULL,
    knowledge_file_id uuid,
    PRIMARY KEY (report_id, position),
    CONSTRAINT report_source_kind_allowed CHECK (kind IN ('page', 'file', 'drive_copy')),
    CONSTRAINT report_source_report_account_fkey FOREIGN KEY (report_id, account_id)
        REFERENCES report (id, account_id) ON DELETE CASCADE,
    CONSTRAINT report_source_knowledge_account_fkey FOREIGN KEY (knowledge_file_id, account_id)
        REFERENCES knowledge_file (id, account_id) ON DELETE SET NULL (knowledge_file_id)
);
