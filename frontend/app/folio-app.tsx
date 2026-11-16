"use client";

import {
  ArrowPathIcon,
  ArrowRightStartOnRectangleIcon,
  Bars3Icon,
  ChevronDownIcon,
  Cog6ToothIcon,
  DocumentTextIcon,
  PuzzlePieceIcon,
  PaperAirplaneIcon,
  StopIcon,
  UserCircleIcon,
} from "@heroicons/react/24/outline";
import type { ComponentType, SVGProps } from "react";
import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import {
  createThread,
  deleteThread,
  getActivity,
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
  type ActivityLine,
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
import { Integrations, type DriveReturn } from "./integrations";
import { Markdown } from "./markdown";
import { ProfilePanel } from "./profile-panel";
import { Reports } from "./reports";
import { Settings } from "./settings";
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
    <svg className="mark" viewBox="0 0 64 64" aria-hidden="true">
      <rect width="64" height="64" rx="14" fill="currentColor" />
      <path d="M42 0H50A14 14 0 0 1 64 14V22H50A8 8 0 0 1 42 14Z" fill="var(--mark)" />
      <path d="M18 18h22v7H26v6h12v7H26v10h-8z" fill="var(--paper)" />
    </svg>
  );
}

// What the agent is doing while it runs a tool. A new tool is one more line here.
function toolStatus(name: string): string {
  switch (name) {
    case "list_threads":
      return "Looking at your threads";
    case "create_thread":
      return "Creating a thread";
    case "rename_thread":
      return "Renaming the thread";
    case "delete_thread":
      return "Deleting the thread";
    case "list_knowledge":
      return "Looking at your notes";
    case "search_knowledge":
      return "Searching your notes";
    case "read_knowledge":
      return "Reading your notes";
    case "write_knowledge":
    case "append_knowledge":
    case "edit_knowledge":
      return "Updating your notes";
    case "delete_knowledge":
      return "Deleting a note";
    case "delete_profile":
      return "Deleting your profile";
    case "list_abilities":
      return "Looking at abilities";
    case "write_ability":
      return "Saving an ability";
    case "delete_ability":
      return "Deleting an ability";
    case "list_schedules":
      return "Looking at your schedules";
    case "write_schedule":
      return "Saving a schedule";
    case "delete_schedule":
      return "Deleting a schedule";
    case "start_research":
      return "Starting research";
    case "cancel_research":
      return "Cancelling research";
    case "list_research_runs":
      return "Looking at research";
    case "list_reports":
      return "Looking at reports";
    case "rename_report":
      return "Renaming a report";
    case "delete_report":
      return "Deleting a report";
    default:
      return "Working on it";
  }
}

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
  return {
    id: thread.id,
    name: thread.name,
    created_at: thread.created_at,
    working: thread.working,
    working_since: thread.working_since,
    working_kind: thread.working_kind,
    latest_message_at: thread.latest_message_at,
  };
}

// A single thread's answer may not carry the list's fields, so the list's values stay.
function withListed(page: Thread, listed: Thread | undefined): Thread {
  return {
    ...threadSummary(page),
    working: listed?.working ?? page.working,
    working_since: listed ? listed.working_since : page.working_since,
    working_kind: listed ? listed.working_kind : page.working_kind,
    latest_message_at: listed?.latest_message_at ?? page.latest_message_at,
  };
}

const POLL_MS = 20000;

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

