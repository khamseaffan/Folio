# Models

The platform holds the provider keys. A person does not bring, store, or replace a key.

The keys stay envelope-encrypted with Cloud KMS.

Chat, research, and scheduled runs use one model. The person cannot change it. The current model is Gemini.

The model call goes through one internal interface. Gemini is the first implementation. Another provider is a new implementation of that interface. The loop stays the same. Google ADK is not that interface, and it is not the loop.

Every model call writes a usage record: the account email, the time, the provider, the model, the input tokens, the output tokens, the total tokens, and the expected cost of that provider and model. When the provider returns no usage, the record says the usage is missing. A browser task's page text and screenshots are part of those calls. A transcription is one of those calls. A chat turn counts only through the tokens on its usage record.

The person sees that account's recorded input tokens, output tokens, and total tokens in settings, and the file byte count in `docs/product/files.md`. The expected cost is not shown to them. Detail of the screens is in `docs/product/screens.md`.

The usage view is the total for one email, and the split of that total by provider and model, including the expected cost.

Each account has a token cap of 10 million total tokens. The cap does not reset. `khamseaffan@gmail.com` can read usage, raise the cap for one account, and stop an account. The cap is the sum of recorded total tokens. A record whose usage is missing adds nothing.

An account may have more than one run at a time while its recorded total is inside its token cap and its file count is inside 5 GB.

Reaching the cap stops the account. A stopped account sees the line "You have reached the allowed usage limit." The line has no usage figures. The current call is aborted. The partial reply is kept. Queued runs do not start. A stopped account cannot start a chat turn, a research run, or a scheduled run.
