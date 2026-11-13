# Instant chat feedback, image versions, and release environments

Small change. Date: 2026-11-13.

## Intent

The first message of a session feels slow, because the chat waits for the API to start answering before it shows anything. People should see their message and a sign that the system is working the moment they press Enter. Every container image should carry a version. The hand-in report should say how development, staging and production will be separated later.

## Goals

- Press Enter and the person's message appears and the box clears at once, with no wait on the API.
- A "working" indicator (three animated dots) appears on the reply side at once, then is replaced by the streamed text on the first words.
- Every API image has a version tag and an image label.
- The hand-in report has a "Release environments" note.

## Non-goals

- Any API change. Streaming already works.
- Warming the API or any server-side change for cold starts. This change hides the delay, it does not remove it.
- Building the staging and production split. It is only written down as a plan.
- A chat redesign, a Stop button before the reply starts, or message editing.

## Anti-goals

- Losing the person's text when the server refuses a message.
- A message shown as sent when it was never stored.

## Approaches considered

- **A. Optimistic UI in the existing send function (chosen).** Draw the message and the dots before the request, and undo them on refusal. Smallest change, no API change.
- **B. Server-sent early acknowledgement.** The API would send a first byte at once. It does not help when the API itself is cold, which is the slow case.
- **C. Keep the current flow and add only a spinner on the button.** Cheapest, but the message still does not appear at once.

## Design

### Chat (`frontend/app/folio-app.tsx`, `sendDraft`)

- On Enter: add the person message, clear the draft and add a "working" reply row immediately, before the request starts. These use the same ids the stream uses today.
- First streamed chunk: the working row becomes the reply row and shows the text.
- The server refuses the send (limit reached, account stopped, name not set, not signed in): remove the person row and the working row, put the original text back in the box, and show the existing refusal notice.
- A failed reply: remove the working row and show the existing "Try again" failure line.
- The person stops or leaves while waiting: the working row is removed and a partial reply is kept as it is today.
- The working row is a reply row with three dots, labelled "Folio is working" for screen readers. The animation stops when the person prefers reduced motion.
- Send stays disabled while a message is in flight, as now.
- The generation counter already guards against late results from an earlier send. The new rows follow the same guard.

### Image versions (`.github/workflows/deploy.yml`, `backend/Dockerfile`)

- Each deploy pushes the image with two tags: `v0.1.<run number>` and the commit sha. Cloud Run keeps deploying the commit sha, so what runs is still traceable to one commit.
- The Dockerfile takes a `VERSION` build argument and sets the label `org.opencontainers.image.version`.
- The base version `0.1` lives in one place in the workflow. When releases exist, the GitHub release tag becomes the version.

### Report (`docs/hand-in/report.md`)

A "Release environments" note under Quality assurance:

- Development is a branch and a pull request, checked by CI.
- Staging will be what a merge to `main` produces.
- Production will happen only when a release is made on GitHub.
- Today there is one pipeline: a merge to `main` deploys straight to the one live environment. The split is a future step, not built.

## Tests

- No frontend test runner exists. The behavior is checked by lint, typecheck and build, and then by hand on the live site: Enter shows the message and dots at once, the stream replaces the dots, a refused send restores the text, and Try again still works.
- The workflow change is checked by reading the next deploy: both tags exist in Artifact Registry and the label is on the image.

## Left to the builder

The exact dots animation, the class names and the wording of the screen-reader label, within the rules above.
