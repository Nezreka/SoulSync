/**
 * The source tabs' card: a playlist you could bring into SoulSync.
 *
 * One action. A playlist that isn't in yet says Import; one that is says it's
 * in SoulSync and opens it. Everything else (identify, sync, download) happens
 * on the imported playlist, under Mirrored.
 *
 * Only the button imports (#1934). A click anywhere else on a card that can
 * load its songs shows them first, with Import there too.
 *
 * A source that loads its own tracks also offers a copy kept as your own
 * (#1913): a playlist in My Playlists that never refreshes from the source.
 *
 * Once it's in, Edit opens its copy on the server in the server editor, to
 * swap a track for another version you own without going to Server Playlists.
 */

import { type ReactNode, useState } from 'react';

import type { ImportStatus } from './use-importer';

import styles from './import-card.module.css';
import { ImportPreviewSheet, type PreviewTrack } from './import-preview-sheet';
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
  /** The songs, for the look-before-you-import sheet; absent when it can't. */
  loadPreview?: () => Promise<PreviewTrack[]>;
  /** imported: its server copy in the server editor */
  onEdit?: () => Promise<unknown>;
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
  loadPreview,
  onEdit,
}: ImportCardProps) {
  const [previewing, setPreviewing] = useState(false);
  const [opening, setOpening] = useState(false);
  const imported = status === 'imported';
  const busy = status === 'importing' || status === 'checking';
  const previews = !imported && Boolean(loadPreview);
  const actionTitle = imported ? `Open ${name} in SoulSync` : `Import ${name} into SoulSync`;
  const title = previews ? `See what's in ${name}` : actionTitle;
  const open = () => {
    if (busy) return;
    if (previews) setPreviewing(true);
    else onAction();
  };
  return (
    <div
      className={styles.card}
      id={id}
      data-status={status}
      role="button"
      tabIndex={0}
      aria-busy={status === 'importing' || undefined}
      aria-label={previews ? title : name}
      title={title}
      onClick={open}
      onKeyDown={(e) => {
        if (e.target !== e.currentTarget) return;
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          open();
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
      <span className={styles.actions}>
        {imported && onEdit ? (
          <button
            type="button"
            className={styles.edit}
            disabled={opening}
            aria-busy={opening || undefined}
            aria-label={`Edit ${name} on your server`}
            title="Edit its copy on your server: swap a track for another version you own, add or remove"
            onClick={(e) => {
              e.stopPropagation();
              if (opening) return;
              setOpening(true);
              void onEdit().finally(() => setOpening(false));
            }}
            onKeyDown={(e) => e.stopPropagation()}
          >
            {opening ? <span className={styles.spinner} /> : null}
            Edit
          </button>
        ) : null}
        <button
          type="button"
          className={styles.action}
          disabled={busy}
          data-status={status}
          aria-label={actionTitle}
          title={actionTitle}
          onClick={(e) => {
            e.stopPropagation();
            if (!busy) onAction();
          }}
          onKeyDown={(e) => e.stopPropagation()}
        >
          {status === 'importing' ? <span className={styles.spinner} /> : null}
          {actionLabel(status)}
        </button>
      </span>
      {previewing && loadPreview ? (
        <ImportPreviewSheet
          name={name}
          meta={meta}
          artUrl={artUrl}
          glyph={glyph}
          load={loadPreview}
          onImport={onAction}
          onCopy={onCopy}
          onClose={() => setPreviewing(false)}
        />
      ) : null}
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
