"use client";

import { PaperClipIcon, XMarkIcon } from "@heroicons/react/24/outline";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  completeUpload,
  startUpload,
  uploadBytes,
  type ApiDown,
  type ApiMessage,
} from "../lib/api";
import { ACCEPT, refusal } from "./file-rules";
import { formatBytes } from "./file-size";

export const MAX_ATTACHED = 5;
const TOO_MANY = "You can attach up to 5 files to one message.";
const FALLBACK = "This could not be completed.";

export type Attachment = {
  key: number;
  // The thread the file was attached in, so a file chosen in one thread does not follow you to another.
  scope: string;
  name: string;
  size: number;
  percent: number;
  // The stored file's id once the upload has finished, otherwise null.
  id: string | null;
  // A sentence when the upload failed, otherwise null.
  error: string | null;
};

type Handlers = {
  onSignedOut: (message: string | null) => void;
  onDown: () => void;
};

// A file attached in the chat goes to the same place as one uploaded on the Files page. The
// message then names it, so the assistant knows which stored file is meant.
export function useAttachments({ onSignedOut, onDown }: Handlers, scope: string | null) {
  const [all, setAll] = useState<Attachment[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const queue = useRef<{ key: number; file: File }[]>([]);
  const running = useRef(false);
  const nextKey = useRef(0);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const section = useCallback(
    (result: ApiDown | ApiMessage): string | null => {
      if (result.kind === "down") {
        onDown();
        return null;
      }
      if (result.unauthorized) {
        onSignedOut(result.message);
        return null;
      }
      return result.message;
    },
    [onDown, onSignedOut],
  );

  const patch = useCallback((key: number, change: Partial<Attachment>) => {
    setAll((current) => current.map((item) => (item.key === key ? { ...item, ...change } : item)));
  }, []);

  const send = useCallback(
    async (key: number, file: File) => {
      const started = await startUpload(file.name, file.size);
      if (started.kind !== "value") {
        patch(key, { error: section(started) ?? FALLBACK });
        return;
      }
      const sent = await uploadBytes(started.value, file, (bytes) =>
        patch(key, { percent: Math.min(99, Math.round((bytes / Math.max(file.size, 1)) * 100)) }),
      );
      if (sent.kind !== "value") {
        patch(key, { error: section(sent) ?? FALLBACK });
        return;
      }
      const finished = await completeUpload(started.value.id);
      if (finished.kind !== "value") {
        patch(key, { error: section(finished) ?? FALLBACK });
        return;
      }
      patch(key, { percent: 100, id: finished.value.id });
    },
    [patch, section],
  );

  const drain = useCallback(async () => {
    if (running.current) return;
    running.current = true;
    try {
      for (let next = queue.current.shift(); next !== undefined; next = queue.current.shift()) {
        if (!mounted.current) break;
        await send(next.key, next.file);
      }
    } finally {
      running.current = false;
    }
  }, [send]);

  const items = all.filter((item) => item.scope === scope);
  const ready = items.filter((item) => item.id !== null);
  const busy = items.some((item) => item.id === null && item.error === null);

  function add(chosen: File[]) {
    if (scope === null) return;
    setNotice(null);
    const room = MAX_ATTACHED - items.filter((item) => item.error === null).length;
    const taken = chosen.slice(0, Math.max(room, 0));
    if (chosen.length > taken.length) setNotice(TOO_MANY);
    const added: Attachment[] = [];
    for (const file of taken) {
      const key = nextKey.current++;
      const problem = refusal(file);
      added.push({ key, scope, name: file.name, size: file.size, percent: 0, id: null, error: problem });
      if (problem === null) queue.current.push({ key, file });
    }
    setAll((current) => [...current, ...added]);
    void drain();
  }

  function remove(key: number) {
    queue.current = queue.current.filter((item) => item.key !== key);
    setAll((current) => current.filter((item) => item.key !== key));
  }

  function clear() {
    queue.current = queue.current.filter((item) => !items.some((mine) => mine.key === item.key));
    setAll((current) => current.filter((item) => item.scope !== scope));
    setNotice(null);
  }

  function restore(back: Attachment[]) {
    setAll((current) => [...current, ...back]);
  }

  return { items, ready, busy, notice, add, remove, clear, restore };
}

// The sentence added to the message. The id lets the assistant open exactly that file.
export function referenceLine(files: Attachment[]): string {
  return `Attached: ${files.map((file) => `${file.name} [file:${file.id}]`).join(", ")}`;
}

// The message is shown without the ids, which are for the assistant.
export function plainBody(body: string): string {
  return body.replace(/ \[file:[0-9a-f-]{36}\]/g, "");
}

export function AttachButton({
  disabled,
  onFiles,
}: {
  disabled: boolean;
  onFiles: (files: File[]) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <>
      <button
        type="button"
        className="attach-button"
        aria-label="Attach a file"
        title="Attach a file"
        disabled={disabled}
        onClick={() => input.current?.click()}
      >
        <PaperClipIcon aria-hidden="true" />
      </button>
      <input
        ref={input}
        type="file"
        hidden
        multiple
        accept={ACCEPT}
        onChange={(event) => {
          onFiles(Array.from(event.target.files ?? []));
          // Clearing lets the same file be chosen again.
          event.target.value = "";
        }}
      />
    </>
  );
}

export function AttachmentChips({
  items,
  notice,
  onRemove,
}: {
  items: Attachment[];
  notice: string | null;
  onRemove: (key: number) => void;
}) {
  if (items.length === 0 && notice === null) return null;
  return (
    <div className="attached">
      {items.length > 0 ? (
        <ul aria-label="Attached files">
          {items.map((item) => (
            <li key={item.key} className={item.error !== null ? "attached-item failed" : "attached-item"}>
              <PaperClipIcon aria-hidden="true" />
              <span className="attached-name">{item.name}</span>
              {item.error !== null ? (
                <span className="attached-state" role="alert">
                  {item.error}
                </span>
              ) : item.id === null ? (
                <span className="attached-state" role="status">
                  {item.percent}%
                </span>
              ) : (
                <span className="attached-state">{formatBytes(item.size)}</span>
              )}
              <button
                type="button"
                className="attached-remove"
                aria-label={`Remove ${item.name}`}
                onClick={() => onRemove(item.key)}
              >
                <XMarkIcon aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      {notice !== null ? (
        <p className="attached-notice" role="alert">
          {notice}
        </p>
      ) : null}
    </div>
  );
}
