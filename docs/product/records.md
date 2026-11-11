# Records

The MVP records are drawn before any table is created. Each table is created when the work that writes it is built.

Every product row carries the account id. An unused invite carries the account id of the account that sent it.

Knowledge files, abilities, and send confirmations are separate tables. Deleting the profile removes the knowledge files and leaves the abilities.

A queued run is its own row. Its id is the job id. The report or the message it writes points at that run. A second delivery cannot write a second result.

A usage record is its own table, one row per model call. The row points at the chat turn or the run that caused it. The account holds the token cap.

Deleting an account removes its threads, reports, file rows, schedules, and runs by a foreign key. The same request deletes the stored bytes, the Firebase sign-in, the Drive token, and the browser cookies.

Deleting a thread removes its messages, its summary, and its schedules. Knowledge files and other files stay.

Postgres holds these records.
