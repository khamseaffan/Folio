# Hand-in report

Draft version 1. The site is not deployed yet, and there is no live URL. This is the design I intend to build.

## Service overview

Sakana is a private assistant for one person. You receive an invite, sign in, and talk to it the way you would talk to someone who remembers your work. The conversation is a set of threads. A reply appears as it is written, and you can stop it. What you already said stays in the thread. A message has no length limit.

The assistant uses one model, Gemini. You do not choose a different one, and you do not paste in your own key. I pay for the model. I made it a single model so the product is one thing to run, and so a key never has to live in the browser.

Who can join is limited on purpose. My email is the first account. After that, an account has to be an address at sakana.ai, and someone who already has an account has to invite you. They can type any email. If it is not allowed, the invite is refused right then, with a short message, so they find out while they are sending it. Sign-in is Google or a magic link that works once and lasts a day. There is no password. You can end your own session. If you delete the account, it is gone immediately and it cannot be restored.

I am building this for about 25 people, with enough room for a few hundred, in one region.

The assistant is written for this project. The path from your message to the reply is ordinary code in the API and in a background worker. I did this because the assignment asks for an original agent, and because a service this small should be something I can read from end to end.

When a thread gets long, the model still sees your recent exchange: at least your last ten messages and the last ten replies, plus as many newer ones as fit. Older messages stay saved. They are folded into a short summary that the model can see and you cannot. You are never told that a message was too long.

### Deep research

A research report is a different piece of work from a chat reply. You ask for it in the thread, or you ask the assistant to produce one on a schedule. The work then runs in the background. You can close the page. When it is finished, the report is there to open and download.

During the run it can read your files, the Google Drive files you have already imported, and public pages. It stops after 20 sources, or after 30 minutes of real work, and writes the report from what it has. If it needs an answer from you, the clock pauses and the question stays in the thread. Nothing is sent out of the account while it waits.

Each run writes one report, and you can keep several. A finished report includes the pages and files it actually used, saved together with the writing, so you can see where it came from. Until that moment the screen shows that the work is still going, or shows the question it is waiting on. If you cancel, the unfinished writing is discarded and the last finished report remains.

You download the file that run produced. It may be Markdown, a PDF, or another file the task needed. The research itself only reads. If the work would submit something on a website, you still confirm that exact action.

I took research off the chat request because reading the web and writing a long piece should continue after you leave. The limit of 20 sources and 30 minutes is there so a run has an ending. Saving the sources with the text is there so a finished report can be checked.

## Technology and architecture

You use a website. The pages are a Next.js app, hosted on Vercel. Behind them is a Python API on Cloud Run. A short conversation is answered on that same request, so a reply does not wait for a background job to start.

Research, and anything you put on a schedule, goes on a queue. A worker picks the job up. That worker calls Gemini, reads public pages, and can drive Chrome when the task needs a browser.

Your account is stored in Postgres. Your files are stored in Cloud Storage, each person under their own folder. Sign-in is handled by Firebase, and Firebase only handles sign-in. The threads, the reports, and the files are in Postgres. Google Drive is a read-only import: you pick one file in Google's picker, and Sakana keeps a private copy from that moment. A later change in Drive waits until you import the file again.

The model keys and the Drive connection are encrypted with Cloud KMS. Ordinary application secrets sit in Secret Manager. The database has a private IP, so it is not on the public internet. The scheduler calls the worker with Google's own identity. All of this Google Cloud estate is in us-central1, and Terraform is how it is described. GitHub Actions runs the tests.

If Gemini is unreachable, a queued job tries again three times and then stops. I can inspect the failed job. You see one line in the thread saying that the run failed.

I split the site from the data because the assignment asks for a React app and for Google Cloud. Vercel fits the website. The records, the files, and the jobs stay together in one region. Chat stays on the request because a conversation should feel immediate. Research goes on a queue because it can take half an hour and should survive a closed tab.

## Additional features

The assignment asks for two features of my own. I chose a lasting memory, and work that runs when you are away.

Memory is a set of notes in English, kept as Markdown. One note is your profile, how you like to work, and the assistant reads it on every call. The other notes are found by the words in them. A single call opens at most five of them, besides the profile. When you say something worth keeping, the assistant writes a short fact into the note where it belongs. You can read the notes in a panel beside the chat. You change them, or delete them, by saying so.

A schedule is also something you say. You might say that you want a check every morning. There is no form to fill in. The assistant creates the schedule, changes it, and removes it. It belongs to the thread you were in, so if that thread is deleted, the schedule goes with it. At the chosen time it does the task you described. It can use your files, your imported Drive copies, the web, and a way of working it has already learned, when the task needs them. The result is a message in the thread, or a report. If that same task is still running, or still waiting for you, the next occurrence is skipped. A different task is allowed to run at the same time.

A few things sit on top of those two, and the assignment does not require them.

