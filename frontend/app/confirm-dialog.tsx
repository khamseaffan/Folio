"use client";

import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";

type Props = {
  title: string;
  body: string;
  confirmLabel: string;
  error: string | null;
  busy: boolean;
  // When set, the confirm button stays inactive until the person types the expected text.
  requireText?: { label: string; expected: string };
  onCancel: () => void;
  onConfirm: () => void;
};

export function ConfirmDialog({
  title,
  body,
  confirmLabel,
  error,
  busy,
  requireText,
  onCancel,
  onConfirm,
}: Props) {
  const id = useId();
  const [typed, setTyped] = useState("");
  const fieldRef = useRef<HTMLInputElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const wasBusy = useRef(false);

  const expected = requireText?.expected.trim().toLowerCase() ?? null;
  const matches = expected === null || (expected !== "" && typed.trim().toLowerCase() === expected);
  const confirmInactive = busy || !matches;

  useEffect(() => {
    (fieldRef.current ?? cancelRef.current)?.focus();
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
    if (confirmInactive) return;
    onConfirm();
  }

  function onKey(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      cancel();
      return;
    }
    if (event.key !== "Tab") return;
    const first = fieldRef.current ?? cancelRef.current;
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

  function onFieldKey(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key !== "Enter") return;
    if (event.nativeEvent.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    confirm();
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
        {requireText !== undefined ? (
          <input
            ref={fieldRef}
            type="text"
            className="confirm-field"
            aria-label={requireText.label}
            autoComplete="off"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            readOnly={busy}
            value={typed}
            onChange={(event) => setTyped(event.target.value)}
            onKeyDown={onFieldKey}
          />
        ) : null}
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
            aria-disabled={confirmInactive}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
