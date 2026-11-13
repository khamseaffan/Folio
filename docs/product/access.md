# Access

An account requires an email address. The address is stored in lowercase. One email holds one account.

An account can be created for `khamseaffan@gmail.com`, or for an address at `sakana.ai`. Any other email is refused at sign-in with the line "This email is not an allowed account."

`khamseaffan@gmail.com` is the first account.

The site opens on one public page. The person signs in from that page, and sign-in opens the app.

The person signs in through Firebase Authentication with Google or a magic link. Firebase holds that sign-in. The Google account's email must be on the allowlist. The magic link works once and expires after 24 hours. That sign-in verifies the email. The account stores no password.

The person sets a display name. The name is required and is 1 to 40 characters.

A session ends after 30 minutes in which the person sends nothing and the assistant does nothing. An account may have two sessions. A third sign-in locks the account row and ends the oldest session in that same save. The person can see the sessions and end one. Ending a session is logging out.

Ending a session does not stop a running reply. The reply continues on the server. Another browser, or a refresh, shows the stream still running or the finished reply.

Deleting the account is immediate and cannot be restored. It removes the Firebase sign-in. Detail is in `docs/product/failure.md`.

Accounts are personal. `khamseaffan@gmail.com` can open the usage tool, raise one email's token cap, and stop an email. The cap and the stop stay with that email after the account is deleted. That account cannot read another account's threads, files, or knowledge.
