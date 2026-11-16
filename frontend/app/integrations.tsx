"use client";

import { useEffect, useRef, useState } from "react";
import {
  disconnectDrive,
  driveConnectUrl,
  getDriveToken,
  getIntegrations,
  importDriveFile,
  type ApiDown,
  type ApiMessage,
  type DriveIntegration,
} from "../lib/api";
import { ConfirmDialog } from "./confirm-dialog";

export type DriveReturn = "connected" | "failed";

type Props = {
  returned: DriveReturn | null;
  // The message is the sentence to show on the public page, or null for a plain sign out.
  onSignedOut: (message: string | null) => void;
  onDown: () => void;
};

type PickerResponse = {
  action: string;
  docs?: { id: string; name?: string }[];
};

type PickerView = {
  setIncludeFolders: (on: boolean) => PickerView;
  setSelectFolderEnabled: (on: boolean) => PickerView;
};
type Picker = { setVisible: (on: boolean) => void };
type PickerBuilder = {
  addView: (view: PickerView) => PickerBuilder;
  setOAuthToken: (token: string) => PickerBuilder;
  setDeveloperKey: (key: string) => PickerBuilder;
  setAppId: (id: string) => PickerBuilder;
  setOrigin: (origin: string) => PickerBuilder;
  setCallback: (callback: (response: PickerResponse) => void) => PickerBuilder;
  build: () => Picker;
};
type PickerApi = {
  PickerBuilder: new () => PickerBuilder;
  DocsView: new (viewId?: string) => PickerView;
  ViewId: { DOCS: string };
  Action: { PICKED: string; CANCEL: string };
};
type GoogleGlobals = {
  gapi?: { load: (name: string, options: { callback: () => void; onerror?: () => void }) => void };
  google?: { picker?: PickerApi };
};

const SCRIPT = "https://apis.google.com/js/api.js";
const PICKER_FAILED = "Google Drive could not be opened. Try again.";
const FALLBACK = "This could not be completed.";

let pickerReady: Promise<PickerApi> | null = null;

function globals(): GoogleGlobals {
  return window as unknown as GoogleGlobals;
}

// The script and the picker module load once. A failure clears the cache so the next click retries.
function loadPicker(): Promise<PickerApi> {
  if (pickerReady !== null) return pickerReady;
  const attempt = new Promise<PickerApi>((resolve, reject) => {
    const withGapi = () => {
      const gapi = globals().gapi;
      if (gapi === undefined) {
        reject(new Error("gapi missing"));
        return;
      }
      gapi.load("picker", {
        callback: () => {
          const api = globals().google?.picker;
          if (api === undefined) reject(new Error("picker missing"));
          else resolve(api);
        },
        onerror: () => reject(new Error("picker failed")),
      });
    };
    if (globals().gapi !== undefined) {
      withGapi();
      return;
    }
    const script = document.createElement("script");
    script.src = SCRIPT;
    script.async = true;
    script.onload = withGapi;
    script.onerror = () => {
      script.remove();
      reject(new Error("script failed"));
    };
    document.head.appendChild(script);
  });
  pickerReady = attempt;
  attempt.catch(() => {
    pickerReady = null;
  });
  return attempt;
}