// "4 min 12 s" for a run that has been going on for 252 seconds.
function elapsedText(since: string, now: number): string {
  const started = Date.parse(since);
  if (Number.isNaN(started)) return "";
  const seconds = Math.max(0, Math.floor((now - started) / 1000));
  const minutes = Math.floor(seconds / 60);
  if (minutes === 0) return `${seconds} s`;
  if (minutes < 60) return `${minutes} min ${seconds % 60} s`;
  return `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

function domainOf(url: string | null): string {
  if (url === null) return "";
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

function ActivityText({ line }: { line: ActivityLine }) {
  if (line.kind === "search") {
    return <span className="act-text">Searched for “{line.text}”</span>;
  }
  if (line.kind === "skipped") {
    return <span className="act-text">Could not open {domainOf(line.text) || line.text}</span>;
  }
  const site = domainOf(line.url);
  return (
    <>
      <span className="act-text">{line.url === null ? `Read your note ${line.text}` : `Read ${line.text}`}</span>
      {site ? <span className="act-site">{site}</span> : null}
    </>
  );
}

const ACTIVITY_POLL_MS = 5000;

function WorkBanner({
  threadId,
  since,
  kind,
}: {
  threadId: string;
  since: string | null;
  kind: string | null;
}) {
  const [now, setNow] = useState(() => Date.now());
  const [lines, setLines] = useState<ActivityLine[]>([]);
  const [open, setOpen] = useState(true);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => {
    let alive = true;
    async function load() {
      const result = await getActivity(threadId);
      if (alive && result.kind === "value") setLines(result.value.items);
    }
    void load();
    const timer = window.setInterval(() => void load(), ACTIVITY_POLL_MS);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [threadId]);
  const title = kind === "research" ? "Research in progress" : "Working in the background";
  const latest = lines.length > 0 ? lines[lines.length - 1] : null;
  return (
    <div className="work-banner">
      <span className="work-dot" aria-hidden="true" />
      <div className="work-text">
        <p className="work-title" role="status">
          {title}
          {since ? <span className="work-time">{elapsedText(since, now)}</span> : null}
        </p>
        {latest && open ? (
          <p className="work-now">
            <ActivityText line={latest} />
          </p>
        ) : null}
      </div>
      <button
        type="button"
        className="work-toggle"
        aria-expanded={open}
        aria-label={open ? "Minimize progress" : "Show progress"}
        onClick={() => setOpen((current) => !current)}
      >
        <ChevronDownIcon aria-hidden="true" />
      </button>
    </div>
  );
}

function readDriveReturn(): DriveReturn | null {
  if (typeof window === "undefined") return null;
  const value = new URLSearchParams(window.location.search).get("drive");
  return value === "connected" || value === "failed" ? value : null;
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
  const [toolLine, setToolLine] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [reportsOpen, setReportsOpen] = useState(false);
  const [driveReturn] = useState<DriveReturn | null>(readDriveReturn);
  const [integrationsOpen, setIntegrationsOpen] = useState(driveReturn !== null);
  // Only used below 900 px, where the sidebar is a menu that covers the page.
  const [railOpen, setRailOpen] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const linkHref = useRef<string | null>(null);
  const streamAbort = useRef<AbortController | null>(null);
  const stopping = useRef(false);
  const dialogRef = useRef<HTMLDivElement>(null);
  const messagePane = useRef<HTMLDivElement>(null);
  const requestGeneration = useRef(0);
  const acting = useRef(false);
  const pollBusy = useRef(false);
  // Raised when the list is changed by a rename, create or delete, so an older poll answer is dropped.
  const listEdits = useRef(0);
  // What the page last knew about the open thread, to tell when something changed without it.
  const knownOpen = useRef<{ id: string; latest: string | null; working: boolean } | null>(null);
  const threadsRef = useRef<Thread[]>([]);

  // Google sends the person back to /?drive=connected or /?drive=failed. The shell shows the page that
  // explains it, then removes the value so a reload does not repeat the line.
  useEffect(() => {
    if (driveReturn === null) return;
    const params = new URLSearchParams(window.location.search);
    params.delete("drive");
    const query = params.toString();
    window.history.replaceState(
      null,
      "",
      `${window.location.pathname}${query === "" ? "" : `?${query}`}${window.location.hash}`,
    );
  }, [driveReturn]);
  const messagesRef = useRef<ThreadMessage[]>([]);
  const openIdRef = useRef<string | null>(null);
  // Set when messages are reloaded while the person has scrolled up, so the view stays put.
  const keepScroll = useRef(false);

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
    setToolLine(null);
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
    setSettingsOpen(false);
    setProfileOpen(false);
    setReportsOpen(false);
    setIntegrationsOpen(false);
    setView("signed-out");
  }, []);

  const settingsSignedOut = useCallback(
    (message: string | null) => void endSession(message, () => true),
    [endSession],
  );
  const settingsDown = useCallback(() => setDown(true), []);
  const named = view === "ready" && displayName !== null && displayName !== "";

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
    // Keyed on whether a name exists, so saving a new name in settings does not reload the threads.
    if (!named) return;
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
        current.map((thread) =>
          thread.id === page.id ? withListed(page, thread) : thread,
        ),
      );
      setOpenId(page.id);
      setMessages(page.messages);
      setThreadsLoaded(true);
    })();
    return () => {
      active = false;
    };
  }, [named, endSession]);

  useEffect(() => {
    threadsRef.current = threads;
    messagesRef.current = messages;
    openIdRef.current = openId;
  });

  // True when the thread's messages were loaded again. A failure or a skipped load is false,
  // so the caller can leave its record of the thread unchanged and try again later.
  const reloadOpen = useCallback(
    async (threadId: string): Promise<boolean> => {
      const generation = requestGeneration.current;
      const pane = messagePane.current;
      const atBottom = pane === null || pane.scrollHeight - pane.scrollTop - pane.clientHeight < 40;
      const page = await getThread(threadId);
      if (requestGeneration.current !== generation || acting.current) return false;
      if (page.kind === "value") {
        keepScroll.current = !atBottom;
        setMessages(page.value.messages);
        setReloadKey((key) => key + 1);
        return true;
      }
      if (page.kind === "down") setDown(true);
      else if (page.unauthorized) await endSession(page.message, () => true);
      return false;
    },
    [endSession],
  );

  useEffect(() => {
    const open = threadsRef.current.find((thread) => thread.id === openId);
    knownOpen.current =
      open === undefined
        ? null
        : { id: open.id, latest: open.latest_message_at, working: open.working };
  }, [openId]);

  useEffect(() => {
    if (!named) return;
    let active = true;
    async function poll() {
      if (pollBusy.current || document.visibilityState === "hidden") return;
      pollBusy.current = true;
      const edits = listEdits.current;
      try {
        const listed = await listThreads();
        if (!active) return;
        if (listed.kind === "value") {
          if (listEdits.current !== edits) return;
          setThreads(listed.value);
          const id = openIdRef.current;
          const now = listed.value.find((thread) => thread.id === id);
          const before = knownOpen.current;
          // A turn of the person's own records its result itself, so it is left alone here.
          if (acting.current || id === null) return;
          if (now === undefined) {
            // Deleted elsewhere, so show the empty chat as a delete here does.
            requestGeneration.current += 1;
            knownOpen.current = null;
            setOpenId(null);
            setMessages([]);
            setDraft("");
            setToolLine(null);
            return;
          }
          const changed =
            before !== null &&
            before.id === now.id &&
            (before.latest !== now.latest_message_at || (before.working && !now.working));
          // The record moves on only after the reload worked, so a failed one is tried again.
          if (changed && !(await reloadOpen(now.id))) return;
          if (openIdRef.current === now.id) {
            knownOpen.current = { id: now.id, latest: now.latest_message_at, working: now.working };
          }
          return;
        }
        if (listed.kind === "down") setDown(true);
        else if (listed.unauthorized) await endSession(listed.message, () => active);
      } finally {
        pollBusy.current = false;
      }
    }
    const timer = setInterval(() => void poll(), POLL_MS);
    // The timer skips while hidden, so catch up once when the page comes back.
    const onVisible = () => {
      if (document.visibilityState === "visible") void poll();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      active = false;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [named, endSession, reloadOpen]);

  useEffect(() => {
    const pane = messagePane.current;
    if (!pane) return;
    if (keepScroll.current) {
      keepScroll.current = false;
      return;
    }
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
        setProfileOpen(false);
        setReportsOpen(false);
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
    setThreads((current) => {
      const exists = current.some((thread) => thread.id === page.id);
      if (!exists) return [...current, threadSummary(page)];
      return current.map((thread) =>
        thread.id === page.id ? withListed(page, thread) : thread,
      );
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
      listEdits.current += 1;
      setThreads((current) =>
        current.some((thread) => thread.id === created.id) ? current : [...current, created],
      );
      setOpenId(created.id);
      setMessages([]);
      setDraft("");
      setToolLine(null);
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
      setToolLine(null);
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
    listEdits.current += 1;
    setThreads(listed.value);
    const turned = listed.value.find((t) => t.id === threadId);
    if (
      turned !== undefined &&
      requestGeneration.current === generation &&
      openIdRef.current === threadId
    ) {
      // A scheduled run may have written into this thread while the turn was running.
      const shownNewest = Math.max(
        0,
        ...messagesRef.current.map((message) => Date.parse(message.created_at) || 0),
      );
      const newest = turned.latest_message_at === null ? 0 : Date.parse(turned.latest_message_at);
      const behind = Number.isFinite(newest) && newest > shownNewest;
      if (!behind || (await reloadOpen(threadId))) {
        knownOpen.current = {
          id: turned.id,
          latest: turned.latest_message_at,
          working: turned.working,
        };
      }
    }
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
      listEdits.current += 1;
      setThreads((current) =>
        current.map((thread) =>
          thread.id === id ? withListed(result.value, thread) : thread,
        ),
      );
      return null;
    }
    return refusal(result);
  }

  async function onDeleteThread(id: string): Promise<string | null> {
    const result = await deleteThread(id);
    if (result.kind !== "value") return refusal(result);
    listEdits.current += 1;
    setThreads((current) => current.filter((thread) => thread.id !== id));
    if (id === openId) {
      requestGeneration.current += 1;
      streamAbort.current?.abort();
      setOpenId(null);
      setMessages([]);
      setDraft("");
      setStreaming(false);
      setWorking(false);
      setToolLine(null);
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
          setToolLine(null);
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
        (name) => {
          if (requestGeneration.current !== generation) return;
          setToolLine(toolStatus(name));
          setWorking(true);
        },
      );
      if (requestGeneration.current !== generation) return;
      if (result.kind === "done" || result.kind === "closed") return;
      if (opened && result.kind === "message") return;
      if (result.kind === "failed") {
        setWorking(false);
        setToolLine(null);
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
      setToolLine(null);
      await showChatError(result);
    } finally {
      if (streamAbort.current === controller) streamAbort.current = null;
      acting.current = false;
      if (requestGeneration.current === generation) {
        setStreaming(false);
        setWorking(false);
        setToolLine(null);
      }
      void refreshThreads(threadId, generation);
      setReloadKey((key) => key + 1);
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
          setWorking(false);
          setToolLine(null);
          setMessages((current) => {
            if (requestGeneration.current !== generation) return current;
            return applyRetryChunk(current, assistantId, chunk, replace);
          });
        },
        (name) => {
          if (requestGeneration.current !== generation) return;
          setToolLine(toolStatus(name));
          setWorking(true);
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
      if (requestGeneration.current === generation) {
        setStreaming(false);
        setWorking(false);
        setToolLine(null);
      }
      void refreshThreads(threadId, generation);
      setReloadKey((key) => key + 1);
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
    setProfileOpen(false);
    setReportsOpen(false);
    setView("signed-out");
  }

  function holdFocus(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Tab") event.preventDefault();
  }

  type Page = "chat" | "reports" | "profile" | "integrations" | "settings";

  // One page at a time. Leaving a page is choosing another one in the sidebar.
  function go(next: Page) {
    setSettingsOpen(next === "settings");
    setReportsOpen(next === "reports");
    setProfileOpen(next === "profile");
    setIntegrationsOpen(next === "integrations");
    setRailOpen(false);
  }

  const signedIn = view === "ask-name" || view === "ready";
  const landing = view === "signed-out" || view === "link-email";
  const visibleThreads = threadsNewestFirst(threads);
  const openThread = visibleThreads.find((thread) => thread.id === openId) ?? null;
  const page: Page = settingsOpen
    ? "settings"
    : reportsOpen
      ? "reports"
      : profileOpen
        ? "profile"
        : integrationsOpen
          ? "integrations"
          : "chat";
  const initials =
    (displayName ?? "")
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((word) => word[0]?.toUpperCase() ?? "")
      .join("") || "F";
  const navItems: { id: Exclude<Page, "chat">; label: string; icon: ComponentType<SVGProps<SVGSVGElement>> }[] = [
    { id: "reports", label: "Reports", icon: DocumentTextIcon },
    { id: "profile", label: "Profile", icon: UserCircleIcon },
    { id: "integrations", label: "Integrations", icon: PuzzlePieceIcon },
    { id: "settings", label: "Settings", icon: Cog6ToothIcon },
  ];

  return (
    <>
      <div className="app" inert={down}>
        {landing ? (
          <div className="landing">
            <div className="landing-side">
              <span className="wordmark">
                <Mark />
                Folio
              </span>
              <div className="landing-body">
                <h1>An assistant that keeps a record of your work.</h1>
                <p className="landing-lead">
                  Ask questions, run research that cites its sources, and let Folio remember how you
                  like things done. Everything stays in your own account.
                </p>
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
                {view === "signed-out" ? (
                  <>
                    <button
                      type="button"
                      className="google"
                      onClick={() => void onGoogle()}
                      disabled={pending}
                    >
                      <GoogleMark />
                      Continue with Google
                    </button>
                    <p className="divider">or use your email</p>
                  </>
                ) : (
                  <p className="hint">Enter the email address the link was sent to.</p>
                )}
                <form className="email-form" onSubmit={(event) => void onEmail(event)} aria-busy={pending}>
                  <input
                    id="email"
                    name="email"
                    type="email"
                    autoComplete="email"
                    autoCapitalize="none"
                    autoCorrect="off"
                    inputMode="email"
                    placeholder="you@example.com"
                    aria-label="Email"
                    required
                    value={email}
                    onChange={(event) => setEmail(event.target.value)}
                  />
                  <button type="submit" disabled={pending}>
                    {view === "link-email" ? "Continue" : "Email me a link"}
                  </button>
                </form>
                {view === "link-email" ? (
                  <button type="button" className="text-button" onClick={leaveLink} disabled={pending}>
                    Use another sign-in
                  </button>
                ) : null}
                <p className="fine">Folio is invite only. Only approved email addresses can sign in.</p>
              </div>
              <p className="landing-foot">
                Your conversations are private to your account. <a href="/privacy">Privacy policy</a>
              </p>
            </div>
            <div className="landing-sample" aria-hidden="true">
              <article className="sample">
                <p className="sample-meta">
                  <span className="chip">Example report</span>12 sources opened, 9 cited
                </p>
                <h2>Do 4-day work weeks hold up for small teams?</h2>
                <p>
                  Across the pilots Folio read, most small teams{" "}
                  <mark>kept output level while cutting hours</mark>
                  <sup>1</sup>, though results were weaker where work depends on customer coverage
                  <sup>2</sup>.
                </p>
                <p>
                  Reported gains were mostly <mark>fewer sick days and lower turnover</mark>
                  <sup>3</sup>. Two sources disagree on how long the effect lasts<sup>4</sup>.
                </p>
                <ol>
                  <li>
                    <b>Four Day Week pilot results</b>, 4dayweek.com
                  </li>
                  <li>
                    <b>Coverage limits in service teams</b>, hbr.org
                  </li>
                  <li>
                    <b>Turnover after a shorter week</b>, nber.org
                  </li>
                  <li>
                    <b>Do the gains last?</b>, economist.com
                  </li>
                </ol>
              </article>
            </div>
          </div>
        ) : named && displayName !== null ? (
          <div className="desk" data-rail={railOpen ? "open" : "closed"}>
            <aside className="rail" id="rail">
              <span className="wordmark">
                <Mark />
                Folio
              </span>
              <ThreadList
                threads={visibleThreads}
                openId={openId}
                disabled={pending}
                onOpen={(id) => {
                  go("chat");
                  void onOpenThread(id);
                }}
                onCreate={() => {
                  go("chat");
                  void onNewThread();
                }}
                onRename={onRenameThread}
                onDelete={onDeleteThread}
              />
              <nav aria-label="Account">
                {navItems.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    className="nav-item"
                    aria-current={page === item.id ? "page" : undefined}
                    onClick={() => go(item.id)}
                  >
                    <item.icon aria-hidden="true" />
                    {item.label}
                  </button>
                ))}
              </nav>
              <div className="who">
                <span className="avatar" aria-hidden="true">
                  {initials}
                </span>
                <div className="who-text">
                  <p className="account-name">{displayName}</p>
                  {accountEmail ? <p className="mail">{accountEmail}</p> : null}
                </div>
                <button
                  type="button"
                  className="sign-out"
                  onClick={() => void onSignOut()}
                  disabled={pending}
                >
                  <ArrowRightStartOnRectangleIcon aria-hidden="true" />
                  Sign out
                </button>
              </div>
            </aside>
            <main className="main">
              <div className="mobilebar">
                <button
                  type="button"
                  className="menu-button"
                  aria-expanded={railOpen}
                  aria-controls="rail"
                  onClick={() => setRailOpen((open) => !open)}
                >
                  <Bars3Icon aria-hidden="true" />
                  {railOpen ? "Close menu" : "Menu"}
                </button>
              </div>
              {notice ? (
                <p role="alert" className="notice">
                  {notice}
                </p>
              ) : null}
              {!threadsLoaded ? <p className="hint pad">One moment.</p> : null}
              {threadsLoaded && page === "settings" ? (
                <Settings
                  account={{ email: accountEmail ?? "", display_name: displayName }}
                  onAccountChange={(account) => {
                    setDisplayName(account.display_name);
                    setAccountEmail(account.email);
                  }}
                  onSignedOut={settingsSignedOut}
                  onDown={settingsDown}
                />
              ) : null}
              {threadsLoaded && page === "reports" ? (
                <Reports reloadKey={reloadKey} onSignedOut={settingsSignedOut} onDown={settingsDown} />
              ) : null}
              {threadsLoaded && page === "profile" ? (
                <ProfilePanel reloadKey={reloadKey} onSignedOut={settingsSignedOut} onDown={settingsDown} />
              ) : null}
              {threadsLoaded && page === "integrations" ? (
                <Integrations
                  returned={driveReturn}
                  onSignedOut={settingsSignedOut}
                  onDown={settingsDown}
                />
              ) : null}
              {threadsLoaded && page === "chat" ? (
                openThread ? (
                  <section
                    className={messages.length === 0 ? "thread thread-empty" : "thread"}
                    aria-label={openThread.name}
                  >
                    <header className="top">
                      <h1 className="thread-name">{openThread.name}</h1>
                    </header>
                    {messages.length === 0 ? (
                      <div className="welcome">
                        <p className="welcome-hello">Hello, {displayName}</p>
                        <h2>How can I help today?</h2>
                      </div>
                    ) : (
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
                          ) : message.role === "person" ||
                            (message.kind !== "chat" && message.kind !== "result") ? (
                            <p
                              key={message.id}
                              className={message.role === "person" ? "message person" : "message reply"}
                            >
                              <span className="speaker">{message.role === "person" ? "You" : "Folio"}</span>
                              {message.body}
                            </p>
                          ) : (
                            <div key={message.id} className="message reply">
                              <span className="speaker">Folio</span>
                              <Markdown text={message.body} />
                            </div>
                          ),
                        )}
                        {working ? (
                          <div className="message reply working">
                            <span className="dots" role="status" aria-label="Folio is working">
                              <span className="dot" aria-hidden="true" />
                              <span className="dot" aria-hidden="true" />
                              <span className="dot" aria-hidden="true" />
                            </span>
                            <span className="working-line" role="status" aria-live="polite">
                              {toolLine}
                            </span>
                          </div>
                        ) : null}
                      </div>
                    )}
                    {openThread.working ? (
                      <WorkBanner
                        threadId={openThread.id}
                        since={openThread.working_since}
                        kind={openThread.working_kind}
                      />
                    ) : null}
                    <form className="composer" onSubmit={(event) => void sendDraft(event)}>
                      <textarea
                        rows={1}
                        value={draft}
                        placeholder="Ask Folio anything"
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
                ) : (
                  <section className="thread thread-empty" aria-label="Start">
                    <header className="top">
                      <h1 className="thread-name">Folio</h1>
                    </header>
                    <div className="welcome">
                      <p className="welcome-hello">Hello, {displayName}</p>
                      <h2>Start a thread to begin.</h2>
                      <button
                        type="button"
                        className="primary"
                        onClick={() => void onNewThread()}
                        disabled={pending}
                      >
                        New thread
                      </button>
                    </div>
                  </section>
                )
              ) : null}
            </main>
          </div>
        ) : (
          <div className="center">
            <section className="panel">
              <span className="wordmark">
                <Mark />
                Folio
              </span>
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
              {view === "ask-name" ? (
                <form className="block" onSubmit={(event) => void onName(event)} aria-busy={pending}>
                  <h1>What should Folio call you?</h1>
                  <input
                    id="display-name"
                    name="display-name"
                    type="text"
                    autoComplete="name"
                    autoCapitalize="words"
                    placeholder="Your name"
                    aria-label="Your name"
                    maxLength={40}
                    required
                    value={draftName}
                    onChange={(event) => setDraftName(event.target.value)}
                  />
                  <button type="submit" className="primary" disabled={pending}>
                    Save
                  </button>
                </form>
              ) : null}
              {signedIn ? (
                <button type="button" className="quiet" onClick={() => void onSignOut()} disabled={pending}>
                  <ArrowRightStartOnRectangleIcon aria-hidden="true" />
                  Sign out
                </button>
              ) : null}
            </section>
          </div>
        )}
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
