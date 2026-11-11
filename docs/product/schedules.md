# Schedules

The person says in the chat when the work should happen. The agent creates the schedule, updates it, and deletes it. Upcoming schedules are listed in the profile panel for reading. Detail is in `docs/product/screens.md`.

A schedule belongs to the thread where the person asked for it. Deleting that thread deletes the schedule. One account may have any number of schedules.

A schedule runs the task the person described. The run uses the account's files, imported Drive copies, the web, and a remembered ability when the task needs them. A new Drive import still starts when the person picks the file. The run does not write to Drive. A send on a website still requires the confirmation in `docs/product/confirmations.md`.

The run writes a message in the thread or a report.

A schedule runs while the account is inside its usage limit and its storage count. Detail is in `docs/product/models.md` and `docs/product/files.md`.

When a run of that same schedule is still working or waiting and the next time comes, that next run is skipped. A different schedule runs at the same time.
