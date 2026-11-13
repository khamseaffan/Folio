# Voice

Speech is an input. It is uploaded and transcribed with the platform key. `khamseaffan@gmail.com` selects that provider and model. The starting model is Gemini. That call writes a usage record and counts toward the token cap in `docs/product/models.md`. Missing usage is stored as missing.

The transcript appears in the composer. It becomes a chat message when the person sends it, and then it enters the thread.

The audio is discarded once the transcript exists. It is not stored and it is not part of the 5 GB count.
