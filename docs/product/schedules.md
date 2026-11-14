# Schedules

The person says in the chat when the work should happen. The agent creates the schedule, updates it, and deletes it. Upcoming schedules are listed in the profile panel for reading. Detail is in `docs/product/screens.md`.

A schedule belongs to the thread where the person asked for it. Deleting that thread deletes the schedule. One account may have any number of schedules.

A schedule runs the task the person described. The run uses the account's files, imported Drive copies, the web, and a remembered ability when the task needs them. A new Drive import still starts when the person picks the file. The run does not write to Drive. A send on a website still requires the confirmation in `docs/product/confirmations.md`.

The run writes a message in the thread or a report.

A schedule runs while the account is inside its usage limit and its storage count. Detail is in `docs/product/models.md` and `docs/product/files.md`.

When a run of that same schedule is still working or waiting and the next time comes, that next run is skipped. A different schedule runs at the same time.

## How a schedule fires

A clock calls the worker once a minute. That call is the tick. The tick finds every schedule whose next time has come. A stopped account, or an account at its usage limit, starts nothing, and its schedule stays due until the limit allows it.

For a due schedule the model works out the next time from the person's own words, their time zone, and the time now. The answer is a time or "none". "None" leaves the schedule listed with no next time, which is how a one-time schedule ends. If the answer cannot be used, the schedule gets no next time, the thread shows one line saying so, and the failure is logged.

The tick then makes one run and puts it on a queue. The queue calls the worker with the run id. The worker runs the task with the same agent loop as chat, and writes the result as one message in the thread. A second delivery of the same run writes nothing.

A run has the same tools as chat, except the three that create, change, and delete a schedule. A run is limited to 8 tool rounds.

The person's time zone comes from the browser with each message. It is written into the schedule's words, so the schedule does not move when the person travels.
