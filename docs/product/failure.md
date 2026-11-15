# Failure

Deleting an account is immediate. It removes the Firebase sign-in, sessions, rows, files, the integration secrets, browser cookies, and schedules. The usage rows stay, with the email kept and the account id cleared. The token cap and the stop for that email stay. The other owned rows go by a foreign key. Detail is in `docs/product/records.md`. It does not remove the platform model keys. A deleted account cannot be restored.

When Postgres is unavailable, the app stops. A popup says the system is down and repair is underway. The app accepts no work until Postgres is back.

A failed chat reply keeps the person's message. Words that had already arrived stay. The thread shows the line "That didn't go through." and Try again. Try again sends that same message once more. A reply that then arrives replaces those words and removes the line. The screen shows no cause.

A worker crash is told in the thread. A send on a website that may already have completed is not repeated. The agent tells the person what happened. When nothing was sent, the agent decides the next step, and it can ask the person whether to try again.

A queued run retries a dead provider three times after the first attempt. A stopped account is not retried. After the third retry the run stops. The failed run stays on a dead letter queue for developers. The person sees one line in the thread that the run failed.

A scheduled run is tried up to four times, and each try adds one to `attempt_count`. After the fourth failure the run is marked `dead`, the thread shows "A scheduled run failed.", and the failure is logged with the run id. The dead run stays in the `run` table with its task and attempt count. That table is the dead letter record.

A run that fails because the account is stopped or at its limit is marked `cancelled` and is not tried again. A run whose thread was deleted is cancelled, or finishes with no message when the thread goes during the run.

A research run follows the same rules, with these differences. The thread shows "The research failed." after the fourth failure. The time of each attempt adds up, and a retry with less than 3 minutes of the 30 left is not started, so the run is marked `failed` with the same line. A research run that opens no source is marked `failed`, shows "I could not open any source for this question.", and is not tried again. A research run for an account that is stopped or at its limit when it starts shows the stopped line or the limit line. An account that is stopped during the run cancels it with the stopped line and no report. An account that reaches its limit during the run gets one last call that writes the report. The person cancelling, or a deleted thread, ends the run with no report.

A run that has been `working` for more than 90 minutes since its latest attempt began is marked `dead` by the next tick and told in the thread. This frees its schedule to fire again. A run that is still `queued` after 30 minutes is marked `dead` the same way. A `queued` run older than 2 minutes is put on the queue again by the tick, so a run that never reached the queue is not lost.

The lines "A scheduled run failed." and the no-next-time line are plain assistant messages of kind `result`. They do not offer Try again, which belongs to a failed chat reply only.

A schedule never stops without a line in the thread. When the next time cannot be worked out, the thread shows "This schedule has no next time now. Tell Folio when it should run again."
