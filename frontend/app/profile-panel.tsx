"use client";

import { ArrowLeftIcon } from "@heroicons/react/24/outline";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  getKnowledge,
  listAbilities,
  listKnowledge,
  type AbilityItem,
  type ApiDown,
  type ApiMessage,
  type KnowledgeFile,
  type KnowledgeItem,
} from "../lib/api";

type Props = {
  onClose: () => void;
  // The message is the sentence to show on the public page.
  onSignedOut: (message: string | null) => void;
  onDown: () => void;
  // Raised by the shell each time a chat turn ends.
  reloadKey: number;
};

const PROFILE_FILE = "profile.md";
const NO_KNOWLEDGE = "Nothing is stored yet. Tell Folio something about you, and it appears here.";
const NO_ABILITIES = "No abilities yet.";

function whenText(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function profileFirst(items: KnowledgeItem[]): KnowledgeItem[] {
  const profile = items.filter((item) => item.name === PROFILE_FILE);
  const rest = items.filter((item) => item.name !== PROFILE_FILE);
  return [...profile, ...rest];
}

export function ProfilePanel({ onClose, onSignedOut, onDown, reloadKey }: Props) {
  const [items, setItems] = useState<KnowledgeItem[] | null>(null);
  const [itemsError, setItemsError] = useState<string | null>(null);
  const [abilities, setAbilities] = useState<AbilityItem[] | null>(null);
  const [abilitiesError, setAbilitiesError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [file, setFile] = useState<KnowledgeFile | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const selectedRef = useRef<string | null>(null);
  const generation = useRef(0);
  const choice = useRef(0);
  const reportedDown = useRef(false);
  const reportedSignedOut = useRef(false);

  // A 401 or a down result is handled for the whole screen, once per load.
  // Anything else is a sentence for the section.
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
      const abilityList = await listAbilities();
      if (!current()) return;
      if (abilityList.kind === "value") {
        setAbilities(abilityList.value);
        setAbilitiesError(null);
      } else {
        setAbilities((previous) => previous ?? []);
        setAbilitiesError(section(abilityList));
      }
      // The second pass runs only when the file to show was gone, so there is at most one extra list request.
      for (let pass = 0; pass < 2; pass += 1) {
        const listed = await listKnowledge();
        if (!current()) return;
        if (listed.kind !== "value") {
          setItems((previous) => previous ?? []);
          setItemsError(section(listed));
          return;
        }
        const ordered = profileFirst(listed.value);
        setItems(ordered);
        setItemsError(null);
        const first = ordered[0];
        // The shown file stays when it still exists. Otherwise the first one is shown.
        const keep = ordered.find((item) => item.id === selectedRef.current);
        const target = keep ?? first;
        const chosen = choice.current;
        if (target === undefined) {
          selectedRef.current = null;
          setSelectedId(null);
          setFile(null);
          setFileError(null);
          return;
        }
        let opened = await getKnowledge(target.id);
        // A click while this load ran decides what is shown.
        if (!current() || choice.current !== chosen) return;
        let shown = target;
        const gone = (result: typeof opened) => result.kind === "message" && !result.unauthorized;
        if (gone(opened) && first !== undefined && first !== target) {
          shown = first;
          opened = await getKnowledge(first.id);
          if (!current() || choice.current !== chosen) return;
        }
        if (gone(opened) && pass === 0) {
          // The first file is gone too, so the list is stale. Read it again once.
          selectedRef.current = null;
          continue;
        }
        selectedRef.current = shown.id;
        setSelectedId(shown.id);
        if (opened.kind === "value") {
          setFile(opened.value);
          setFileError(null);
        } else {
          setFile(null);
          setFileError(section(opened));
        }
        return;
      }
    })();
    return () => {
      // A newer load or an unmount makes this one stale.
      if (generation.current === mine) generation.current += 1;
    };
  }, [reloadKey, section]);

  async function choose(item: KnowledgeItem) {
    const mine = ++choice.current;
    selectedRef.current = item.id;
    setSelectedId(item.id);
    setFileError(null);
    const opened = await getKnowledge(item.id);
    if (choice.current !== mine) return;
    if (opened.kind === "value") {
      setFile(opened.value);
      return;
    }
    setFile(null);
    setFileError(section(opened));
  }

  return (
    <section className="profile" aria-label="Profile">
      <div className="settings-head">
        <h2>Profile</h2>
        <button type="button" className="quiet profile-back" onClick={onClose}>
          <ArrowLeftIcon aria-hidden="true" />
          Back to chat
        </button>
      </div>
      <p className="settings-note">You change these by telling Folio in the chat.</p>

      <section className="settings-section" aria-labelledby="profile-knowledge">
        <h3 id="profile-knowledge">Knowledge</h3>
        {items === null ? <p className="settings-note">One moment.</p> : null}
        {items !== null && items.length === 0 && itemsError === null ? (
          <p className="settings-note">{NO_KNOWLEDGE}</p>
        ) : null}
        {items !== null && items.length > 0 ? (
          <ul aria-label="Knowledge files" className="profile-files">
            {items.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  className="profile-file"
                  aria-current={item.id === selectedId ? "true" : undefined}
                  onClick={() => void choose(item)}
                >
                  {item.name}
                </button>
              </li>
            ))}
          </ul>
        ) : null}
        {itemsError ? (
          <p role="alert" className="rename-error">
            {itemsError}
          </p>
        ) : null}
        {file !== null && file.id === selectedId ? (
          <div className="profile-text">
            <h4>{file.name}</h4>
            <p className="settings-note">Updated {whenText(file.updated_at)}</p>
            <pre>{file.body}</pre>
          </div>
        ) : null}
        {fileError ? (
          <p role="alert" className="rename-error">
            {fileError}
          </p>
        ) : null}
      </section>

      <section className="settings-section" aria-labelledby="profile-abilities">
        <h3 id="profile-abilities">Abilities</h3>
        {abilities === null ? <p className="settings-note">One moment.</p> : null}
        {abilities !== null && abilities.length === 0 && abilitiesError === null ? (
          <p className="settings-note">{NO_ABILITIES}</p>
        ) : null}
        {abilities !== null && abilities.length > 0 ? (
          <ul aria-label="Abilities" className="profile-abilities">
            {abilities.map((ability) => (
              <li key={ability.id}>
                <strong>{ability.name}</strong>
                <pre>{ability.body}</pre>
              </li>
            ))}
          </ul>
        ) : null}
        {abilitiesError ? (
          <p role="alert" className="rename-error">
            {abilitiesError}
          </p>
        ) : null}
      </section>
    </section>
  );
}
