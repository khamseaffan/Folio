"use client";

import { MicrophoneIcon, StopIcon } from "@heroicons/react/24/outline";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { transcribeAudio } from "../lib/api";

type Phase = "idle" | "recording" | "transcribing";

// About ten minutes of the largest format stays under the 10 MB the server accepts.
const LONGEST_MS = 5 * 60 * 1000;

const BLOCKED = "Folio cannot use the microphone. Allow it in your browser settings and try again.";
const NO_MIC = "No microphone was found.";
const EMPTY = "Nothing was recorded. Try again.";
const SILENT = "No speech was heard. Try again.";
const DOWN = "Voice is unavailable right now. Try again.";

const TYPES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/mp4",
  "audio/ogg;codecs=opus",
  "audio/ogg",
];

function pickType(): string | undefined {
  return TYPES.find((type) => MediaRecorder.isTypeSupported(type));
}

function subscribe(): () => void {
  return () => {};
}

function canRecord(): boolean {
  return (
    typeof MediaRecorder !== "undefined" &&
    navigator.mediaDevices !== undefined &&
    typeof navigator.mediaDevices.getUserMedia === "function"
  );
}

function clock(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(seconds % 60).padStart(2, "0")}`;
}

export function VoiceButton({
  disabled,
  onText,
}: {
  disabled: boolean;
  onText: (text: string) => void;
}) {
  const available = useSyncExternalStore(subscribe, canRecord, () => false);
  const [phase, setPhase] = useState<Phase>("idle");
  const [seconds, setSeconds] = useState(0);
  const [note, setNote] = useState<string | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const chunks = useRef<Blob[]>([]);
  const cancelled = useRef(false);
  const limit = useRef<number | null>(null);
  const onTextRef = useRef(onText);
  onTextRef.current = onText;

  useEffect(() => {
    // Strict mode runs the cleanup below once before the page is really in use.
    cancelled.current = false;
  }, []);

  useEffect(() => {
    if (phase !== "recording") return;
    const started = Date.now();
    const timer = window.setInterval(() => {
      setSeconds(Math.floor((Date.now() - started) / 1000));
    }, 500);
    return () => window.clearInterval(timer);
  }, [phase]);

  useEffect(
    () => () => {
      cancelled.current = true;
      if (limit.current !== null) window.clearTimeout(limit.current);
      if (recorder.current !== null && recorder.current.state !== "inactive") {
        recorder.current.stop();
      }
      release();
    },
    [],
  );

  function release() {
    stream.current?.getTracks().forEach((track) => track.stop());
    stream.current = null;
  }

  async function finish(kind: string) {
    release();
    recorder.current = null;
    if (cancelled.current) return;
    const audio = new Blob(chunks.current, { type: kind });
    chunks.current = [];
    if (audio.size === 0) {
      setNote(EMPTY);
      setPhase("idle");
      return;
    }
    setPhase("transcribing");
    const result = await transcribeAudio(audio);
    if (cancelled.current) return;
    setPhase("idle");
    if (result.kind === "down") {
      setNote(DOWN);
    } else if (result.kind === "message") {
      setNote(result.message);
    } else if (result.value.trim() === "") {
      setNote(SILENT);
    } else {
      onTextRef.current(result.value.trim());
    }
  }

  async function start() {
    setNote(null);
    let media: MediaStream;
    try {
      media = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (error) {
      const name = error instanceof DOMException ? error.name : "";
      setNote(name === "NotFoundError" || name === "OverconstrainedError" ? NO_MIC : BLOCKED);
      return;
    }
    if (cancelled.current) {
      media.getTracks().forEach((track) => track.stop());
      return;
    }
    stream.current = media;
    chunks.current = [];
    let made: MediaRecorder;
    try {
      const type = pickType();
      made = new MediaRecorder(media, type === undefined ? undefined : { mimeType: type });
    } catch {
      release();
      setNote(BLOCKED);
      return;
    }
    made.ondataavailable = (event) => {
      if (event.data.size > 0) chunks.current.push(event.data);
    };
    made.onstop = () => {
      if (limit.current !== null) window.clearTimeout(limit.current);
      limit.current = null;
      void finish(made.mimeType || pickType() || "audio/webm");
    };
    recorder.current = made;
    made.start();
    setSeconds(0);
    setPhase("recording");
    limit.current = window.setTimeout(stop, LONGEST_MS);
  }

  function stop() {
    if (recorder.current !== null && recorder.current.state !== "inactive") {
      recorder.current.stop();
    }
  }

  if (!available) return null;

  return (
    <>
      {phase === "recording" ? (
        <button type="button" className="voice voice-recording" onClick={stop} aria-label="Stop recording">
          <StopIcon aria-hidden="true" />
          <span className="voice-time" aria-hidden="true">
            {clock(seconds)}
          </span>
        </button>
      ) : (
        <button
          type="button"
          className="voice"
          onClick={() => void start()}
          disabled={disabled || phase === "transcribing"}
          aria-label="Record a message"
        >
          {phase === "transcribing" ? (
            <span className="voice-spinner" aria-hidden="true" />
          ) : (
            <MicrophoneIcon aria-hidden="true" />
          )}
          {phase === "transcribing" ? <span className="voice-time">Transcribing</span> : null}
        </button>
      )}
      <p className="voice-note" role="status">
        {phase === "recording"
          ? "Recording. Press stop when you are done."
          : phase === "transcribing"
            ? "Turning your recording into text."
            : (note ?? "")}
      </p>
    </>
  );
}
