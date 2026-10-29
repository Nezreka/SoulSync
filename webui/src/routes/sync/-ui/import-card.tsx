/**
 * The source tabs' card: a playlist you could bring into SoulSync.
 *
 * One action. A playlist that isn't in yet says Import; one that is says it's
 * in SoulSync and opens it. Everything else (identify, sync, download) happens
 * on the imported playlist, under Mirrored.
 *
 * A source that loads its own tracks also offers a copy kept as your own
 * (#1913): a playlist in My Playlists that never refreshes from the source.
 */

import type { ReactNode } from 'react';

import type { ImportStatus } from './use-importer';

import styles from './import-card.module.css';
import { PlaylistArt } from './playlist-art';

export interface ImportCardProps {
  id?: string;
  name: string;
  /** "42 tracks · by someone" */
  meta: string;
  artUrl?: string | null;
  glyph?: string;
  /** Spotify public's ALBUM / PLAYLIST tag, and the like. */
  badge?: string;
  status: ImportStatus;
  onAction: () => void;
  /** Keep it as your own playlist instead; absent when it can't be copied. */
  onCopy?: () => void;
  /** that copy is being made */
  copying?: boolean;
}

function actionLabel(status: ImportStatus): string {
  switch (status) {
    case 'imported':
      return 'Open';
    case 'importing':
      return 'Importing…';
    case 'checking':
      return '…';
    default:
      return 'Import';
  }
}

export function ImportCard({
  id,
  name,
  meta,
  artUrl,
  glyph,
  badge,
  status,
  onAction,
  onCopy,
  copying = false,
}: ImportCardProps) {
  const imported = status === 'imported';
  const busy = status === 'importing' || status === 'checking';
  const title = imported ? `Open ${name} in SoulSync` : `Import ${name} into SoulSync`;
  return (
    <div
      className={styles.card}
      id={id}
      data-status={status}
      role="button"
      tabIndex={0}
      aria-busy={status === 'importing' || undefined}
      aria-label={title}
      title={title}
      onClick={() => {
        if (!busy) onAction();
      }}
      onKeyDown={(e) => {
        if ((e.key === 'Enter' || e.key === ' ') && !busy) {
          e.preventDefault();
          onAction();
        }
      }}
    >
      <span className={styles.art}>
        <PlaylistArt url={artUrl} glyph={glyph ?? '🎵'} />
        {/* on the cover's corner, so the name keeps the card's width */}
        {onCopy ? (
          <button
            type="button"
            className={styles.copy}
            disabled={copying}
            aria-label={`Keep ${name} as your own playlist`}
            title="Keep your own copy in My Playlists. It won't refresh from the source, so it's yours to edit."
            onClick={(e) => {
              // the card imports on click; this is its own action
              e.stopPropagation();
              if (!copying) onCopy();
            }}
            onKeyDown={(e) => e.stopPropagation()}
          >
            {copying ? (
              <span className={styles.spinner} />
            ) : (
              <svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">
                <rect
                  x="8"
                  y="8"
                  width="12"
                  height="12"
                  rx="2.5"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                />
                <path
                  d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                />
              </svg>
            )}
          </button>
        ) : null}
      </span>
      <span className={styles.text}>
        <span className={styles.name}>{name}</span>
        <span className={styles.meta}>
          {badge ? <span className={styles.badge}>{badge}</span> : null}
          {meta}
        </span>
        {imported ? <span className={styles.inTag}>In SoulSync</span> : null}
      </span>
      <span className={styles.action} aria-hidden="true">
        {status === 'importing' ? <span className={styles.spinner} /> : null}
        {actionLabel(status)}
      </span>
    </div>
  );
}

export function ImportGrid({ children }: { children: ReactNode }) {
  return <div className={styles.grid}>{children}</div>;
}

/**
 * The line above a source's playlists: what importing does, and Import all
 * when there's more than one still to bring in.
 */
export function ImportIntro({
  remaining,
  onImportAll,
  canCopy = false,
}: {
  remaining: number;
  onImportAll?: () => void;
  /** this tab's cards offer the keep-your-own-copy button (#1913) */
  canCopy?: boolean;
}) {
  return (
    <div className={styles.intro}>
      <p>
        Import a playlist to bring it into SoulSync. It shows up under Mirrored and identifies
        itself in the background, ready to sync and download.
        {canCopy
          ? ' Or keep your own copy with the button beside Import: it lands in My Playlists and stays as it is, whatever the source does.'
          : ''}
      </p>
      {onImportAll && remaining > 1 ? (
        <button type="button" className={styles.importAll} onClick={onImportAll}>
          Import all {remaining}
        </button>
      ) : null}
    </div>
  );
}
