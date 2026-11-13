"use client";

import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import {
  getReady,
  getSession,
  postDisplayName,
  postSignIn,
  postSignOut,
  type Account,
} from "../lib/api";
import {
  completeEmailLink,
  currentIdToken,
  emailLinkHref,
  readStoredEmail,
  sendEmailLink,
  signInWithGoogle,
  signOutFirebase,
} from "../lib/firebase-auth";

const SYSTEM_DOWN = "The system is down and repair is underway.";
const UNAVAILABLE = "Sign-in is unavailable.";
const LINK_SENT = "Check your email for a sign-in link. Open it in this browser.";

type View = "checking" | "signed-out" | "link-email" | "ask-name" | "ready";

type BootResult = {
  down: boolean;
  view: Exclude<View, "checking">;
  notice: string | null;
  status: string | null;
  displayName: string | null;
  linkHref: string | null;
};

const QUIET: BootResult = {
  down: false,
  view: "signed-out",
  notice: null,
  status: null,
  displayName: null,
  linkHref: null,
};

let bootTask: Promise<BootResult> | null = null;

function accountBoot(account: Account): BootResult {
  if (account.display_name === null || account.display_name.trim() === "") {
    return { ...QUIET, view: "ask-name" };
  }
  return { ...QUIET, view: "ready", displayName: account.display_name };
}

function clearLink(): void {
  window.history.replaceState(null, "", window.location.pathname);
}

async function exchangeToken(token: string): Promise<BootResult> {
  const result = await postSignIn(token);
  if (result.kind === "down") return { ...QUIET, down: true };
  if (result.kind === "message") {
    if (result.refused) await signOutFirebase();
    clearLink();
    return { ...QUIET, notice: result.message };
  }
  clearLink();
  return accountBoot(result.value);
}

async function runBoot(): Promise<BootResult> {
  try {
    const ready = await getReady();
    if (ready.kind === "down") return { ...QUIET, down: true };
    if (ready.kind === "message") return { ...QUIET, notice: ready.message };

    const session = await getSession();
    if (session.kind === "down") return { ...QUIET, down: true };
    if (session.kind === "value") {
      clearLink();
      return accountBoot(session.value);
    }
    if (!session.unauthorized) return { ...QUIET, notice: session.message };

    const href = await emailLinkHref();
    if (href) {
      const stored = readStoredEmail();
      if (!stored) return { ...QUIET, view: "link-email", linkHref: href };
      const finished = await completeEmailLink(stored, href);
      if (finished.kind === "token") return exchangeToken(finished.token);
      clearLink();
      if (finished.kind === "cancel") return QUIET;
      if (finished.kind === "unavailable") return { ...QUIET, notice: UNAVAILABLE };
      return { ...QUIET, notice: finished.message };
    }

    const current = await currentIdToken();
    if (current.kind === "token") return exchangeToken(current.token);
    if (current.kind === "message") return { ...QUIET, notice: current.message };
    return QUIET;
  } catch {
    return { ...QUIET, notice: "This could not be completed." };
  }
}

function bootOnce(): Promise<BootResult> {
  bootTask ??= runBoot();
  return bootTask;
}

