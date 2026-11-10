# Profile

The product language is English.

Knowledge for one account is a set of Markdown files. `profile.md` holds manner and working preferences and is included in every model call. Other files are created when their name is the right place for the knowledge. The name states what the file holds. Examples are `answer-bank.md` and `tracker.md`. When no existing file fits, a new file is created with a name that states the knowledge.

There is no cap on how many files an account may have. A file has no tags. It is a title and the text.

A stored fact is a short declarative form of what the person said. It keeps the substance.

The assistant writes that fact into the fitting file without asking. A hand edit saves immediately.

The model is not given every file. It searches the words in the files and opens at most five. `profile.md` is extra and always included.

The person can open the files, read them, edit them, delete one fact, and delete the whole profile. Deleting the profile removes the knowledge files and cannot be restored. The account remains.

When two facts disagree, the assistant says so and names which one it believes should go. It deletes one only after the person confirms.

Deleting a thread does not delete these files.

A grant is a database row: the kind in the person's words, whether it is active, and when it changed. The model cites a grant by id. Code allows the sensitive call only when that id exists, belongs to this account, and is active.

An ability is a Markdown file the model opens when it needs that ability. The file states what it does and which built-in tools it may use. Those tool names are not pasted into every call. Code checks the names when a tool runs. This build does not let the model write or run new code.
