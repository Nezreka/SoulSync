/**
 * The Recent-URLs pill bar under each URL-import input (renderUrlHistory,
 * sync-services.js 8720-8768): 'Recent' label + one pill per entry (source
 * icon, truncated name, title tooltip, × remove). Hidden entirely when the
 * history is empty (the vanilla toggles display none/flex — here it just
 * doesn't render). Escaping is by construction; the vanilla escaped too (the
 * one region that did).
 */

import { useState } from 'react';

import type { UrlHistoryEntry } from '../-sync.urls';

import { readUrlHistory, writeUrlHistory } from '../-sync.url-tabs';
import { historyWithEntry, historyWithout, pillDisplayName } from '../-sync.urls';

/** localStorage-backed history state for one source's bar. */
export function useUrlHistory(source: string) {
  const [history, setHistory] = useState<UrlHistoryEntry[]>(() => readUrlHistory(source));

  const save = (url: string, name: string | null | undefined) => {
    const next = historyWithEntry(readUrlHistory(source), url, name, Date.now());
    writeUrlHistory(source, next);
    setHistory(next);
  };

  const remove = (url: string) => {
    const next = historyWithout(readUrlHistory(source), url);
    writeUrlHistory(source, next);
    setHistory(next);
  };

  return { history, save, remove };
}

export function UrlHistoryBar({
  containerId,
  icon,
  history,
  onPick,
  onRemove,
}: {
  containerId: string;
  icon: string;
  history: UrlHistoryEntry[];
  /** Pill click — the tab runs the already-loaded guard + load (8720-8732). */
  onPick: (url: string) => void;
  onRemove: (url: string) => void;
}) {
  if (history.length === 0) return null;

  return (
    <div className="url-history-bar" id={containerId} style={{ display: 'flex' }}>
      <span className="url-history-bar-label">Recent</span>
      {history.map((h) => (
        <div key={h.url} className="url-history-pill" title={h.name} onClick={() => onPick(h.url)}>
          <span className="url-history-pill-icon">{icon}</span>
          <span className="url-history-pill-name">{pillDisplayName(h.name)}</span>
          <button
            type="button"
            className="url-history-pill-remove"
            onClick={(e) => {
              e.stopPropagation();
              onRemove(h.url);
            }}
          >
            &times;
          </button>
        </div>
      ))}
    </div>
  );
}
