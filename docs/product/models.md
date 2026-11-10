# Models

The platform holds the provider keys. A person does not bring, store, or replace a key.

The keys stay envelope-encrypted with Cloud KMS.

Chat, research, and scheduled runs use one model. The person cannot change it. The current model is Gemini.

The model call goes through one internal interface. Gemini is the first implementation. Another provider is a new implementation of that interface. The loop stays the same. Google ADK is not that interface, and it is not the loop.

Every model call writes a usage record: the account email, the time, the provider, the model, the input tokens, the output tokens, the total tokens, and the expected cost of that provider and model. When the provider returns no usage, the record says the usage is missing.

The usage view is the total for one email, and the split of that total by provider and model.

`khamseaffan@gmail.com` can read usage and stop an account.

A stopped account sees the line "You have reached the allowed usage limit." The line has no usage figures. The current call is aborted. The partial reply is kept. Queued runs do not start. A stopped account cannot start a chat turn, a research run, or a scheduled run.
