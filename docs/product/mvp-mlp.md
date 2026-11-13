# MVP and MLP

## MVP

1. A person has a private account.
2. An account is created for khamseaffan@gmail.com or an address at sakana.ai. Any other email is refused at sign-in.
3. Sign-in is Firebase Authentication with Google or a one-time magic link. The link expires after 6 hours.
4. The person sets a display name of 1 to 40 characters.
5. The person can end a session. A session also ends after 30 minutes of silence from the person and the assistant, and an account has at most two sessions.
6. Deleting an account is immediate and cannot be restored. The usage rows, the token cap, and the stop for that email stay.
7. The person talks with one platform model, `gemini-4-argon`, and cannot change it. The call goes through one internal interface in the Python API.
8. The person has many threads, and the agent renames or deletes a thread when told.
9. A message has no length limit, and a call includes the newest blocks that fit, including the latest 10 person blocks and the latest 10 assistant blocks.
10. A reply streams, the person can stop it, and the partial reply is kept.
11. Knowledge is English Markdown files, profile.md is included in every model call, and a search opens at most five other files.
12. The agent writes, changes, and deletes knowledge when told, and the person reads it in the profile panel.
13. The agent remembers abilities, lists them for reading, and changes them when told.
14. Research opens at most 20 sources, stops when new pages mostly repeat or after 30 minutes, and the file contains the question, the text, how the run stopped, and the source list.
15. The person downloads a report, and the agent names, renames, or deletes it when told.
16. The person says a schedule in the chat, the agent creates, updates, and deletes it, and deleting the thread deletes the schedule.
17. A scheduled run uses files, imported Drive copies, the web, and a remembered ability when the task needs them.
18. When a run of the same schedule is still working or waiting, the next occurrence is skipped, and a different schedule runs at the same time.
19. The person uploads a PDF, plain text, Markdown, CSV, XLSX, JPEG, PNG, WebP, or HEIC file of at most 50 MB.
20. Uploads and task files stop at 5 GB, and knowledge files and Drive copies sit outside that count.
21. The assistant writes the file the task needs, as Markdown, PDF, or another type, and the person can download it.
22. A Drive import is one file the person picks, stored as a private read-only copy.
23. Chrome on the worker reads pages, and a send on a website runs only after the person confirms that exact send. The card expires 24 hours after it appears.
24. Speech is transcribed by Gemini into the composer, and the audio is discarded.
25. The person sees token totals and the file byte count. The email stops at 10 million recorded tokens unless that cap is raised. The cap and the stop stay with the email.
26. The site opens on one public page. Sign-in opens chat, reports, files, a profile panel, and settings. The interface is English.
27. A working or waiting run shows on the thread name, a question or a failed run appears in that thread, and two runs may write in that thread at the same time.
28. When Postgres is unavailable, the app stops and says the system is down.
29. A queued run is its own row and its id is the job id. A second delivery does not write a second result. A dead provider is retried three times, then the failed run stays on a dead letter queue for the developers.

## MLP

1. A later version lets one account have several named bots.
2. A later version lets the assistant name a new thread.
3. A later version searches threads and edits an earlier message.
4. A later version lets the model write new code and run it in a sandbox.
5. A later version runs a repeated form when code matches the site, the form, the allowed fields, and an expiry.
6. A later integration is another row: a connection on the account, a secret in Cloud KMS, and one or more tools.
7. A later version exports an account's data.
8. A later version groups accounts in an organization.
