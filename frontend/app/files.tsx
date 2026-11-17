"use client";

import { ArrowDownTrayIcon, ArrowUpTrayIcon } from "@heroicons/react/24/outline";
import { useCallback, useEffect, useRef, useState, type ChangeEvent, type DragEvent } from "react";
import {
  completeUpload,
  fileDownloadUrl,
  getFiles,
  startUpload,
  uploadBytes,
  type ApiDown,
  type ApiMessage,
  type FileList,
} from "../lib/api";
import { ACCEPT, refusal } from "./file-rules";
import { formatBytes } from "./file-size";

type Props = {
  // The message is the sentence to show on the public page.
  onSignedOut: (message: string | null) => void;
  onDown: () => void;
  // Raised by the shell each time a chat turn ends or the open thread gets a new message.
  reloadKey: number;
};

const FALLBACK = "This could not be completed.";
const NO_FILES = "No files yet. Upload your first file above.";

type Upload = {
  key: number;
  name: string;
  percent: number;
  // A sentence when the upload failed, otherwise null.
  error: string | null;
};

function whenText(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString();
}

function typeText(contentType: string, name: string): string {
  const dot = name.lastIndexOf(".");
  if (dot >= 0 && dot < name.length - 1) return name.slice(dot + 1).toUpperCase();
  return contentType;
}

export function Files({ onSignedOut, onDown, reloadKey }: Props) {
  const [list, setList] = useState<FileList | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [dragging, setDragging] = useState(false);
  const queue = useRef<{ key: number; file: File }[]>([]);
  const running = useRef(false);
  const nextKey = useRef(0);
  const mounted = useRef(true);
  const picker = useRef<HTMLInputElement>(null);

  // A 401 or a down result is handled for the whole screen. Anything else is a sentence to show in place.
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

  const show = useCallback(
    (listed: Awaited<ReturnType<typeof getFiles>>) => {
      if (!mounted.current) return;
      if (listed.kind === "value") {
        setList(listed.value);
        setListError(null);
        return;
      }
      setListError(section(listed));
      setList((previous) => previous ?? { files: [], used_bytes: 0, limit_bytes: 0 });
    },
    [section],
  );

  useEffect(() => {
    mounted.current = true;
    void (async () => show(await getFiles()))();
    return () => {
      mounted.current = false;
    };
  }, [show, reloadKey]);

  function patch(key: number, change: Partial<Upload>) {
    setUploads((current) => current.map((item) => (item.key === key ? { ...item, ...change } : item)));
  }

  function drop(key: number) {
    setUploads((current) => current.filter((item) => item.key !== key));
  }

  async function send(key: number, file: File) {
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
    drop(key);
    show(await getFiles());
  }

  async function drain() {
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
  }

  function add(chosen: File[]) {
    const added: Upload[] = [];
    for (const file of chosen) {
      const key = nextKey.current++;
      const problem = refusal(file);
      added.push({ key, name: file.name, percent: 0, error: problem });
      if (problem === null) queue.current.push({ key, file });
    }
    setUploads((current) => [...current, ...added]);
    void drain();
  }

  function picked(event: ChangeEvent<HTMLInputElement>) {
    add(Array.from(event.target.files ?? []));
    // Clearing lets the same file be chosen again.
    event.target.value = "";
  }

  function dragOver(event: DragEvent<HTMLElement>) {
    if (!event.dataTransfer.types.includes("Files")) return;
    event.preventDefault();
    setDragging(true);
  }

  function dragLeave(event: DragEvent<HTMLElement>) {
    if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
    setDragging(false);
  }

  function dropped(event: DragEvent<HTMLElement>) {
    event.preventDefault();
    setDragging(false);
    add(Array.from(event.dataTransfer.files));
  }

  const counted = list !== null && list.limit_bytes > 0;
  const percentUsed = counted ? Math.min(100, (list.used_bytes / list.limit_bytes) * 100) : 0;

  return (
    <section
      className={dragging ? "files files-dragging" : "files"}
      aria-label="Files"
      onDragOver={dragOver}
      onDragLeave={dragLeave}
      onDrop={dropped}
    >
      <div className="settings-head">
        <h2>Files</h2>
      </div>

      <div className="files-upload">
        <input
          ref={picker}
          type="file"
          multiple
          accept={ACCEPT}
          className="files-input"
          aria-label="Choose files to upload"
          tabIndex={-1}
          onChange={picked}
        />
        <button type="button" className="primary" onClick={() => picker.current?.click()}>
          <ArrowUpTrayIcon aria-hidden="true" />
          Upload files
        </button>
        <p className="settings-note">
          Or drop files anywhere on this page. PDF, text, Markdown, CSV, XLSX, JPEG, PNG, WebP, or HEIC,
          up to 50 MB each.
        </p>
      </div>

      {counted ? (
        <div className="files-usage">
          <p>
            {formatBytes(list.used_bytes)} of {formatBytes(list.limit_bytes)} used
          </p>
          <div
            className="files-bar"
            role="progressbar"
            aria-label="Storage used"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(percentUsed)}
            aria-valuetext={`${formatBytes(list.used_bytes)} of ${formatBytes(list.limit_bytes)} used`}
          >
            <span style={{ width: `${percentUsed}%` }} />
          </div>
        </div>
      ) : null}

      {uploads.length > 0 ? (
        <ul className="files-uploads" aria-label="Uploads" aria-live="polite">
          {uploads.map((item) => (
            <li key={item.key} className="files-row">
              <span className="files-name">{item.name}</span>
              {item.error !== null ? (
                <>
                  <span role="alert" className="rename-error">
                    {item.error}
                  </span>
                  <button type="button" className="settings-button" onClick={() => drop(item.key)}>
                    Dismiss
                  </button>
                </>
              ) : (
                <>
                  <div
                    className="files-bar files-progress"
                    role="progressbar"
                    aria-label={`Uploading ${item.name}`}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={item.percent}
                  >
                    <span style={{ width: `${item.percent}%` }} />
                  </div>
                  <span className="settings-note">{item.percent === 0 ? "Waiting" : `${item.percent}%`}</span>
                </>
              )}
            </li>
          ))}
        </ul>
      ) : null}

      {list === null ? <p className="settings-note">One moment.</p> : null}
      {list !== null && list.files.length === 0 && listError === null ? (
        <p className="settings-note">{NO_FILES}</p>
      ) : null}
      {listError ? (
        <p role="alert" className="rename-error">
          {listError}
        </p>
      ) : null}

      {list !== null && list.files.length > 0 ? (
        <ul aria-label="File list" className="files-list">
          {list.files.map((file) => (
            <li key={file.id} className="files-row">
              <span className="files-name">{file.name}</span>
              <span className="settings-note files-meta">
                {file.kind === "drive_copy" ? "Imported from Drive" : typeText(file.content_type, file.name)}
                {" · "}
                {formatBytes(file.byte_size)}
                {" · "}
                {whenText(file.created_at)}
              </span>
              <a
                className="report-download"
                href={fileDownloadUrl(file.id)}
                aria-label={`Download ${file.name}`}
              >
                <ArrowDownTrayIcon aria-hidden="true" />
                Download
              </a>
            </li>
          ))}
        </ul>
      ) : null}

      <p className="settings-note files-delete-note">To delete a file, tell Folio in a chat.</p>
    </section>
  );
}
