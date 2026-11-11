# Profile

The product language is English.

Knowledge for one account is a set of Markdown files. `profile.md` holds manner and working preferences and is included in every model call. Other files are created when their name is the right place for the knowledge. The name states what the file holds. Examples are `answer-bank.md` and `tracker.md`. When no existing file fits, a new file is created with a name that states the knowledge.

There is no cap on how many files an account may have. A file has no tags. It is a title and the text.

A stored fact is a short declarative form of what the person said. It keeps the substance.

The assistant writes that fact into the fitting file without asking.

The model is not given every file. It searches the words in the files and opens at most five. `profile.md` is extra and always included.

The person can read the files. The person tells the agent to change a fact, delete a fact, or delete the profile, and the agent does so. Deleting the profile removes the knowledge files and cannot be restored. The account remains.

When two facts disagree, the assistant says so and names which one it believes should go. It deletes one only after the person confirms.

Deleting a thread does not delete these files.