export function Integrations({ returned, onSignedOut, onDown }: Props) {
  const [drive, setDrive] = useState<DriveIntegration | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [imported, setImported] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [disconnectError, setDisconnectError] = useState<string | null>(null);
  const [line, setLine] = useState<string | null>(
    returned === "connected"
      ? "Google Drive is connected."
      : returned === "failed"
        ? "Google Drive could not be connected. Try again."
        : null,
  );
  const disconnectButton = useRef<HTMLButtonElement>(null);
  const connectButton = useRef<HTMLButtonElement>(null);
  const importButton = useRef<HTMLButtonElement>(null);
  const focusConnect = useRef(false);

  // A 401 or a down result is handled for the whole screen. Anything else is a sentence for the card.
  function sentence(result: ApiDown | ApiMessage): string | null {
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
      const result = await getIntegrations();
      if (!active) return;
      if (result.kind === "value") setDrive(result.value);
      else {
        if (result.kind === "down") onDown();
        else if (result.unauthorized) onSignedOut(result.message);
        else setLoadError(result.message);
      }
    })();
    return () => {
      active = false;
    };
  }, [onDown, onSignedOut]);

  useEffect(() => {
    if (focusConnect.current && drive !== null && !drive.connected) {
      focusConnect.current = false;
      connectButton.current?.focus();
    }
  }, [drive]);

  async function importPicked(fileId: string) {
    const result = await importDriveFile(fileId);
    setBusy(false);
    if (result.kind !== "value") {
      setError(sentence(result));
      return;
    }
    setImported(result.value.name);
    requestAnimationFrame(() => importButton.current?.focus());
  }

  async function openPicker() {
    if (busy || drive === null) return;
    setBusy(true);
    setError(null);
    setImported(null);
    setLine(null);
    const token = await getDriveToken();
    if (token.kind !== "value") {
      setBusy(false);
      setError(sentence(token));
      return;
    }
    let api: PickerApi;
    try {
      api = await loadPicker();
    } catch {
      setBusy(false);
      setError(PICKER_FAILED);
      return;
    }
    try {
      const view = new api.DocsView(api.ViewId.DOCS).setIncludeFolders(false).setSelectFolderEnabled(false);
      new api.PickerBuilder()
        .addView(view)
        .setOAuthToken(token.value.access_token)
        .setDeveloperKey(drive.picker_api_key)
        .setAppId(drive.app_id)
        .setOrigin(`${window.location.protocol}//${window.location.host}`)
        .setCallback((response) => {
          if (response.action === api.Action.PICKED) {
            const id = response.docs?.[0]?.id;
            if (id === undefined || id === "") {
              setBusy(false);
              setError(FALLBACK);
              return;
            }
            void importPicked(id);
          } else if (response.action === api.Action.CANCEL) {
            setBusy(false);
            requestAnimationFrame(() => importButton.current?.focus());
          }
        })
        .build()
        .setVisible(true);
    } catch {
      setBusy(false);
      setError(PICKER_FAILED);
    }
  }

  async function confirmDisconnect() {
    if (busy) return;
    setBusy(true);
    setDisconnectError(null);
    const result = await disconnectDrive();
    setBusy(false);
    if (result.kind !== "value") {
      setDisconnectError(sentence(result));
      return;
    }
    setConfirming(false);
    setImported(null);
    setError(null);
    setLine("Google Drive is disconnected.");
    focusConnect.current = true;
    setDrive((current) => (current === null ? current : { ...current, connected: false, label: null }));
  }

  function cancelDisconnect() {
    setConfirming(false);
    setDisconnectError(null);
    requestAnimationFrame(() => disconnectButton.current?.focus());
  }

  const shownError = error ?? loadError;

  return (
    <section className="settings" aria-label="Integrations">
      <div className="settings-head">
        <h2>Integrations</h2>
      </div>

      <section className="settings-section" aria-labelledby="integrations-drive">
        <h3 id="integrations-drive">Google Drive</h3>
        {drive === null && loadError === null ? <p className="settings-note">One moment.</p> : null}
        {drive !== null ? (
          <p className="settings-note">
            Folio keeps a private, read-only copy of each file you pick. It never changes your Drive.
          </p>
        ) : null}
        {drive !== null && !drive.connected ? (
          <div className="integration-actions">
            <button
              ref={connectButton}
              type="button"
              className="settings-button"
              onClick={() => window.location.assign(driveConnectUrl())}
            >
              Connect Google Drive
            </button>
          </div>
        ) : null}
        {drive !== null && drive.connected ? (
          <>
            <p className="settings-note">
              Connected{drive.label !== null && drive.label !== "" ? ` as ${drive.label}` : ""}.
            </p>
            <div className="integration-actions">
              <button
                ref={importButton}
                type="button"
                className="settings-button"
                onClick={() => void openPicker()}
                disabled={busy}
              >
                Import a file
              </button>
              <button
                ref={disconnectButton}
                type="button"
                className="settings-button settings-danger"
                onClick={() => setConfirming(true)}
                disabled={busy}
              >
                Disconnect
              </button>
            </div>
          </>
        ) : null}
        {busy && !confirming ? (
          <p role="status" className="settings-note">
            Working on it.
          </p>
        ) : null}
        {imported !== null ? (
          <p role="status" className="settings-note">
            Imported {imported}. You can find it on the Files page.
          </p>
        ) : null}
        {line !== null ? (
          <p role="status" className="settings-note">
            {line}
          </p>
        ) : null}
        {shownError ? (
          <p role="alert" className="rename-error">
            {shownError}
          </p>
        ) : null}
      </section>

      {confirming ? (
        <ConfirmDialog
          title="Disconnect Google Drive?"
          body="Files you already imported stay in Folio."
          confirmLabel="Disconnect"
          error={disconnectError}
          busy={busy}
          onCancel={cancelDisconnect}
          onConfirm={() => void confirmDisconnect()}
        />
      ) : null}
    </section>
  );
}
