# Records

The MVP records are drawn before any table is created. Each table is created when the work that writes it is built.

Every product row carries the account id while the account exists. A child row names the same account as its parent. `thread`, `message`, `schedule`, `run`, `file`, `knowledge_file`, and `report` are unique on `id` and `account_id`, and the child link uses that pair.

An integration is its own table. One account has one row per provider. The secret is on that row. A new provider is a new row. A list or a fact that row cannot hold is a separate table, created when that provider is built.

Knowledge files, abilities, and send confirmations are separate tables. Deleting the profile removes the knowledge files and leaves the abilities.

A queued run is its own row. Its id is the job id. The report or the message it writes points at that run. A second delivery cannot write a second result.

A usage record is its own table, one row per model call. The row points at the chat turn or the run that caused it. The row keeps the email. Deleting the account clears the account id and leaves the row. The cap for that email is the sum of these rows. The token cap and the stop are one row for that email. Deleting the account leaves that row. A new account for that email keeps the cap and the stop.

Deleting an account removes its threads, reports, file rows, schedules, and runs by a foreign key. It leaves the usage rows. Deleting a run does not delete its report. Deleting the account removes the report before the run. The same request deletes the stored bytes, the Firebase sign-in, the integration secrets, and the browser cookies.

A stored file's path is `accounts/{account_id}/.../{row id}/...`. The knowledge text is the row. A report keeps its text in the row, and the file at its path is the download.

Deleting a thread removes its messages, its summary, and its schedules. Knowledge files and other files stay.

Postgres holds these records.
