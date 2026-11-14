"use client";

import { ArrowLeftIcon } from "@heroicons/react/24/outline";
import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  deleteAccount,
  endSession,
  getUsage,
  listSessions,
  postDisplayName,
  type Account,
  type ApiDown,
  type ApiMessage,
  type SessionItem,
  type TokenTotals,
} from "../lib/api";
import { ConfirmDialog } from "./confirm-dialog";

type Props = {
  account: Account;
  onClose: () => void;
  onAccountChange: (account: Account) => void;
  // The message is the sentence to show on the public page, or null for a plain sign out.
  onSignedOut: (message: string | null) => void;
  onDown: () => void;
};

function whenText(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

export function Settings({ account, onClose, onAccountChange, onSignedOut, onDown }: Props) {
  const [nameDraft, setNameDraft] = useState(account.display_name ?? "");
  const [nameSaving, setNameSaving] = useState(false);
  const [nameError, setNameError] = useState<string | null>(null);
  const [nameSaved, setNameSaved] = useState(false);
  const [sessions, setSessions] = useState<SessionItem[] | null>(null);
  const [sessionsError, setSessionsError] = useState<string | null>(null);
  const [endingId, setEndingId] = useState<string | null>(null);
  const [usage, setUsage] = useState<TokenTotals | null>(null);
  const [usageError, setUsageError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const deleteButton = useRef<HTMLButtonElement>(null);

  // A 401 or a down result is handled for the whole screen. Anything else is a sentence for the section.
  function section(result: ApiDown | ApiMessage): string | null {
    if (result.kind === "down") {
      onDown();
      return null;
    }
    if (result.unauthorized) {
      onSignedOut(result.message);
      return null;
    }
    return result.message;
  }

  useEffect(() => {
    let active = true;
    void (async () => {
      const [listed, totals] = await Promise.all([listSessions(), getUsage()]);
      if (!active) return;
      // Both requests can fail the same way, but the screen is told once.
      let reportedDown = false;
      let reportedSignedOut = false;
      const down = () => {
        if (reportedDown) return;
        reportedDown = true;
        onDown();
      };
      const signedOut = (message: string) => {
        if (reportedSignedOut) return;
        reportedSignedOut = true;
        onSignedOut(message);
      };
      if (listed.kind === "value") setSessions(listed.value);
      else {
        setSessions([]);
        if (listed.kind === "down") down();
        else if (listed.unauthorized) signedOut(listed.message);
        else setSessionsError(listed.message);
      }
      if (totals.kind === "value") setUsage(totals.value);
      else if (totals.kind === "down") down();
      else if (totals.unauthorized) signedOut(totals.message);
      else setUsageError(totals.message);
    })();
    return () => {
      active = false;
    };
  }, [onDown, onSignedOut]);

  async function saveName(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const name = nameDraft.trim();
    if (name === "" || nameSaving) return;
    setNameSaving(true);
    setNameError(null);
    setNameSaved(false);
    const result = await postDisplayName(name);
    setNameSaving(false);
    if (result.kind !== "value") {
      setNameError(section(result));
      return;
    }
    onAccountChange(result.value);
    setNameDraft(result.value.display_name ?? name);
    setNameSaved(true);
  }

  async function endOne(item: SessionItem) {
    if (endingId !== null) return;
    setEndingId(item.id);
    setSessionsError(null);
    const result = await endSession(item.id);
    setEndingId(null);
    if (result.kind !== "value") {
      const sentence = section(result);
      if (sentence === null) return;
      setSessionsError(sentence);
      // The row may already be gone, so show what the server has now.
      const listed = await listSessions();
      if (listed.kind === "value") setSessions(listed.value);
      return;
    }
    if (item.current) {
      onSignedOut(null);
      return;
    }
    setSessions((current) => (current === null ? current : current.filter((s) => s.id !== item.id)));
  }

  async function confirmDelete() {
    if (deleting) return;
    setDeleting(true);
    setDeleteError(null);
    const result = await deleteAccount();
    if (result.kind === "value") {
      onSignedOut(null);
      return;
    }
    setDeleting(false);
    setDeleteError(section(result));
  }

  function cancelDelete() {
    setConfirming(false);
    setDeleteError(null);
    requestAnimationFrame(() => deleteButton.current?.focus());
  }

  return (
    <section className="settings" aria-label="Settings">
      <div className="settings-head">
        <h2>Settings</h2>
        <button type="button" className="quiet settings-back" onClick={onClose}>
          <ArrowLeftIcon aria-hidden="true" />
          Back to chat
        </button>
      </div>

      <section className="settings-section" aria-labelledby="settings-name">
        <h3 id="settings-name">Name</h3>
        <form className="settings-name" onSubmit={(event) => void saveName(event)}>
          <input
            type="text"
            maxLength={40}
            autoComplete="name"
            aria-label="Display name"
            value={nameDraft}
            onChange={(event) => {
              setNameDraft(event.target.value);
              setNameSaved(false);
            }}
          />
          <button type="submit" className="settings-button" disabled={nameSaving || nameDraft.trim() === ""}>
            Save
          </button>
        </form>
        {nameError ? (
          <p role="alert" className="rename-error">
            {nameError}
          </p>
        ) : null}
        {nameSaved ? (
          <p role="status" className="settings-note">
            Saved.
          </p>
        ) : null}
      </section>

      <section className="settings-section" aria-labelledby="settings-sessions">
        <h3 id="settings-sessions">Sessions</h3>
        {sessions === null ? <p className="settings-note">One moment.</p> : null}
        {sessions !== null && sessions.length > 0 ? (
          <ul aria-label="Sessions" className="session-list">
            {sessions.map((item) => (
              <li key={item.id} className="session-row">
                <dl>
                  <div>
                    <dt>Started</dt>
                    <dd>{whenText(item.created_at)}</dd>
                  </div>
                  <div>
                    <dt>Last active</dt>
                    <dd>{whenText(item.last_active_at)}</dd>
                  </div>
                </dl>
                {item.current ? <span className="session-mark">This browser</span> : null}
                <button
                  type="button"
                  className="settings-button"
                  onClick={() => void endOne(item)}
                  disabled={endingId !== null}
                >
                  End session
                </button>
              </li>
            ))}
          </ul>
        ) : null}
        {sessionsError ? (
          <p role="alert" className="rename-error">
            {sessionsError}
          </p>
        ) : null}
      </section>

      <section className="settings-section" aria-labelledby="settings-usage">
        <h3 id="settings-usage">Usage</h3>
        {usage === null && usageError === null ? <p className="settings-note">One moment.</p> : null}
        {usage !== null ? (
          <dl className="usage-figures">
            <div>
              <dt>Input tokens</dt>
              <dd>{usage.input_tokens.toLocaleString()}</dd>
            </div>
            <div>
              <dt>Output tokens</dt>
              <dd>{usage.output_tokens.toLocaleString()}</dd>
            </div>
            <div>
              <dt>Total tokens</dt>
              <dd>{usage.total_tokens.toLocaleString()}</dd>
            </div>
          </dl>
        ) : null}
        {usageError ? (
          <p role="alert" className="rename-error">
            {usageError}
          </p>
        ) : null}
      </section>

      <section className="settings-section" aria-labelledby="settings-delete">
        <h3 id="settings-delete">Delete account</h3>
        <p className="settings-note">Deleting your account is immediate and cannot be restored.</p>
        <button
          ref={deleteButton}
          type="button"
          className="settings-button settings-danger"
          onClick={() => setConfirming(true)}
        >
          Delete account
        </button>
      </section>

      {confirming ? (
        <ConfirmDialog
          title="Delete your account?"
          body={`Your threads and messages are deleted now. This cannot be undone. Type ${account.email} to confirm.`}
          confirmLabel="Delete account"
          requireText={{ label: "Type your email to confirm", expected: account.email }}
          error={deleteError}
          busy={deleting}
          onCancel={cancelDelete}
          onConfirm={() => void confirmDelete()}
        />
      ) : null}
    </section>
  );
}
