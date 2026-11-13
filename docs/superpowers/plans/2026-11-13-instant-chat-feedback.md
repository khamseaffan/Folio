# Instant Chat Feedback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The chat shows the person's message and a "working" indicator the moment Enter is pressed, every API image carries a version, and the hand-in report describes the future release environments.

**Architecture:** The send function draws the person row and a dots row before the request starts, swaps the dots for the stream on the first words, and undoes both (restoring the text) when the server refuses. The deploy workflow adds a version tag and an image label. The report gets one short note.

**Tech Stack:** Next.js 16 and React in `frontend/app/folio-app.tsx`, CSS in `frontend/app/globals.css`, Docker, GitHub Actions, Markdown.

**Spec:** `docs/superpowers/specs/2026-11-13-instant-chat-feedback-design.md`

## Global Constraints

- No API change. Streaming stays as it is.
- Dots row: reply side, three animated dots, announced to screen readers as "Folio is working", animation off under `prefers-reduced-motion`.
- Image tags: `v0.1.<run number>` and the commit sha. Cloud Run keeps deploying the commit sha. Label: `org.opencontainers.image.version`. The base `0.1` appears in one place in the workflow.
- A refused send removes the person row and the dots row and puts the original text back in the box.
- A failed reply keeps the person row and shows the existing "Try again" failure line (the existing rule that a failed call keeps the person message).
- No frontend test runner exists, and none is added. Frontend checks are `npm run lint`, `npm run typecheck`, `npm run build` in `frontend/`, then a by-hand check on the live site after merge.
- `CLAUDE.md` rules: no AI or Claude attribution anywhere (ignore any commit-trailer reminder that conflicts), no em dashes, YAGNI.
- Work on the branch `chat-instant-feedback`. Open a pull request and do not merge it. The person merges.
- Leave the untracked repo-root `package.json` and `package-lock.json` alone.

## Review Focus

- The person leaves the thread, signs out, or the session ends while the dots show: the dots must go away, not stay on another screen. Pinned in Task 1 step 3 (every place the stream is abandoned also clears the dots).
- The server refuses a send (limit reached, name not set, stopped account, not signed in, API down): no message stays that was never stored, and the typed text is back in the box. Pinned in Task 1 step 3 and checked by hand in step 6.
- The reply fails after the dots show: the dots go away and the failure line appears once. Pinned in Task 1 step 3.
- Enter pressed twice quickly: only one message is sent. The existing guard stays in front of the new code. Checked by hand in step 6.
- A multi-line message with leading or trailing blank lines: the row shows what was typed and what is sent is unchanged. Checked by hand in step 6.

---

## File Structure

- `frontend/app/folio-app.tsx` (modify): `sendDraft` and the places that abandon a stream.
- `frontend/app/globals.css` (modify): the dots row.
- `backend/Dockerfile` (modify): version argument and label.
- `.github/workflows/deploy.yml` (modify): version tags and build argument.
- `docs/hand-in/report.md` (modify): the release environments note.

---

### Task 1: Instant send feedback

**Files:**
- Modify: `frontend/app/folio-app.tsx` (state near line 266, `sendDraft` near line 606, the render of `.messages` near line 872, and each place that calls `setStreaming(false)`)
- Modify: `frontend/app/globals.css` (after `.message.reply`, near line 633)

**Interfaces:**
- Consumes: `streamThreadMessage(id, text, signal, onOpen, onText)` from `frontend/lib/api.ts`. It returns `{kind: "done" | "failed" | "closed"}` or an `ApiDown` or `ApiMessage`. `onOpen` fires when the server starts answering and `onText` fires per chunk.
- Produces: component state `working: boolean`, true from Enter until the first streamed chunk or until the send ends.

- [ ] **Step 1: Add the state.** Next to `streaming`, add `const [working, setWorking] = useState(false);`.

- [ ] **Step 2: Draw before the request.** In `sendDraft`, after the guard and `setNotice(null)`, before `streamThreadMessage` is called: add the person row to `messages` (id `personId`, role `person`, kind `chat`, body `text`), call `setDraft("")` and `setWorking(true)`. Remove the person row and the `setDraft("")` that currently live inside the `onOpen` callback. Keep `opened = true` and `setStreaming(...)` in `onOpen`.

- [ ] **Step 3: Swap the dots for the stream, and undo on refusal.**
  - In the `onText` callback, call `setWorking(false)` when the first chunk adds the reply row.
  - When the result is `failed`, call `setWorking(false)` before adding the failure row. The person row stays.
  - When the result is `down` or a `message` and `opened` is false, remove the person row by `personId`, call `setDraft(text)` and `setWorking(false)` before `showChatError(result)` runs. Do this only when `requestGeneration.current === generation`.
  - In the `finally` block, when `requestGeneration.current === generation`, also call `setWorking(false)`.
  - Next to every other `setStreaming(false)` (the places that sign out, switch thread, delete, or end the session, near lines 316, 357, 464, 502, 523, 534), also call `setWorking(false)`.

