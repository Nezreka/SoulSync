/**
 * The source tabs' card: a playlist you could bring into SoulSync.
 *
 * One action. A playlist that isn't in yet says Import; one that is says it's
 * in SoulSync and opens it. Everything else (identify, sync, download) happens
 * on the imported playlist, under Mirrored.
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
}: {
  remaining: number;
  onImportAll?: () => void;
}) {
  return (
    <div className={styles.intro}>
      <p>
        Import a playlist to bring it into SoulSync. It shows up under Mirrored and identifies
        itself in the background, ready to sync and download.
      </p>
      {onImportAll && remaining > 1 ? (
        <button type="button" className={styles.importAll} onClick={onImportAll}>
          Import all {remaining}
        </button>
      ) : null}
    </div>
  );
}
