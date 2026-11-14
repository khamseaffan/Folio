# Memory: knowledge files and abilities

Date: 2026-11-13. Builds on the operation layer in `2026-11-13-operations-and-management-design.md`.

## Intent

Folio remembers what a person tells it, and uses it in later conversations. `Goal.md` asks for two or more original features, and names long-term memory as one. The product rules are already written in `docs/product/profile.md` and `docs/product/abilities.md`. This design says how they are built.

## What the person gets

- They tell the agent a fact. The agent stores it in a Markdown file without asking.
- In any later thread the agent knows it.
- They can ask the agent to change a fact, delete a fact, or delete the whole profile.
- They can read every file, and every ability, in a profile panel beside the chat. The panel is for reading only.

## Goals

- Knowledge files and abilities stored per account.
- `profile.md` is part of every model call.
- The agent searches the other files by their words and opens at most five in one turn.
- The agent creates, changes, and deletes files and abilities through operations.
- A read-only profile panel in the website.

## Non-goals

- Schedules in the profile panel. Schedules do not exist yet.
- Reading a very long file in parts. A file is read whole.
- Search by meaning (embeddings). Search is by words.
- A control for the person to edit a file directly. The docs say the person changes memory by telling the agent.
- Cloud Storage copies of the files. The row is the file.

## Anti-goals

- The agent reading or changing another account's files.
- The model being handed every file on every call.
- Code deciding what is worth remembering, or which of two facts is right. The model decides. Code only limits and stores.

## The records

Two tables, as drawn in `docs/diagrams/schema.html`, with one difference: `knowledge_file` has no `storage_key`.

**`knowledge_file`**

| Column | Type | Note |
| --- | --- | --- |
| `id` | uuid | Primary key. |
| `account_id` | uuid | The owner. Deleting the account deletes the row. |
| `name` | text | Unique for the account. `profile.md` is the manner file. |
| `body` | text | The Markdown text. The model reads this. |
| `byte_size` | bigint | The size of `body` in bytes. |
| `created_at`, `updated_at` | timestamptz | |

Unique on (`id`, `account_id`) and on (`account_id`, `name`). An English full-text index on `body`.

**`ability`**

| Column | Type | Note |
| --- | --- | --- |
| `id` | uuid | Primary key. |
| `account_id` | uuid | The owner. Deleting the account deletes the row. |
| `name` | text | |
| `body` | text | |
| `created_at`, `updated_at` | timestamptz | |

Index on `account_id`.

## The operations

All are agent tools. None has a button. Each one works only on the caller's account.

| Operation | Input | Does |
| --- | --- | --- |
| `list_knowledge` | none | The names, sizes, and update times of the account's files. |
| `search_knowledge` | `query` | Up to five files whose text matches the words, best match first, each with a short piece of the matching text. |
| `read_knowledge` | `name` | The whole text of one file. |
| `write_knowledge` | `name`, `body` | Creates the file, or replaces all of its text. |
| `append_knowledge` | `name`, `text` | Adds text to the end. Creates the file when it is missing. |
| `edit_knowledge` | `name`, `find`, `replace` | Replaces one piece of text with another. `find` must appear exactly once in the file. |
| `delete_knowledge` | `name` | Deletes one file. |
| `delete_profile` | none | Deletes every knowledge file of the account. Abilities stay. |
| `list_abilities` | none | Every ability with its id, name, and text. |
| `write_ability` | `ability_id` (optional), `name`, `body` | Creates an ability, or replaces the one with that id. |
| `delete_ability` | `ability_id` | Deletes one ability. |

Rules in the operations:

- A file name is trimmed and is 1 to 80 characters. A null character is refused in a name or in text.
- `read_knowledge` opens at most five different files in one chat turn, not counting `profile.md`. A sixth is refused with a sentence the model can read. Reading the same file twice counts once.
- `profile.md` holds at most 20,000 characters. A write, an append, or an edit that would leave it longer is refused, the file stays as it was, and the answer is a sentence the model can read. Other files have no limit.
- `edit_knowledge` is refused when `find` is missing from the file or appears more than once. The answer says how many times it appeared.
- A refused call returns an error sentence, as the thread operations do. It is logged.

## The model call

- The system prompt gains the text of `profile.md`, when the file exists.
- The system prompt gains a short, fixed instruction about memory: store what the person tells you as short plain statements, without asking; search before you answer something about the person that you do not see; when two facts disagree, say so and say which one you believe should go, and delete only after the person agrees.
- No extra model call is made. Memory costs only the tokens of the tool rounds, which are recorded as usage already.

## The website