export function FolioApp() {
  const [view, setView] = useState<View>("checking");
  const [down, setDown] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [email, setEmail] = useState("");
  const [draftName, setDraftName] = useState("");
  const [displayName, setDisplayName] = useState<string | null>(null);
  const linkHref = useRef<string | null>(null);
  const dialogRef = useRef<HTMLDivElement>(null);

  function applyBoot(result: BootResult) {
    setDown(result.down);
    setView(result.view);
    setNotice(result.notice);
    setStatus(result.status);
    setDisplayName(result.displayName);
    linkHref.current = result.linkHref;
  }

  useEffect(() => {
    let cancelled = false;
    void bootOnce().then((result) => {
      if (!cancelled) applyBoot(result);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!down) return;
    dialogRef.current?.focus();
  }, [down]);

  async function onGoogle() {
    setPending(true);
    setNotice(null);
    setStatus(null);
    try {
      const result = await signInWithGoogle();
      if (result.kind === "token") {
        applyBoot(await exchangeToken(result.token));
        return;
      }
      if (result.kind === "cancel") return;
      setNotice(result.kind === "unavailable" ? UNAVAILABLE : result.message);
    } finally {
      setPending(false);
    }
  }

  async function onEmail(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = email.trim();
    if (trimmed === "") {
      setNotice("Enter an email address.");
      return;
    }
    setPending(true);
    setNotice(null);
    try {
      const href = linkHref.current;
      if (href) {
        const finished = await completeEmailLink(trimmed, href);
        if (finished.kind === "token") {
          applyBoot(await exchangeToken(finished.token));
          return;
        }
        clearLink();
        linkHref.current = null;
        setView("signed-out");
        if (finished.kind === "cancel") return;
        setNotice(finished.kind === "unavailable" ? UNAVAILABLE : finished.message);
        return;
      }
      const sent = await sendEmailLink(trimmed);
      if (sent.kind === "sent") {
        setStatus(LINK_SENT);
        return;
      }
      setNotice(sent.kind === "unavailable" ? UNAVAILABLE : sent.message);
    } finally {
      setPending(false);
    }
  }

  async function onName(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setNotice(null);
    try {
      const result = await postDisplayName(draftName.trim());
      if (result.kind === "down") {
        setDown(true);
        return;
      }
      if (result.kind === "message") {
        setNotice(result.message);
        if (result.unauthorized) {
          await signOutFirebase();
          setDisplayName(null);
          setView("signed-out");
        }
        return;
      }
      const saved = result.value.display_name;
      if (saved === null || saved.trim() === "") {
        setView("ask-name");
        return;
      }
      setDisplayName(saved);
      setDraftName("");
      setView("ready");
    } finally {
      setPending(false);
    }
  }

  async function onSignOut() {
    setPending(true);
    setNotice(null);
    try {
      const result = await postSignOut();
      if (result.kind === "down") {
        setDown(true);
        return;
      }
      if (result.kind === "message") {
        setNotice(result.message);
        return;
      }
      await signOutFirebase();
      setDisplayName(null);
      setDraftName("");
      setStatus(null);
      setEmail("");
      linkHref.current = null;
      setView("signed-out");
    } finally {
      setPending(false);
    }
  }

  function leaveLink() {
    clearLink();
    linkHref.current = null;
    setNotice(null);
    setView("signed-out");
  }

  function holdFocus(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Tab") event.preventDefault();
  }

  const signedIn = view === "ask-name" || view === "ready";

  return (
    <>
      <div className="app" inert={down}>
        <header className="shell-header">
          <h1 className="wordmark">Folio</h1>
          {view === "ready" && displayName ? (
            <p className="person">{displayName}</p>
          ) : null}
          {signedIn ? (
            <button
              type="button"
              className="sign-out"
              onClick={() => void onSignOut()}
              disabled={pending}
            >
              Sign out
            </button>
          ) : null}
        </header>
        <main className="main">
          {notice ? (
            <p role="alert" className="notice">
              {notice}
            </p>
          ) : null}
          {status ? (
            <p role="status" className="status">
              {status}
            </p>
          ) : null}
          {view === "checking" ? <p className="lead">One moment.</p> : null}
          {view === "signed-out" || view === "link-email" ? (
            <div className="stack">
              <p className="lead">
                {view === "link-email"
                  ? "Enter the email address the link was sent to."
                  : "Sign in with Google, or an email link."}
              </p>
              {view === "link-email" ? null : (
                <button
                  type="button"
                  className="primary"
                  onClick={() => void onGoogle()}
                  disabled={pending}
                >
                  Continue with Google
                </button>
              )}
              {view === "link-email" ? null : <p className="or">or</p>}
              <form className="stack" onSubmit={(event) => void onEmail(event)} aria-busy={pending}>
                <div className="field">
                  <label htmlFor="email">Email</label>
                  <input
                    id="email"
                    name="email"
                    type="email"
                    autoComplete="email"
                    autoCapitalize="none"
                    autoCorrect="off"
                    inputMode="email"
                    required
                    value={email}
                    onChange={(event) => setEmail(event.target.value)}
                  />
                </div>
                <button type="submit" className="secondary" disabled={pending}>
                  {view === "link-email" ? "Continue" : "Email me a link"}
                </button>
              </form>
              {view === "link-email" ? (
                <button
                  type="button"
                  className="text-button"
                  onClick={leaveLink}
                  disabled={pending}
                >
                  Use another sign-in
                </button>
              ) : null}
            </div>
          ) : null}
          {view === "ask-name" ? (
            <form className="stack" onSubmit={(event) => void onName(event)} aria-busy={pending}>
              <p className="lead">Enter a name.</p>
              <div className="field">
                <label htmlFor="display-name">Name</label>
                <input
                  id="display-name"
                  name="display-name"
                  type="text"
                  autoComplete="name"
                  autoCapitalize="words"
                  required
                  value={draftName}
                  onChange={(event) => setDraftName(event.target.value)}
                />
              </div>
              <button type="submit" className="primary" disabled={pending}>
                Save
              </button>
            </form>
          ) : null}
        </main>
      </div>
      {down ? (
        <div className="down-layer">
          <div
            ref={dialogRef}
            className="down-panel"
            role="dialog"
            aria-modal="true"
            aria-labelledby="system-down"
            tabIndex={-1}
            onKeyDown={holdFocus}
          >
            <p id="system-down">{SYSTEM_DOWN}</p>
          </div>
        </div>
      ) : null}
    </>
  );
}
