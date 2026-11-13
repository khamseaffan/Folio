"use client";

import { ArrowRightStartOnRectangleIcon, EnvelopeIcon } from "@heroicons/react/24/outline";
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

function GoogleMark() {
  return (
    <svg viewBox="0 0 48 48" aria-hidden="true">
      <path
        fill="#FFC107"
        d="M43.611 20.083H42V20H24v8h11.303c-1.649 4.657-6.08 8-11.303 8-6.627 0-12-5.373-12-12s5.373-12 12-12c3.059 0 5.842 1.154 7.961 3.039l5.657-5.657C34.046 6.053 29.268 4 24 4 12.955 4 4 12.955 4 24s8.955 20 20 20 20-8.955 20-20c0-1.341-.138-2.65-.389-3.917z"
      />
      <path
        fill="#FF3D00"
        d="m6.306 14.691 6.571 4.819C14.655 15.108 18.961 12 24 12c3.059 0 5.842 1.154 7.961 3.039l5.657-5.657C34.046 6.053 29.268 4 24 4 16.318 4 9.656 8.337 6.306 14.691z"
      />
      <path
        fill="#4CAF50"
        d="M24 44c5.166 0 9.86-1.977 13.409-5.192l-6.19-5.238A11.91 11.91 0 0 1 24 36c-5.202 0-9.619-3.317-11.283-7.946l-6.522 5.025C9.505 39.556 16.227 44 24 44z"
      />
      <path
        fill="#1976D2"
        d="M43.611 20.083H42V20H24v8h11.303a12.04 12.04 0 0 1-4.087 5.571l.003-.002 6.19 5.238C36.971 39.205 44 34 44 24c0-1.341-.138-2.65-.389-3.917z"
      />
    </svg>
  );
}

function Mark() {
  return (
    <svg viewBox="0 0 64 64" aria-hidden="true">
      <rect width="64" height="64" rx="14" fill="#3d5872" />
      <path d="M18 12h20l10 10v30H18z" fill="#f7f4f1" />
      <path d="M38 12l10 10H38z" fill="#d7e4ee" />
      <path d="M24 26h18v5H29v3h8v4h-8v8h-5z" fill="#3d5872" />
    </svg>
  );
}

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
  accountEmail: string | null;
  linkHref: string | null;
};

const QUIET: BootResult = {
  down: false,
  view: "signed-out",
  notice: null,
  status: null,
  displayName: null,
  accountEmail: null,
  linkHref: null,
};

let bootTask: Promise<BootResult> | null = null;

function accountBoot(account: Account): BootResult {
  if (account.display_name === null || account.display_name.trim() === "") {
    return { ...QUIET, view: "ask-name", accountEmail: account.email };
  }
  return {
    ...QUIET,
    view: "ready",
    displayName: account.display_name,
    accountEmail: account.email,
  };
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
  const [accountEmail, setAccountEmail] = useState<string | null>(null);
  const linkHref = useRef<string | null>(null);
  const dialogRef = useRef<HTMLDivElement>(null);

  function applyBoot(result: BootResult) {
    setDown(result.down);
    setView(result.view);
    setNotice(result.notice);
    setStatus(result.status);
    setDisplayName(result.displayName);
    setAccountEmail(result.accountEmail);
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
          setAccountEmail(null);
          setView("signed-out");
        }
        return;
      }
      const saved = result.value.display_name;
      setAccountEmail(result.value.email);
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
      setAccountEmail(null);
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
  const named = view === "ready" && displayName !== null && displayName !== "";

  return (
    <>
      <div className="scene" inert={down}>
        <svg className="fuji" viewBox="0 0 1440 360" aria-hidden="true">
          <path fill="#3d5872" d="M0 320 720 40 1440 320V360H0Z" />
          <path fill="#ffffff" d="M468 138 720 40 972 138Z" />
        </svg>
        <div className="stage">
          <section className="panel">
          <div className="panel-head">
            <div className="mark" aria-hidden="true">
              <Mark />
            </div>
            <div>
              {named ? <p className="kicker">Folio</p> : null}
              <h1>{named ? displayName : "Folio"}</h1>
            </div>
          </div>
          {view === "signed-out" ? (
            <p className="lede">A private assistant that remembers your work.</p>
          ) : null}
          {signedIn && accountEmail ? <p className="mail">{accountEmail}</p> : null}
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
          {view === "checking" ? <p className="hint">One moment.</p> : null}
          {view === "signed-out" || view === "link-email" ? (
            <form className="block" onSubmit={(event) => void onEmail(event)} aria-busy={pending}>
              {view === "link-email" ? (
                <p className="hint">Enter the email address the link was sent to.</p>
              ) : null}
              <div className="field">
                <EnvelopeIcon aria-hidden="true" />
                <input
                  id="email"
                  name="email"
                  type="email"
                  autoComplete="email"
                  autoCapitalize="none"
                  autoCorrect="off"
                  inputMode="email"
                  placeholder="Email"
                  aria-label="Email"
                  required
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                />
              </div>
              <button type="submit" className="primary" disabled={pending}>
                {view === "link-email" ? "Continue" : "Send a link"}
              </button>
              {view === "link-email" ? (
                <button type="button" className="text-button" onClick={leaveLink} disabled={pending}>
                  Use another sign-in
                </button>
              ) : (
                <button
                  type="button"
                  className="google"
                  onClick={() => void onGoogle()}
                  disabled={pending}
                >
                  <GoogleMark />
                  Continue with Google
                </button>
              )}
            </form>
          ) : null}
          {view === "ask-name" ? (
            <form className="block" onSubmit={(event) => void onName(event)} aria-busy={pending}>
              <div className="field">
                <input
                  id="display-name"
                  name="display-name"
                  type="text"
                  autoComplete="name"
                  autoCapitalize="words"
                  placeholder="Your name"
                  aria-label="Your name"
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
          {signedIn ? (
            <button
              type="button"
              className="quiet"
              onClick={() => void onSignOut()}
              disabled={pending}
            >
              <ArrowRightStartOnRectangleIcon aria-hidden="true" />
              Sign out
            </button>
          ) : null}
          </section>
        </div>
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
