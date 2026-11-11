# Files

A person can upload a PDF, plain text, Markdown, CSV, XLSX, JPEG, PNG, WebP, or HEIC file. One upload is at most 50 MB.

Plain text is read as text, a spreadsheet as cells, a PDF as a document, and an image as the image. The model receives the image.

The assistant writes a task file when the person's task needs that file as its result. That file may be Markdown or PDF. A reply that stays in the thread writes no task file. Knowledge files are specified in `docs/product/profile.md`.

The person can download one uploaded file, one task file, or one report. The person tells the agent to delete one file, and the agent deletes it. Deleting a file leaves the messages that mentioned it.

An upload is a file after its bytes are stored and its ownership row commits. An unfinished upload is discarded after 24 hours.

Each account has a byte count of uploads and task files, including an upload whose row has not committed yet. The person can see this count. Knowledge files and Drive copies are outside that count. A file is not stored when the count plus that file would exceed 5 GB. Deleting a file lowers the count. Discarding an unfinished upload lowers the count.
