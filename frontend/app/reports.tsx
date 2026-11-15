"use client";

import { ArrowDownTrayIcon } from "@heroicons/react/24/outline";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  getReport,
  listReports,
  reportDownloadUrl,
  type ApiDown,
  type ApiMessage,
  type ReportDetail,
  type ReportItem,
  type ReportSource,
} from "../lib/api";
import { Markdown } from "./markdown";

type Props = {
  // The message is the sentence to show on the public page.
  onSignedOut: (message: string | null) => void;
  onDown: () => void;
  // Raised by the shell each time a chat turn ends or the open thread gets a new message.
  reloadKey: number;
};

const NO_REPORTS = "No reports yet. Ask Folio to research something.";

function whenText(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

// Only a web address becomes a link. Anything else is shown as text.
function webAddress(value: string | null): string | null {
  if (value === null) return null;
  try {
    const parsed = new URL(value);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.href : null;
  } catch {
    return null;
  }
}

function Source({ source }: { source: ReportSource }) {
  const href = source.kind === "page" ? webAddress(source.url) : null;
  return (
    <li value={source.position} className="report-source">
      {href !== null ? (
        <a href={href} target="_blank" rel="noopener noreferrer">
          {source.title}
        </a>
      ) : (
        <span>{source.title}</span>
      )}
      {source.url !== null ? <span className="settings-note">{source.url}</span> : null}
      <span className="report-used">{source.used_for}</span>
    </li>
  );
}

export function Reports({ onSignedOut, onDown, reloadKey }: Props) {
  const [items, setItems] = useState<ReportItem[] | null>(null);
  const [itemsError, setItemsError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [report, setReport] = useState<ReportDetail | null>(null);
  const [reportError, setReportError] = useState<string | null>(null);
  const selectedRef = useRef<string | null>(null);
  const generation = useRef(0);
  const choice = useRef(0);
  const reportedDown = useRef(false);
  const reportedSignedOut = useRef(false);

  // A 401 or a down result is handled for the whole screen, once per load.
  // Anything else is a sentence to show in place.
  const section = useCallback(
    (result: ApiDown | ApiMessage): string | null => {
      if (result.kind === "down") {
        if (!reportedDown.current) {
          reportedDown.current = true;
          onDown();
        }
        return null;
      }
      if (result.unauthorized) {
        if (!reportedSignedOut.current) {
          reportedSignedOut.current = true;
          onSignedOut(result.message);
        }
        return null;
      }
      return result.message;
    },
    [onDown, onSignedOut],
  );

  useEffect(() => {
    const mine = ++generation.current;
    reportedDown.current = false;
    reportedSignedOut.current = false;
    const current = () => generation.current === mine;
    void (async () => {
      const listed = await listReports();
      if (!current()) return;
      if (listed.kind !== "value") {
        setItems((previous) => previous ?? []);
        setItemsError(section(listed));
        return;
      }
      setItems(listed.value);
      setItemsError(null);
      const first = listed.value[0];
      // The shown report stays when it still exists. Otherwise the newest one is shown.
      const keep = listed.value.find((item) => item.id === selectedRef.current);
      const target = keep ?? first;
      const chosen = choice.current;
      if (target === undefined) {
        selectedRef.current = null;
        setSelectedId(null);
        setReport(null);
        setReportError(null);
        return;
      }
      let opened = await getReport(target.id);
      // A click while this load ran decides what is shown.
      if (!current() || choice.current !== chosen) return;
      let shown = target;
      const gone = (result: typeof opened) => result.kind === "message" && !result.unauthorized;
      if (gone(opened) && first !== undefined && first !== target) {
        shown = first;
        opened = await getReport(first.id);
        if (!current() || choice.current !== chosen) return;
      }
      selectedRef.current = shown.id;
      setSelectedId(shown.id);
      if (opened.kind === "value") {
        setReport(opened.value);
        setReportError(null);
      } else {
        setReport(null);
        setReportError(section(opened));
      }
    })();
    return () => {
      // A newer load or an unmount makes this one stale.
      if (generation.current === mine) generation.current += 1;
    };
  }, [reloadKey, section]);

  async function choose(item: ReportItem) {
    const mine = ++choice.current;
    selectedRef.current = item.id;
    setSelectedId(item.id);
    setReportError(null);
    const opened = await getReport(item.id);
    if (choice.current !== mine) return;
    if (opened.kind === "value") {
      setReport(opened.value);
      return;
    }
    setReport(null);
    setReportError(section(opened));
  }

  return (
    <section className="reports" aria-label="Reports">
      <div className="settings-head">
        <h2>Reports</h2>
      </div>

      {items === null ? <p className="settings-note">One moment.</p> : null}
      {items !== null && items.length === 0 && itemsError === null ? (
        <p className="settings-note">{NO_REPORTS}</p>
      ) : null}
      {itemsError ? (
        <p role="alert" className="rename-error">
          {itemsError}
        </p>
      ) : null}

      {items !== null && items.length > 0 ? (
        <div className="reports-body">
          <ul aria-label="Report list" className="reports-list">
            {items.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  className="reports-item"
                  aria-current={item.id === selectedId ? "true" : undefined}
                  onClick={() => void choose(item)}
                >
                  <span className="reports-item-name">{item.name}</span>
                  <span className="reports-item-question">{item.question}</span>
                  <span className="reports-item-time">{whenText(item.created_at)}</span>
                </button>
              </li>
            ))}
          </ul>

          <div className="report-pane">
            {report !== null && report.id === selectedId ? (
              <article className="report-view" aria-labelledby="report-title">
                <div className="report-head">
                  <h3 id="report-title">{report.name}</h3>
                  <a
                    className="report-download"
                    href={reportDownloadUrl(report.id)}
                    download
                  >
                    <ArrowDownTrayIcon aria-hidden="true" />
                    Download
                  </a>
                </div>
                <p className="report-question">Question: {report.question}</p>
                <p className="settings-note">{whenText(report.created_at)}</p>
                <div className="report-text">
                  <Markdown text={report.body} />
                </div>
                <section aria-labelledby="report-stopped" className="report-part">
                  <h4 id="report-stopped">How this research stopped</h4>
                  <p className="report-stop">{report.stop_note}</p>
                </section>
                <section aria-labelledby="report-sources" className="report-part">
                  <h4 id="report-sources">Sources</h4>
                  <ol aria-label="Sources" className="report-sources">
                    {report.sources.map((source) => (
                      <Source key={source.position} source={source} />
                    ))}
                  </ol>
                </section>
              </article>
            ) : null}
            {reportError ? (
              <p role="alert" className="rename-error">
                {reportError}
              </p>
            ) : null}
          </div>
        </div>
      ) : null}
    </section>
  );
}
