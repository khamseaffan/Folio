# Research

Research starts when the person asks for a report in the thread, or when a schedule the person told the agent to run writes a report.

The person tells the agent to name, rename, or delete a report, and the agent does so. One account can keep several reports. Each run writes one report. Deleting a report leaves other files, threads, and Drive copies.

A run may read the account's files, Drive copies, and the public web. It may open at most 20 sources, then it writes the report from those sources. It may work for at most 30 minutes, then it writes the report from what it has. Time spent waiting on the person does not count.

A ready report keeps the list of pages and account files it read. That list commits with the text and the ready status. The person sees that list when the report is ready. Until then the app shows that the run is in progress, or the question it is waiting on.

Research reads. A send on a website still requires the confirmation in `docs/product/confirmations.md`.

When an answer is missing, the run waits and the question stays in the app.

The person tells the agent to cancel a run while it is running or waiting, and the agent cancels it. Cancelling discards the partial text. The last ready report stays when one already exists.
