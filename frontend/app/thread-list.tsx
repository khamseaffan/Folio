"use client";

import { PencilSquareIcon, PlusIcon, TrashIcon } from "@heroicons/react/24/outline";
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import type { Thread } from "../lib/api";

type Props = {
  threads: Thread[];
  openId: string | null;
  disabled: boolean;
  onOpen: (id: string) => void;
  onCreate: () => void;
  // Each returns the sentence to show when the server refused, or null when it is handled.
  onRename: (id: string, name: string) => Promise<string | null>;
  onDelete: (id: string) => Promise<string | null>;
};

export function ThreadList({
  threads,
  openId,
  disabled,
  onOpen,
  onCreate,
  onRename,
  onDelete,
}: Props) {
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  const [renameError, setRenameError] = useState<string | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const listRef = useRef<HTMLUListElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const deleteRef = useRef<HTMLButtonElement>(null);
  // Enter and Escape unmount the field, which fires a blur that must not save a second time.
  const finished = useRef(false);

  useEffect(() => {
    if (renamingId === null) return;
    inputRef.current?.focus();
    inputRef.current?.select();
  }, [renamingId]);

  useEffect(() => {
    if (confirmId !== null) cancelRef.current?.focus();
  }, [confirmId]);

  function focusAction(id: string, action: "rename" | "delete") {
    requestAnimationFrame(() => {
      listRef.current?.querySelector<HTMLElement>(`[data-${action}="${id}"]`)?.focus();
    });
  }

  function startRename(thread: Thread) {
    finished.current = false;
    setRenameDraft(thread.name);
    setRenameError(null);
    setRenamingId(thread.id);
  }

  function stopRename(id: string, restoreFocus: boolean) {
    finished.current = true;
    setRenamingId(null);
    setRenameError(null);
    if (restoreFocus) focusAction(id, "rename");
  }

  async function saveRename(thread: Thread, restoreFocus: boolean) {
    if (finished.current) return;
    const name = renameDraft.trim();
    if (name === "" || name === thread.name) {
      stopRename(thread.id, restoreFocus);
      return;
    }
    finished.current = true;
    const refused = await onRename(thread.id, name);
    if (refused === null) {
      setRenamingId(null);
      setRenameError(null);
      if (restoreFocus) focusAction(thread.id, "rename");
      return;
    }
    finished.current = false;
    setRenameError(refused);
    inputRef.current?.focus();
  }

  function onRenameBlur(thread: Thread) {
    // After a refusal the sentence stays up while the person edits. Leaving then gives up.
    if (renameError !== null) {
      stopRename(thread.id, false);
      return;
    }
    void saveRename(thread, false);
  }

  function onRenameKey(event: KeyboardEvent<HTMLInputElement>, thread: Thread) {
    if (event.key !== "Escape") return;
    event.preventDefault();
    stopRename(thread.id, true);
  }

  function onRenameSubmit(event: FormEvent<HTMLFormElement>, thread: Thread) {
    event.preventDefault();
    void saveRename(thread, true);
  }

  function closeConfirm() {
    const id = confirmId;
    setConfirmId(null);
    setDeleteError(null);
    if (id !== null) focusAction(id, "delete");
  }

  async function confirmDelete() {
    if (confirmId === null || deleting) return;
    setDeleting(true);
    const refused = await onDelete(confirmId);
    setDeleting(false);
    if (refused === null) {
      setConfirmId(null);
      setDeleteError(null);
      return;
    }
    setDeleteError(refused);
  }

  function onDialogKey(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      if (!deleting) closeConfirm();
      return;
    }
    if (event.key !== "Tab") return;
    const first = cancelRef.current;
    const last = deleteRef.current;
    if (first === null || last === null) return;
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  return (
    <>
      <button type="button" className="new-thread" onClick={onCreate} disabled={disabled}>
        <PlusIcon aria-hidden="true" />
        New thread
      </button>
      {threads.length > 0 ? (
        <ul aria-label="Threads" ref={listRef}>
          {threads.map((thread) => (
            <li key={thread.id} className="thread-row">
              {renamingId === thread.id ? (
                <form className="rename" onSubmit={(event) => onRenameSubmit(event, thread)}>
                  <input
                    ref={inputRef}
                    type="text"
                    maxLength={80}
                    autoComplete="off"
                    aria-label="Thread name"
                    value={renameDraft}
                    onChange={(event) => setRenameDraft(event.target.value)}
                    onKeyDown={(event) => onRenameKey(event, thread)}
                    onBlur={() => onRenameBlur(thread)}
                  />
                  {renameError ? (
                    <p role="alert" className="rename-error">
                      {renameError}
                    </p>
                  ) : null}
                </form>
              ) : (
                <>
                  <button
                    type="button"
                    className="thread-open"
                    aria-current={thread.id === openId ? "true" : "false"}
                    onClick={() => onOpen(thread.id)}
                    disabled={disabled}
                  >
                    {thread.name}
                  </button>
                  <div className="row-actions">
                    <button
                      type="button"
                      aria-label="Rename thread"
                      data-rename={thread.id}
                      onClick={() => startRename(thread)}
                      disabled={disabled}
                    >
                      <PencilSquareIcon aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      aria-label="Delete thread"
                      data-delete={thread.id}
                      onClick={() => {
                        setDeleteError(null);
                        setConfirmId(thread.id);
                      }}
                      disabled={disabled}
                    >
                      <TrashIcon aria-hidden="true" />
                    </button>
                  </div>
                </>
              )}
            </li>
          ))}
        </ul>
      ) : null}
      {confirmId !== null ? (
        <div className="down-layer">
          <div
            className="confirm-panel"
            role="dialog"
            aria-modal="true"
            aria-labelledby="delete-title"
            aria-describedby="delete-body"
            onKeyDown={onDialogKey}
          >
            <h2 id="delete-title">Delete this thread?</h2>
            <p id="delete-body">Its messages and schedules are deleted. This cannot be undone.</p>
            {deleteError ? (
              <p role="alert" className="rename-error">
                {deleteError}
              </p>
            ) : null}
            <div className="confirm-actions">
              <button
                ref={cancelRef}
                type="button"
                className="confirm-cancel"
                onClick={closeConfirm}
                disabled={deleting}
              >
                Cancel
              </button>
              <button
                ref={deleteRef}
                type="button"
                className="confirm-delete"
                onClick={() => void confirmDelete()}
                disabled={deleting}
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
