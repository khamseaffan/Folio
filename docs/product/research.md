# Research

Research starts when the person asks for a report in the thread, or when a schedule the person told the agent to run writes a report.

The person tells the agent to name, rename, or delete a report, and the agent does so. The person downloads the file that run wrote. That file is Markdown, PDF, or another type the task needs. One account can keep several reports. Each run writes one report. Deleting a report leaves other files, threads, and Drive copies.

A run opens the account's files and imported Drive copies when the question needs them, then public pages. An opened file or page is given to the model as that file or page. Detail of that read is in `docs/product/files.md`. It follows a citation only when an opened page points at a source the report still needs.

It opens at most 20 sources. That number is a ceiling. A page that cannot be opened does not count. A page that disagrees is kept. A page that only repeats a page already opened is not opened as another source.

The run stops when it has opened 20 sources, when 30 minutes of work have passed, or when new pages mostly repeat the ones already opened. Code enforces the count of 20 and the 30 minutes. The model decides that new pages mostly repeat, and the stop note says so. It then writes the report from those sources. Time spent waiting on the person does not count.

The file contains the question, the report text, a few lines on how the run stopped, and the source list. Each source has its title, its address or file name, and one line on what it was used for. That list commits with the text and the ready status. The person sees that list when the report is ready. Until then the app shows that the run is in progress, or the question it is waiting on.

Research reads. A send on a website still requires the confirmation in `docs/product/confirmations.md`.

When an answer is missing, the run waits and the question stays in the app.

The person tells the agent to cancel a run while it is running or waiting, and the agent cancels it. Cancelling discards the partial text. The last ready report stays when one already exists.
