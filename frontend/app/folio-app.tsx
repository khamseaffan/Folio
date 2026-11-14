"use client";

import {
  ArrowPathIcon,
  ArrowRightStartOnRectangleIcon,
  BookOpenIcon,
  ClockIcon,
  DocumentTextIcon,
  EnvelopeIcon,
  MagnifyingGlassIcon,
  PaperAirplaneIcon,
  StopIcon,
} from "@heroicons/react/24/outline";
import type { ComponentType, SVGProps } from "react";
import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import {
  createThread,
  deleteThread,
  getReady,
  getSession,
  getThread,
  listThreads,
  postDisplayName,
  postSignIn,
  postSignOut,
  postStop,
  renameThread,
  streamThreadMessage,
  streamThreadRetry,
  type Account,
  type ApiDown,
  type ApiMessage,
  type Thread,
  type ThreadMessage,
  type ThreadWithMessages,
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
import { ThreadList } from "./thread-list";

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

const abilities: { title: string; text: string; icon: ComponentType<SVGProps<SVGSVGElement>> }[] = [
  {
    title: "Memory",
    text: "It keeps notes about you and your work, and you change them in plain words.",
    icon: BookOpenIcon,
  },
  {
    title: "Research",
    text: "Ask for a report, then open the finished file with its sources.",
    icon: MagnifyingGlassIcon,
  },
  {
    title: "Scheduled work",
    text: "Say when a task should run. The result is waiting when you return.",
    icon: ClockIcon,
  },
  {
    title: "Files",
    text: "Upload a document, or bring one across from Google Drive.",
    icon: DocumentTextIcon,
  },
];

const SYSTEM_DOWN = "The system is down and repair is underway.";
const FAILED_REPLY = "That didn't go through.";
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

function threadsNewestFirst(threads: Thread[]): Thread[] {
  return [...threads].sort((left, right) => {
    if (left.created_at === right.created_at) return 0;
    return left.created_at < right.created_at ? 1 : -1;
  });
}

function retryTarget(current: ThreadMessage[]): string {
  let failureIndex = -1;
  for (let index = 0; index < current.length; index += 1) {
    const message = current[index];
    if (message !== undefined && message.kind === "failure") failureIndex = index;
  }
  if (failureIndex < 0) return crypto.randomUUID();
  for (let index = failureIndex - 1; index >= 0; index -= 1) {
    const message = current[index];
    if (message === undefined) continue;
    if (message.role === "person") break;
    if (message.role === "assistant" && message.kind === "chat") return message.id;
  }
  return crypto.randomUUID();
}

function applyRetryChunk(
  current: ThreadMessage[],
  assistantId: string,
  chunk: string,
  replace: boolean,
): ThreadMessage[] {
  if (current.some((message) => message.id === assistantId)) {
    return current.map((message) => {
      if (message.id !== assistantId) return message;
      return { ...message, body: replace ? chunk : message.body + chunk };
    });
  }
  const created: ThreadMessage = {
    id: assistantId,
    role: "assistant",
    kind: "chat",
    body: chunk,
    created_at: new Date().toISOString(),
  };
  const failureIndex = current.findIndex((message) => message.kind === "failure");
  if (failureIndex < 0) return [...current, created];
  return [...current.slice(0, failureIndex), created, ...current.slice(failureIndex)];
}

function threadSummary(thread: Thread): Thread {
  return { id: thread.id, name: thread.name, created_at: thread.created_at };
}

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
  const [threads, setThreads] = useState<Thread[]>([]);
  const [threadsLoaded, setThreadsLoaded] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ThreadMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [working, setWorking] = useState(false);
  const linkHref = useRef<string | null>(null);
  const streamAbort = useRef<AbortController | null>(null);
  const stopping = useRef(false);
  const dialogRef = useRef<HTMLDivElement>(null);
  const messagePane = useRef<HTMLDivElement>(null);
  const requestGeneration = useRef(0);
  const acting = useRef(false);

  function applyBoot(result: BootResult) {
    setDown(result.down);
    setView(result.view);
    setNotice(result.notice);
    setStatus(result.status);
    setDisplayName(result.displayName);
    setAccountEmail(result.accountEmail);
    linkHref.current = result.linkHref;
  }

  const endSession = useCallback(async (message: string | null, stillActive: () => boolean) => {
    requestGeneration.current += 1;
    streamAbort.current?.abort();
    setStreaming(false);
    setWorking(false);
    setNotice(message);
    await signOutFirebase();
    if (!stillActive()) return;
    setDisplayName(null);
    setAccountEmail(null);
    setThreads([]);
    setOpenId(null);
    setMessages([]);
    setDraft("");
    setThreadsLoaded(false);
    setPending(false);
    setView("signed-out");
  }, []);

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

  useEffect(() => {
    if (view !== "ready" || displayName === null || displayName === "") return;
    const generation = ++requestGeneration.current;
    let active = true;
    void (async () => {
      const listed = await listThreads();
      if (!active || requestGeneration.current !== generation) return;
      if (listed.kind === "down") {
        setDown(true);
        setThreadsLoaded(true);
        return;
      }
      if (listed.kind === "message") {
        if (listed.unauthorized) {
          await endSession(listed.message, () => active);
          return;
        }
        setNotice(listed.message);
        setThreads([]);
        setOpenId(null);
        setMessages([]);
        setThreadsLoaded(true);
        return;
      }
      setThreads(listed.value);
      const newest = threadsNewestFirst(listed.value)[0];
      if (newest === undefined) {
        setOpenId(null);
        setMessages([]);
        setThreadsLoaded(true);
        return;
      }
      const opened = await getThread(newest.id);
      if (!active || requestGeneration.current !== generation) return;
      if (opened.kind === "down") {
        setDown(true);
        setThreadsLoaded(true);
        return;
      }
      if (opened.kind === "message") {
        if (opened.unauthorized) {
          await endSession(opened.message, () => active);
          return;
        }
        setNotice(opened.message);
        setOpenId(null);
        setMessages([]);
        setThreadsLoaded(true);
        return;
      }
      const page = opened.value;
      setThreads((current) =>
        current.map((thread) => (thread.id === page.id ? threadSummary(page) : thread)),
      );
      setOpenId(page.id);
      setMessages(page.messages);
      setThreadsLoaded(true);
    })();
    return () => {
      active = false;
    };
  }, [view, displayName, endSession]);

  useEffect(() => {
    const pane = messagePane.current;
    if (!pane) return;
    pane.scrollTop = pane.scrollHeight;
  }, [messages, openId]);

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
        if (result.unauthorized) await endSession(result.message, () => true);
        else setNotice(result.message);
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
      await endSession(null, () => true);
      setDraftName("");
      setStatus(null);
      setEmail("");
      linkHref.current = null;
    } finally {
      setPending(false);
    }
  }

  async function showChatError(result: ApiDown | ApiMessage) {
    if (result.kind === "down") {
      setDown(true);
      return;
    }
    if (result.unauthorized) {
      await endSession(result.message, () => true);
      return;
    }
    setNotice(result.message);
  }

  function remember(page: ThreadWithMessages) {
    const summary = threadSummary(page);
    setThreads((current) => {
      const exists = current.some((thread) => thread.id === summary.id);
      if (!exists) return [...current, summary];
      return current.map((thread) => (thread.id === summary.id ? summary : thread));
    });
    setOpenId(page.id);
    setMessages(page.messages);
  }

  async function onNewThread() {
    if (acting.current || pending) return;
    acting.current = true;
    const generation = ++requestGeneration.current;
    setPending(true);
    setNotice(null);
    try {
      const result = await createThread();
      if (requestGeneration.current !== generation) return;
      if (result.kind !== "value") {
        await showChatError(result);
        return;
      }
      const created = threadSummary(result.value);
      setThreads((current) =>
        current.some((thread) => thread.id === created.id) ? current : [...current, created],
      );
      setOpenId(created.id);
      setMessages([]);
      setDraft("");
      setThreadsLoaded(true);
    } finally {
      acting.current = false;
      if (requestGeneration.current === generation) setPending(false);
    }
  }

  async function onOpenThread(id: string) {
    if (acting.current || pending || id === openId) return;
    acting.current = true;
    const generation = ++requestGeneration.current;
    setPending(true);
    setNotice(null);
    try {
      const result = await getThread(id);
      if (requestGeneration.current !== generation) return;
      if (result.kind !== "value") {
        await showChatError(result);
        return;
      }
      remember(result.value);
      setDraft("");
    } finally {
      acting.current = false;
      if (requestGeneration.current === generation) setPending(false);
    }
  }

  async function refreshThreads(threadId: string, generation: number) {
    const listed = await listThreads();
    if (listed.kind !== "value") {
      if (requestGeneration.current === generation) await showChatError(listed);
      return;
    }
    setThreads(listed.value);
    // The agent may have deleted the thread it was in.
    if (requestGeneration.current === generation && !listed.value.some((t) => t.id === threadId)) {
      setOpenId(null);
      setMessages([]);
    }
  }

  // A refusal the person can fix is shown by the control. Anything else goes to the shared handler.
  async function refusal(result: ApiDown | ApiMessage): Promise<string | null> {
    if (result.kind === "message" && !result.unauthorized) return result.message;
    await showChatError(result);
    return null;
  }

  async function onRenameThread(id: string, name: string): Promise<string | null> {
    const result = await renameThread(id, name);
    if (result.kind === "value") {
      const renamed = threadSummary(result.value);
      setThreads((current) => current.map((thread) => (thread.id === id ? renamed : thread)));
      return null;
    }
    return refusal(result);
  }

  async function onDeleteThread(id: string): Promise<string | null> {
    const result = await deleteThread(id);
    if (result.kind !== "value") return refusal(result);
    setThreads((current) => current.filter((thread) => thread.id !== id));
    if (id === openId) {
      requestGeneration.current += 1;
      streamAbort.current?.abort();
      setOpenId(null);
      setMessages([]);
      setDraft("");
      setStreaming(false);
      setWorking(false);
    }
    return null;
  }

  async function sendDraft(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    const text = draft;
    if (text.trim() === "" || openId === null || acting.current || pending || streaming) return;
    const threadId = openId;
    acting.current = true;
    const generation = ++requestGeneration.current;
    const controller = new AbortController();
    streamAbort.current = controller;
    setNotice(null);
    const personId = crypto.randomUUID();
    const assistantId = crypto.randomUUID();
    let opened = false;
    // Show the message and the working dots now. They are undone if the server refuses the send.
    setDraft("");
    setMessages((current) => [
      ...current,
      {
        id: personId,
        role: "person",
        kind: "chat",
        body: text,
        created_at: new Date().toISOString(),
      },
    ]);
    setWorking(true);
    try {
      const result = await streamThreadMessage(
        threadId,
        text,
        controller.signal,
        () => {
          if (requestGeneration.current !== generation) return;
          opened = true;
          setStreaming(() => requestGeneration.current === generation);
        },
        (chunk) => {
          if (requestGeneration.current !== generation) return;
          setWorking(false);
          setMessages((current) => {
            if (requestGeneration.current !== generation) return current;
            if (!current.some((message) => message.id === assistantId)) {
              return [
                ...current,
                {
                  id: assistantId,
                  role: "assistant",
                  kind: "chat",
                  body: chunk,
                  created_at: new Date().toISOString(),
                },
              ];
            }
            return current.map((message) =>
              message.id === assistantId ? { ...message, body: message.body + chunk } : message,
            );
          });
        },
      );
      if (requestGeneration.current !== generation) return;
      if (result.kind === "done" || result.kind === "closed") return;
      if (opened && result.kind === "message") return;
      if (result.kind === "failed") {
        setWorking(false);
        setMessages((current) => {
          if (requestGeneration.current !== generation) return current;
          return [
            ...current,
            {
              id: crypto.randomUUID(),
              role: "assistant",
              kind: "failure",
              body: FAILED_REPLY,
              created_at: new Date().toISOString(),
            },
          ];
        });
        return;
      }
      if (!opened) {
        // The server refused the send, so nothing was stored. Take the message back out.
        setMessages((current) => current.filter((message) => message.id !== personId));
        setDraft((current) => (current === "" ? text : current));
      }
      setWorking(false);
      await showChatError(result);
    } finally {
      if (streamAbort.current === controller) streamAbort.current = null;
      acting.current = false;
      if (requestGeneration.current === generation) {
        setStreaming(false);
        setWorking(false);
      }
      void refreshThreads(threadId, generation);
    }
  }

  async function onRetry() {
    if (openId === null || acting.current || pending || streaming) return;
    if (!messages.some((message) => message.kind === "failure")) return;
    const threadId = openId;
    const assistantId = retryTarget(messages);
    acting.current = true;
    const generation = ++requestGeneration.current;
    const controller = new AbortController();
    streamAbort.current = controller;
    setNotice(null);
    let opened = false;
    let first = true;
    try {
      const result = await streamThreadRetry(
        threadId,
        controller.signal,
        () => {
          if (requestGeneration.current !== generation) return;
          opened = true;
          setStreaming(() => requestGeneration.current === generation);
        },
        (chunk) => {
          if (requestGeneration.current !== generation) return;
          const replace = first;
          first = false;
          setMessages((current) => {
            if (requestGeneration.current !== generation) return current;
            return applyRetryChunk(current, assistantId, chunk, replace);
          });
        },
      );
      if (requestGeneration.current !== generation) return;
      if (result.kind === "done" && first) {
        const page = await getThread(threadId);
        if (requestGeneration.current !== generation) return;
        if (page.kind === "value") {
          setMessages(page.value.messages);
          return;
        }
      }
      if (result.kind === "done") {
        setMessages((current) => current.filter((message) => message.kind !== "failure"));
        return;
      }
      if (result.kind === "closed" || result.kind === "failed") return;
      if (opened && result.kind === "message") return;
      await showChatError(result);
    } finally {
      if (streamAbort.current === controller) streamAbort.current = null;
      acting.current = false;
      if (requestGeneration.current === generation) setStreaming(false);
      void refreshThreads(threadId, generation);
    }
  }

  async function onStop() {
    if (openId === null || stopping.current) return;
    stopping.current = true;
    const threadId = openId;
    const generation = requestGeneration.current;
    try {
      const result = await postStop(threadId);
      if (result.kind === "value" || requestGeneration.current !== generation) return;
      await showChatError(result);
    } finally {
      stopping.current = false;
    }
  }

  function onComposerKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey) return;
    // Safari reports the IME confirm as Enter.
    if (event.nativeEvent.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    void sendDraft();
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
  const landing = view === "signed-out" || view === "link-email";
  const visibleThreads = threadsNewestFirst(threads);
  const openThread = visibleThreads.find((thread) => thread.id === openId) ?? null;

  return (
    <>
      <div className="scene" inert={down}>
        <svg className="fuji" viewBox="0 0 1440 360" aria-hidden="true">
          <path fill="#3d5872" d="M0 320 720 40 1440 320V360H0Z" />
          <path fill="#ffffff" d="M468 138 720 40 972 138Z" />
        </svg>
        <div className={landing ? "stage landing" : named ? "stage desk" : "stage"}>
          {landing ? (
            <section className="intro" aria-label="Folio">
              <h2>A private assistant that remembers your work.</h2>
              <p>Your notes, files, and research stay in one account that only you can open.</p>
              <ul>
                {abilities.map((item) => (
                  <li key={item.title}>
                    <item.icon aria-hidden="true" />
                    <div>
                      <strong>{item.title}</strong>
                      <p>{item.text}</p>
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
          {named && displayName !== null ? (
            <div className="desk">
              {notice ? (
                <p role="alert" className="notice">
                  {notice}
                </p>
              ) : null}
              {!threadsLoaded ? <p className="hint">One moment.</p> : null}
              {threadsLoaded ? (
                <div className={openThread ? "desk-body" : "desk-body idle"}>
                  <div className="threads">
                    <div className="rail-brand">
                      <div className="mark" aria-hidden="true">
                        <Mark />
                      </div>
                      <p className="kicker">Folio</p>
                    </div>
                    <ThreadList
                      threads={visibleThreads}
                      openId={openId}
                      disabled={pending}
                      onOpen={(id) => void onOpenThread(id)}
                      onCreate={() => void onNewThread()}
                      onRename={onRenameThread}
                      onDelete={onDeleteThread}
                    />
                    <div className="desk-account">
                      <p className="account-name">{displayName}</p>
                      {accountEmail ? <p className="mail">{accountEmail}</p> : null}
                      <button
                        type="button"
                        className="quiet"
                        onClick={() => void onSignOut()}
                        disabled={pending}
                      >
                        <ArrowRightStartOnRectangleIcon aria-hidden="true" />
                        Sign out
                      </button>
                    </div>
                  </div>
                  {openThread ? (
                    <section
                      className={messages.length === 0 ? "thread thread-empty" : "thread"}
                      aria-label={openThread.name}
                    >
                      {messages.length === 0 ? (
                        <div className="welcome">
                          <p className="welcome-hello">Hello, {displayName}</p>
                          <h1>How can I assist you today?</h1>
                        </div>
                      ) : (
                        <h2 className="thread-name">{openThread.name}</h2>
                      )}
                      {messages.length === 0 ? null : (
                      <div className="messages" ref={messagePane} tabIndex={0} aria-label="Messages">
                        {messages.map((message) =>
                          message.kind === "failure" ? (
                            <div className="failure-row" key={message.id}>
                              <p className="failure-line">{message.body}</p>
                              {streaming ? null : (
                                <button
                                  type="button"
                                  className="quiet retry"
                                  onClick={() => void onRetry()}
                                  disabled={pending}
                                >
                                  <ArrowPathIcon aria-hidden="true" />
                                  Try again
                                </button>
                              )}
                            </div>
                          ) : (
                            <p
                              key={message.id}
                              className={
                                message.role === "person" ? "message person" : "message reply"
                              }
                            >
                              <span className="speaker">
                                {message.role === "person" ? "You" : "Folio"}
                              </span>
                              {message.body}
                            </p>
                          ),
                        )}
                        {working ? (
                          <div
                            className="message reply working"
                            role="status"
                            aria-label="Folio is working"
                          >
                            <span className="dot" aria-hidden="true" />
                            <span className="dot" aria-hidden="true" />
                            <span className="dot" aria-hidden="true" />
                          </div>
                        ) : null}
                      </div>
                      )}
                      <form className="composer" onSubmit={(event) => void sendDraft(event)}>
                        <textarea
                          rows={2}
                          value={draft}
                          placeholder="Ask me anything..."
                          aria-label="Message"
                          autoComplete="off"
                          disabled={pending}
                          onChange={(event) => setDraft(event.target.value)}
                          onKeyDown={onComposerKey}
                        />
                        {streaming ? (
                          <button type="button" className="primary send" onClick={() => void onStop()}>
                            <StopIcon aria-hidden="true" />
                            Stop
                          </button>
                        ) : (
                          <button
                            type="submit"
                            className="primary send"
                            disabled={pending || draft.trim() === ""}
                          >
                            <PaperAirplaneIcon aria-hidden="true" />
                            Send
                          </button>
                        )}
                      </form>
                    </section>
                  ) : null}
                </div>
              ) : null}
            </div>
          ) : (
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
          )}
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
