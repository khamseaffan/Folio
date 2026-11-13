# Stage 1. Account

An allowed email signs in, a session exists, and ending it is logging out. Decisions D1 to D14 and questions Q1 to Q4 are in `docs/plan/decisions.md`.

## Rules for every unit

- A unit that creates a table first pitches its migration to the person and waits for approval.
- Anything the schema picture lacks is named and stopped on, not added.
- "Creates" lists the tables a unit makes. "Writes" lists the tables it changes.
- Tests that depend on time use the clock from 1.a.

## 1.a Local database, clock, ready check

- True when done: the API connects to a local Postgres (D2). A ready check says up or down. When Postgres is down, every route except health answers with "the system is down and repair is underway". The code has one injectable clock. The existing `ci.yml` gets a Postgres service (D10).
- Depends on: nothing.
- Creates: none. Writes: none.
- Test: pytest against real Postgres, with a test database made and dropped per run. Stopping Postgres flips the check to down.
- Must not include: a migration tool, any table, other CI changes.

## 1.b Sign-in proof and allowlist

- True: one interface turns a sign-in attempt into a verified lowercase email, or the line "This email is not an allowed account." The local stand-in (D1) is its only implementation, and it is off unless a local setting turns it on. Allowed: khamseaffan@gmail.com, or exactly an address at sakana.ai.
- Depends on: nothing.
- Creates: none. Writes: none.
- Test: mixed case and plus-addresses pass. Refused with the exact line: evilsakana.ai, sub.sakana.ai, sakana.ai.evil.com, two @ signs, empty. With the setting off, everything is refused.
- Must not include: passwords, the Firebase SDK, magic link state.

## 1.c Account and email limit

- True: the first allowed sign-in makes the email_limit row (cap 10000000, stopped false) and the account (lowercase email, firebase_uid `local:{email}`, empty display name) in one save. A later sign-in finds the same account. The migration tool is installed here (D14).
- Depends on: 1.a, 1.b.
- Creates: email_limit, account. Writes: both.
- Test: a second sign-in returns the same id. Concurrent first sign-ins make one account. The database refuses an account with no email_limit row, and refuses deleting an email_limit that has an account.
- Must not include: sessions, name rules, deletion, routes.

## 1.d Sessions with the two-session lock

- True: a sign-in makes a session. The account row is locked first. Then sessions silent for over 30 minutes are ended, the live ones are recounted, and if two remain the oldest is ended. The new session is inserted in the same save.
- Depends on: 1.c.
- Creates: session. Writes: session.
- Test: three sign-ins end the oldest. Two concurrent third sign-ins never leave more than two live. Expired sessions do not count.
- Must not include: cookies, HTTP.

## 1.e Session gate

- True: every route except health and sign-in needs a live session (D3). The gate loads the account, refuses an ended session or one silent over 30 minutes (and marks it ended), and moves `last_active_at` when the person acts. A helper moves it when the assistant acts. A state-changing request must come from the web app's origin (D11). A separate admin check passes only a live session whose email is khamseaffan@gmail.com.
- Depends on: 1.d.
- Creates: none. Writes: session.
- Test: the test file defines its own throwaway route, so no demo route ships. Alive at 29 minutes, dead at 31. Person and assistant activity both extend it. A session never resolves to another account. A wrong origin is refused.
- Must not include: stop or cap checks.

## 1.f Sign-in, sign-out, sessions routes

- True: sign-in (refusal line or new session), sign-out, list my sessions, end one of my sessions. Another account's id is "not found". Ending a session is logging out.
- Depends on: 1.e.
- Creates: none. Writes: session.
- Test: after sign-out the cookie is refused. Ending my other session leaves this one live. Ending another account's id changes nothing.
- Must not include: name, deletion, UI.

## 1.g Display name and account deletion

- True: the display name is required, 1 to 40 characters. Until it is set, chat answers "set your name" (D12). Deleting the account is immediate, in one save. It removes the account and its sessions and leaves email_limit. The step that removes the Firebase sign-in is a no-op behind the 1.b interface.
- Depends on: 1.f.
- Creates: none. Writes: account, session.
- Test: name lengths 0, 1, 40, 41. After deletion, a new sign-in keeps the raised cap and the stop.
- Must not include: deleting threads or usage. Those are checked in 2.a and 2.c.

## 1.h Admin limits

Can wait, but is needed before sign-off.

- True: khamseaffan@gmail.com can raise the cap for one email and stop one email. An email with no email_limit row is refused as unknown. Everyone else is refused.
- Depends on: 1.f.
- Creates: none. Writes: email_limit.
- Test: a sakana.ai account is refused. Raising one cap changes no other row. Admin identity is the verified email, never a display name.
- Must not include: usage reading (2.j), any thread read, UI.

## 1.i0 Frontend checks

- True: a frontend test runner is chosen and installed (D14), and `npm test` is added to the existing frontend CI job. A lint rule blocks any import of a database package in client code.
- Depends on: nothing.
- Creates: none. Writes: none.
- Test: one sample test passes. A file with a forbidden import fails lint.
- Must not include: page work.

## 1.i Frontend: public page, sign-in, sign-out, shell

- True: the Next.js starter is replaced. The public page signs in and shows the refusal line. After sign-in the app opens with a chat area, a settings link, and a sign-out button. The person is asked for a display name. The popup text from 1.a shows when the API says it is down. English. A narrow window works. The builder reads the Next.js guide in `node_modules` first, as `frontend/AGENTS.md` says.
- Depends on: 1.a, 1.f, 1.g, 1.i0.
- Creates: none. Writes: none.
- Test: typecheck, lint, build, and component tests. By hand: refusal line, allowed sign-in, sign-out, popup with the API stopped.
- Must not include: chat, settings contents, reports, files, profile. Stage 1 is true here.

## 1.j Frontend: settings

Can wait.

- True: settings edits the name, lists sessions with an end button, and deletes the account.
- Depends on: 1.i, 1.g, 1.f.
- Creates: none. Writes: none.
- Test: as 1.i.
- Must not include: token totals (2.k).