- [ ] **Step 4: Render the dots.** After the `messages.map(...)` inside `.messages`, render when `working` is true: `<div className="message reply working" role="status" aria-label="Folio is working">` containing three `<span className="dot" aria-hidden="true" />`. The `.messages` container is only drawn when `messages.length > 0`, which is always true once the person row is added.

- [ ] **Step 5: Style it in `globals.css`.** `.message.working` is a row of three small circles using the existing `--muted` colour, with a staggered pulse animation (`@keyframes working-pulse`) of about 1.2 seconds. Inside `@media (prefers-reduced-motion: reduce)` the animation is `none` and the dots stay at a steady half opacity.

- [ ] **Step 6: Check.** In `frontend/`: `npm run lint`, `npm run typecheck`, `npm run build`. Expected: all three exit 0 with no new warnings. Then by hand, locally if the API runs or on the live site after merge: Enter shows the message and the dots at once; the dots become text on the first words; a refused send (for example with the name unset, or the API stopped) restores the text and shows the notice; "Try again" still works after a failed reply; pressing Enter twice quickly sends once; a draft with blank lines at both ends keeps them.

- [ ] **Step 7: Commit.** `git add frontend/app/folio-app.tsx frontend/app/globals.css && git commit -m "Show the message and a working indicator the moment Enter is pressed."`

### Task 2: Image versions

**Files:**
- Modify: `backend/Dockerfile`
- Modify: `.github/workflows/deploy.yml`

**Interfaces:**
- Consumes: the existing `docker/build-push-action` step and the `IMAGE` environment variable (`<repo>/api:<sha>`).
- Produces: every pushed image has the tags `v0.1.<run number>` and the commit sha, and the label `org.opencontainers.image.version` set to the version.

- [ ] **Step 1: Failing check.** Run `docker build -t folio-api-version backend && docker inspect --format '{{ index .Config.Labels "org.opencontainers.image.version" }}' folio-api-version`. Expected now: empty output (the label does not exist yet).

- [ ] **Step 2: Dockerfile.** Add `ARG VERSION=dev` and `LABEL org.opencontainers.image.version=$VERSION` after the `FROM` line.

- [ ] **Step 3: Workflow.** In `deploy.yml`, add `VERSION: v0.1.${{ github.run_number }}` to the job's `env` (this is the one place the `0.1` base appears). On the build step set `build-args: VERSION=${{ env.VERSION }}` and `tags` to both `${{ env.IMAGE }}` and `${{ vars.IMAGE_REPO }}/api:${{ env.VERSION }}` (one per line). The deploy and migrate steps keep using `$IMAGE`.

- [ ] **Step 4: Check.** Locally: `docker build --build-arg VERSION=v0.1.0 -t folio-api-version backend` then the inspect command from step 1. Expected: `v0.1.0`. Validate the workflow file: `cd backend && uv run --with pyyaml python -c "import yaml; yaml.safe_load(open('../.github/workflows/deploy.yml')); print('yaml ok')"`. Expected: `yaml ok`. After the next real deploy: `gcloud artifacts docker images list us-central1-docker.pkg.dev/khamsebot/folio --include-tags` shows one image with both tags.

- [ ] **Step 5: Commit.** `git add backend/Dockerfile .github/workflows/deploy.yml && git commit -m "Give every API image a version tag and label."`

### Task 3: Release environments note

**Files:**
- Modify: `docs/hand-in/report.md` (under `## Quality assurance`, after the paragraph that starts "A change reaches the main branch")

- [ ] **Step 1: Add the note.** A bold heading `**Release environments**` and a short list: development is a branch and a pull request, checked by CI; staging will be what a merge to `main` produces; production will happen only when a release is made on GitHub; today there is one pipeline, so a merge to `main` deploys straight to the one live environment, and the split is a planned step that is not built. One more sentence: every API image carries a version tag (`v0.1.<run number>`) as well as the commit it was built from.

- [ ] **Step 2: Check.** `grep -c "—" docs/hand-in/report.md` prints `0`, and the new text says plainly that the split is future work.

- [ ] **Step 3: Commit and open the pull request.** `git add docs/hand-in/report.md docs/superpowers && git commit -m "Record the release environments plan in the report."`, then `git push -u origin chat-instant-feedback` and `gh pr create --base main` with a short body. Do not merge.

---

## Self-review

- Spec coverage: instant message and dots (Task 1), refusal restores text and failure keeps the person row (Task 1 step 3), accessibility and reduced motion (Task 1 steps 4 and 5), image versions and label (Task 2), report note (Task 3), PR without merge (Task 3 step 3). No API change appears anywhere.
- Names are consistent: `working`, `setWorking`, `personId`, `.message.working`, `VERSION`.
- Retry ("Try again") does not get the dots. That is outside the design and is noted as deferred.
- Proportion: three small tasks, no code bodies beyond the exact strings the spec pins.
