# Models

The platform holds the provider keys. A person does not bring, store, or replace a key.

The keys stay envelope-encrypted with Cloud KMS.

Chat, research, scheduled runs, the browser, and memory use one platform model at a time. `khamseaffan@gmail.com` selects the provider and the model in settings. Another account cannot change them and does not bring a key. The starting selection is provider Google and model `gemini-4-argon`, with thinking level medium and a context window of 1,048,576 tokens. A later selection uses that model's own thinking control, context window, and published price. Expected cost uses the published price for the selected provider and model. The usage row stores the provider, the model, and the token counts that provider returns.

The call goes through a LangChain chat model. LangGraph is the loop in the Python API and the worker. The website does not call the model. The graph, the tools, and the memory stay the same when the provider or the model changes. Google ADK is not the loop. The Vercel AI SDK does not call the model.

Every model call writes a usage record: the account email, the time, the provider, the model, the input tokens, the output tokens, the total tokens, and the expected cost of that provider and model. When the provider returns no usage, the record says the usage is missing. A browser task's page text and screenshots are part of those calls. A transcription is one of those calls. A chat turn counts only through the tokens on its usage record.

The person sees that account's recorded input tokens, output tokens, and total tokens in settings, and the file byte count in `docs/product/files.md`. The expected cost is not shown to them. Detail of the screens is in `docs/product/screens.md`.

The usage view is the total for one email, and the split of that total by provider and model, including the expected cost.

The token cap and the stop belong to the email. The default cap is 10 million total tokens. The cap does not reset. `khamseaffan@gmail.com` can read usage, raise the cap for one email, and stop an email. The cap is the sum of recorded total tokens for that email, including rows kept after the account was deleted. A record whose usage is missing adds nothing. Deleting the account leaves the usage rows, the cap, and the stop. A new account for that email keeps them.

An account may have more than one run at a time while its recorded total is inside its token cap and its file count is inside 5 GB.

Reaching the cap stops the account. That account sees the line "You have reached the allowed usage limit." The line has no usage figures. When `khamseaffan@gmail.com` stops an email, that account sees the line "A system admin has stopped your account. Contact khamseaffan@gmail.com." The stop stays with the email. The current call is aborted. The partial reply is kept. Queued runs do not start. A stopped account cannot start a chat turn, a research run, or a scheduled run.
