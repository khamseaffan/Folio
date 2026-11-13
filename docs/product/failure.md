# Failure

Deleting an account is immediate. It removes the Firebase sign-in, sessions, rows, files, the integration secrets, browser cookies, and schedules. The usage rows stay, with the email kept and the account id cleared. The token cap and the stop for that email stay. The other owned rows go by a foreign key. Detail is in `docs/product/records.md`. It does not remove the platform model keys. A deleted account cannot be restored.

When Postgres is unavailable, the app stops. A popup says the system is down and repair is underway. The app accepts no work until Postgres is back.

A worker crash is told in the thread. A send on a website that may already have completed is not repeated. The agent tells the person what happened. When nothing was sent, the agent decides the next step, and it can ask the person whether to try again.

A queued run retries a dead provider three times after the first attempt. A stopped account is not retried. After the third retry the run stops. The failed run stays on a dead letter queue for developers. The person sees one line in the thread that the run failed.
