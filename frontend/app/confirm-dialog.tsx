"use client";

import { useEffect, useId, useRef, type KeyboardEvent } from "react";

type Props = {
  title: string;
  body: string;
  confirmLabel: string;
  error: string | null;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
};

export function ConfirmDialog({ title, body, confirmLabel, error, busy, onCancel, onConfirm }: Props) {
  const id = useId();
  const cancelRef = useRef<HTMLButtonElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const wasBusy = useRef(false);

  useEffect(() => {
    cancelRef.current?.focus();
  }, []);

  // The buttons are never disabled, so focus stays in the dialog. After a refusal it goes to the safe choice.
  useEffect(() => {
    if (wasBusy.current && !busy && error !== null) cancelRef.current?.focus();
    wasBusy.current = busy;
  }, [busy, error]);

  function cancel() {
    if (busy) return;
    onCancel();
  }

  function confirm() {
    if (busy) return;
    onConfirm();
  }

  function onKey(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      cancel();
      return;
    }
    if (event.key !== "Tab") return;
    const first = cancelRef.current;
    const last = confirmRef.current;
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
    <div className="down-layer">
      <div
        className="confirm-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby={`${id}-title`}
        aria-describedby={`${id}-body`}
        onKeyDown={onKey}
      >
        <h2 id={`${id}-title`}>{title}</h2>
        <p id={`${id}-body`}>{body}</p>
        {error ? (
          <p role="alert" className="rename-error">
            {error}
          </p>
        ) : null}
        <div className="confirm-actions">
          <button
            ref={cancelRef}
            type="button"
            className="confirm-cancel"
            onClick={cancel}
            aria-disabled={busy}
          >
            Cancel
          </button>
          <button
            ref={confirmRef}
            type="button"
            className="confirm-delete"
            onClick={confirm}
            aria-disabled={busy}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
