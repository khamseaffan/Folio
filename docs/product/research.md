# Research

Research starts when the person asks for a report in the thread. The agent makes sure it understands the question, then starts the run and says so. A scheduled task can start research too. It starts one run in its own thread, and it cannot start another while one is queued or working there. A person in the chat is not held by that limit.

The person tells the agent to rename or delete a report, and the agent does so. The person downloads the file that run wrote. The file is Markdown. One account can keep several reports. Each run writes one report. Deleting a report leaves other files and threads.

A run searches the web, opens public pages in Chrome, and reads the account's knowledge files when the question needs them. It never sees a whole page. A separate model call reads each page and writes a digest, and the run works from the digests. An opened file or page is a source. A run can open only an address that a search result or an opened page showed it.

It opens at most 20 sources, makes at most 25 searches in an attempt, and takes at most 60 steps. The 20 is a ceiling for cost and time, not a target. The reasons are in `docs/superpowers/specs/2026-11-15-deep-research-design.md`. A page that cannot be opened does not count. A page that disagrees is kept. A page that only repeats a page already opened is not opened as another source.

The run stops when it has opened 20 sources, when the searches or the steps run out, when reading time reaches 25 minutes, or when new pages mostly repeat the ones already opened. Code enforces the counts and the time. The model decides that new pages mostly repeat, and the stop note says so. When code stops the run, one last call writes the report from what was read, and the stop note ends with a line from code that says why. The run is 30 minutes of work in total, across retries. Reading stops at 25 minutes so that the report can be written inside the 30. A run does not stop to ask a question. The agent asks before it starts.

The file contains the question, the report text, a few lines on how the run stopped, and the source list. Each source has its title, its address or file name, and one line on what it was used for. The titles and addresses come from what the run opened, not from the model. That list commits with the text and the ready status. The thread shows a line when the report is ready, and the Reports screen lists it. Until then the thread shows that the run is working.

When the account reaches its token cap, the run stops reading and makes one last call to write the report from what it has, and the stop note says the usage limit was the reason. When the admin stops the email, the run is cancelled at once, no report is written, and the thread shows the stopped line.

A run that opens no source writes no report. The run fails and the thread shows "I could not open any source for this question." It is not tried again.

Research reads. A send on a website still requires the confirmation in `docs/product/confirmations.md`.

The person tells the agent to cancel a run while it is queued or working, and the agent cancels it. Cancelling discards the partial text and writes no report. The last ready report stays when one already exists.
