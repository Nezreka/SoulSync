/**
 * The export destination picker (#pl-export-modal, stats-automations.js
 * 663-724).
 *
 * The vanilla builds this overlay entirely from inline styles — it has no CSS
 * beyond `.pl-export-choice` as a click hook, which carries no rules. So the
 * styles are transcribed inline here rather than invented as classes; the class
 * names are kept because they are the only selector the markup ever had.
 *
 * The connection probe lives in this component (715-723) because that is where
 * the vanilla fires it — after the overlay is already on screen, so the choices
 * paint immediately and grey out a moment later. A probe that fails gates
 * nothing (its `.catch(() => {})`).
 *
 * DECLARED FAITHFULNESS NOTE: the subtitle reads "<name> → ListenBrainz" even
 * though two of the four destinations are Spotify and Deezer. That is the
 * vanilla's text (668) — the service choices were added to an export modal that
 * was originally ListenBrainz-only. Transcribed as-is; changing user-visible
 * copy is not this port's job.
 */

import { useEffect, useState } from 'react';

import type { ExportMode, ExportStatusLine } from '../-sync.export';

import { fetchExportConnectedSources } from '../-sync.api';
import {
  EXPORT_DESTINATIONS,
  EXPORT_LINK_COLOR,
  exportServiceLabel,
  isExportModeGated,
} from '../-sync.export';

/**
 * The live status line, injected into the card's own .card-meta row
 * (_setExportStatus, 807-819) so it sits inline and never breaks the flex row.
 *
 * The vanilla puts the anchor OUTSIDE the coloured span for the two finished
 * arms and INSIDE it for the needs_auth sentence; the anchor sets its own
 * colour in every case, so one shape renders all three identically.
 *
 * TWO DECLARED DIVERGENCES, both on the anchor:
 * - rel="noopener" is added to all three. The vanilla sets it on the authorize
 *   link only; every current browser implies it for target="_blank" anyway, so
 *   this is invisible.
 * - the click is stopped from bubbling. The span sits INSIDE the card, whose
 *   own onclick opens the discovery modal — in the vanilla, clicking "open"
 *   both follows the link and opens a modal behind it.
 */
export function ExportStatusSpan({ status }: { status: ExportStatusLine }) {
  return (
    <span className="export-status-span" style={{ color: status.color }}>
      {status.text}
      {status.link && (
        <>
          {' '}
          <a
            href={status.link.url}
            target="_blank"
            rel="noopener"
            style={{
              color: EXPORT_LINK_COLOR,
              textDecoration: status.link.underline ? 'underline' : undefined,
            }}
            onClick={(e) => e.stopPropagation()}
          >
            {status.link.label}
          </a>
        </>
      )}
      {status.suffix}
    </span>
  );
}

export interface ExportModalProps {
  name: string;
  /** Cancel button, backdrop click (694). */
  onClose: () => void;
  /** A live choice: the overlay closes and the job starts (704-706). */
  onChoose: (mode: ExportMode, backfill: boolean) => void;
  /** A greyed-out service choice: the overlay closes and nudges (697-701). */
  onGated: (mode: ExportMode) => void;
}

const OVERLAY: React.CSSProperties = {
  position: 'fixed',
  inset: 0,
  zIndex: 10000,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  background: 'rgba(0,0,0,0.6)',
  backdropFilter: 'blur(4px)',
};

const DIALOG: React.CSSProperties = {
  width: 420,
  maxWidth: '92vw',
  background: 'linear-gradient(135deg,rgba(26,26,30,0.98),rgba(18,18,22,0.99))',
  border: '1px solid rgba(var(--accent-rgb),0.25)',
  borderRadius: 18,
  padding: 22,
  boxShadow: '0 18px 50px rgba(0,0,0,0.55)',
};

export function ExportModal({ name, onClose, onChoose, onGated }: ExportModalProps) {
  const [connected, setConnected] = useState<string[] | null>(null);
  const [backfill, setBackfill] = useState(false);

  useEffect(() => {
    let current = true;
    void fetchExportConnectedSources().then((list) => {
      if (current) setConnected(list);
    });
    return () => {
      current = false;
    };
  }, []);

  return (
    <div
      id="pl-export-modal"
      style={OVERLAY}
      onClick={(e) => {
        // Only the backdrop itself closes (693).
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div style={DIALOG}>
        <div style={{ fontSize: 16, fontWeight: 700, color: '#fff', marginBottom: 4 }}>
          Export playlist
        </div>
        <div style={{ fontSize: 13, color: 'rgba(255,255,255,0.55)', marginBottom: 18 }}>
          {name} → ListenBrainz
        </div>
        {EXPORT_DESTINATIONS.map((dest, index) => {
          const gated = isExportModeGated(dest.mode, connected);
          const last = index === EXPORT_DESTINATIONS.length - 1;
          return (
            <button
              key={dest.mode}
              type="button"
              className="pl-export-choice"
              data-mode={dest.mode}
              data-disconnected={gated ? '1' : undefined}
              style={{
                width: '100%',
                textAlign: 'left',
                marginBottom: last ? 16 : 10,
                padding: '13px 15px',
                borderRadius: 12,
                border: dest.primary
                  ? '1px solid rgba(var(--accent-rgb),0.35)'
                  : '1px solid rgba(255,255,255,0.1)',
                background: dest.primary
                  ? 'rgba(var(--accent-rgb),0.12)'
                  : 'rgba(255,255,255,0.04)',
                color: '#fff',
                cursor: 'pointer',
                opacity: gated ? 0.5 : undefined,
              }}
              onClick={() => {
                if (gated) onGated(dest.mode);
                else onChoose(dest.mode, backfill);
              }}
            >
              <div style={{ fontWeight: 600 }}>{dest.title}</div>
              <div style={{ fontSize: 12, color: 'rgba(255,255,255,0.55)' }}>
                {gated ? (
                  <>
                    <span style={{ color: '#f59e0b' }}>Not connected</span> — set up{' '}
                    {exportServiceLabel(dest.mode)} in Settings → Connections first.
                  </>
                ) : (
                  dest.detail
                )}
              </div>
            </button>
          );
        })}
        <div style={{ fontSize: 11.5, color: 'rgba(255,255,255,0.4)', lineHeight: 1.5 }}>
          Tracks are matched by ID (MusicBrainz for ListenBrainz/JSPF; the stored Spotify/Deezer ID
          for those). Tracks without a match can&apos;t be included — you&apos;ll see how many made
          it. Renaming/re-syncing can reset play counts on the destination.
        </div>
        <label
          style={{
            display: 'flex',
            alignItems: 'flex-start',
            gap: 8,
            marginTop: 12,
            fontSize: 12,
            color: 'rgba(255,255,255,0.6)',
            cursor: 'pointer',
          }}
        >
          <input
            type="checkbox"
            id="pl-export-backfill"
            checked={backfill}
            onChange={(e) => setBackfill(e.target.checked)}
            style={{ marginTop: 2, flexShrink: 0, accentColor: 'rgb(var(--accent-rgb))' }}
          />
          <span>
            <b style={{ color: 'rgba(255,255,255,0.75)' }}>Match missing tracks</b> (Spotify/Deezer)
            — search the service for tracks with no known ID. Slower, and only confident matches are
            added.
          </span>
        </label>
        <div style={{ textAlign: 'right', marginTop: 14 }}>
          <button
            type="button"
            onClick={onClose}
            style={{
              background: 'none',
              border: 'none',
              color: 'rgba(255,255,255,0.5)',
              cursor: 'pointer',
              fontSize: 13,
            }}
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