The assistant can remember a way of working. You teach it in the conversation, and you tell it when to drop the habit. It is not a settings page you have to maintain.

It can open a web page in Chrome on the server. Reading the page does not ask you for anything. Sending a form, a post, or a payment shows you a card with the site and the exact fields. It sends only if you confirm that card. The card stays in the thread while you keep talking, and it expires after 24 hours. A different form, or a different amount, needs a new card.

When the result of a task is a file, the assistant writes that file and you can download it.

In settings you can see how many tokens the account has used, and how much file space. The account stops at 10 million tokens unless I raise the number for that account. Uploads and files the assistant writes stop at 5 gigabytes. One upload stops at 50 megabytes.

The first screen set is the chat, the reports, the files, a profile panel, and settings.

I added the browser because some tasks are finished on a website, and I did not want that send to happen on a vague yes. The caps are there so a small service has a limit I can see and raise. The remembered ways of working stay in the conversation because that is where you already explain how you want the job done.

## Omissions

These are later. None of them are required by the assignment. Adding one of them later still leaves one email as one private account.

One account with several named assistants. The assistant choosing a name for a new thread on its own. Searching old threads, and editing a message you already sent. Letting the model write new code and run it. Remembering a form so the same form can be sent again, with code deciding the match and with an expiry. Connections other than Google Drive. A full export of an account before it is deleted. Grouping accounts into an organization.

Two choices are narrower than the words in the assignment might suggest. Drive is one file at a time, imported as a private copy, and Sakana does not write back into Drive. Voice is a way to dictate a message: you speak, the words land in the composer, you send them as a normal chat message, and the recording is discarded. The assistant does not speak back.

## Quality assurance

A change reaches the main branch through a pull request. GitHub Actions runs the Python checks, Ruff and pytest, and the website checks, lint and typecheck. The same checks run on my machine before a commit. The main branch is protected.

The tests I will insist on are the ones I do not want to leave to the model's judgment. A send on a website happens only when your card matches that site and those fields, and only while the card is inside 24 hours. A file that is over 50 megabytes, or that would push the account past 5 gigabytes, is refused. A research run stops at 20 sources and at 30 minutes. Delivering the same job twice does not create two reports. A report is marked ready only when the writing and the source list are saved together. A schedule does not start a second copy of itself while the first is still going. Deleting a thread deletes its schedules. Deleting an account deletes that person's data and leaves the platform's model keys in place. If a model call comes back with no usage figure, that fact is stored as missing, and it is not written down as zero.

Before I submit the live URL, I will use the deployed site myself: sign in, hold a conversation, ask for a research report, set a schedule, upload a file, and import one file from Drive.

## Monitoring and logging

This is the design for the running service. It is not switched on yet.

Logs from the API and the worker go to Cloud Logging. A log line names the account, the run, and the operation. It leaves out the conversation, the voice transcript, the contents of a file, the prompt, any key, the Drive connection, a browser cookie, and a magic link. I want to be able to see why a job failed without keeping a second copy of someone's private account.

Cloud Monitoring tells me when the API is failing, when the worker is failing, when a run has given up after three tries, and when the database is unavailable. You already see a short line in the thread when your run fails, and a plain notice if the database is down. The list of failed runs is for me to read.

Every model call also writes a usage row: when it happened, which model, how many tokens, and what I expect it to cost. That row is how I watch the bill, and how I decide to raise one account's limit. I am not adding a separate analytics product. For this size of service, the request log, the job, and the token row are enough.

## Security

You cannot open an account on your own. Someone invites you, and the email has to be on the allowlist. There is no password. Google, or a link that works once, proves the address. A session ends after half an hour if both you and the assistant have gone quiet, and an account can have two sessions at a time.

Your threads, files, and notes belong to your account. I can see usage, raise a token limit, and stop an account. I cannot open another person's threads, files, or notes. If I stop an account, the screen says a system admin stopped it and shows my email.

The model keys and the Drive connection are encrypted with Cloud KMS. Cookies that keep the browser signed in to a site are encrypted the same way, and they are deleted with the account. The database is on a private IP. The scheduler calls the worker as Google, rather than with a password I would have to pass around.

The action that leaves the account is a send on a website. The model may suggest it. The code performs it only for the card you confirmed, for that site and those fields, within 24 hours. Reading a page, reading a file, saving a file inside your account, and reading an imported Drive copy do not ask. If a task would cause physical harm, the assistant stops and asks you first.

I kept sign-in in Firebase and the work in Postgres so the login system is not also the place where your notes live. I tied a send to one card so an agreement on one form is not treated as agreement to the next one.

## Appendix

Settings shows token counts, not a price. I still store the expected cost of each call, so I can compare it with the provider's bill.

Postgres holds the account. I am not adding a separate cache for this version.

The tables and the indexes are the next design step. They are not part of this draft.