- A "Profile" control at the top of the chat opens the profile panel. In a wide window the thread and the panel sit side by side. In a narrow window the panel takes the place of the thread, with a control to go back.
- The panel has two lists: "Knowledge" and "Abilities". Choosing a knowledge file shows its text. An ability shows its name and text.
- The panel loads when it opens, and again when a chat turn ends, so a change by the agent shows.
- Three read routes serve it: `GET /knowledge`, `GET /knowledge/{id}`, `GET /abilities`. There is no route that writes memory.
- The working indicator names the memory tools, for example "Updating your notes".

## Questions and answers

**Why Markdown files, and not a table of facts?**
A file is something a person can read from top to bottom and trust. A table of separate facts needs a schema for every kind of fact, and the agent would have to fit what it hears into that schema. With files, the agent writes plain sentences under a name that says what the file holds, and the person reads exactly what the agent reads.

**Why is `profile.md` sent on every call, and the other files are not?**
`profile.md` holds how the person likes to be answered. That applies to every reply, so it must always be there. The other files hold facts that matter only sometimes. Sending all of them every time would cost tokens on every message and would grow without limit.

**Why is `profile.md` limited to 20,000 characters, when other files have no limit?**
`profile.md` is the only file sent on every model call, so its size is paid for on every message. Without a limit it could grow until it no longer fits in the model's context. Then every turn would fail, and the person could not even ask the agent to fix it, because the agent would never run. 20,000 characters is about 5,000 tokens. That is far more than a description of how someone likes to be answered needs, and it is small beside the model's context. Other files are opened only when needed, and at most five in a turn, so they do not carry the same risk. When the limit is reached the agent is told, and it can shorten the file or move facts into another file.

**Why at most five files in one turn?**
It is a cost limit that the person can understand. Without it, a broad question could make the agent open every file. Five is the number written in the product docs. Code enforces it, because a limit that only the model is asked to keep is not a limit.

**Why search by words, and not by meaning?**
Word search is built into Postgres. It needs no extra service, no extra model call, and no extra cost, and it is easy to explain: the file is found because it contains the words. The agent chooses the words, and it can search again with other words. Search by meaning needs an embedding for every file and a model call for every search. It can be added later as another operation without changing the files.

**Why does the agent have three ways to change a file?**
Each fits a different change, and the agent picks the one that is most accurate for the case.

- `append_knowledge` adds a new fact. It cannot damage what is already there.
- `edit_knowledge` changes or removes one fact. It touches only the piece it names, so the rest of the file cannot be lost. It is refused unless the piece appears exactly once, so it cannot change the wrong place.
- `write_knowledge` replaces the whole text. It is right for a new file, or for reorganising a file. It is the riskiest for a long file, because the agent must repeat everything it wants to keep.

The first design had only whole-file write and append. Find-and-replace was added so that a small change to a long file does not depend on the agent copying the whole file correctly.

**Why can the person not edit a file themselves?**
The product rule is that the person changes memory by telling the agent. One way of changing memory keeps one record of how each fact got there. A direct editor can be added later as a button on the same operations, because every operation can already be called by a route.

**Why is there no `storage_key`, and no copy in Cloud Storage?**
The text lives in the database row, and nothing reads a copy. The schema picture had a `storage_key` for a Cloud Storage path. No bucket exists yet, so the column would point at nothing. It is left out and can be added when files and uploads are built.

**Why are abilities addressed by id, and files by name?**
A file name is unique for the account, and it is how both the person and the agent talk about the file. The schema picture gives abilities no unique name, so two abilities may share one. The id is the only safe handle.

**Why does deleting the profile keep the abilities?**
That is the product rule in `docs/product/records.md`. Knowledge is about the person. An ability is a way of working. A person who wants a clean slate about themselves may still want the agent to keep the ways of working it learned.

**What stops the agent from writing something the person did not say?**
Nothing in code, on purpose. Deciding what was said is a matter of meaning, and the project rule is that the model handles meaning. What code guarantees is that the person can read every file in the panel, and that every change went through an operation that is scoped to their account and logged when refused.

## Approaches considered

- **A. Operations on rows, word search in Postgres (chosen).** Described above.
- **B. Embeddings and search by meaning.** Finds a fact even when the words differ. Rejected for now: an extra model call and cost on every write and search, and an extra store.
- **C. Put every file in every call.** No search needed. Rejected: the cost grows with every fact, on every message.

## Calls made by the designer, not the person

- A file name is 1 to 80 characters. The docs give no limit.
- A file is read whole. Reading in parts waits for a real need.
- The panel shows a file as plain text, not rendered Markdown. No new package.
- Abilities are addressed by id.
- The tool round limit stays at 8 for a turn.

## Left to the builder

- Function and file names not given in the plan, the exact wording of the memory instruction in the system prompt within the meaning above, and the visual details of the panel within the existing palette.
