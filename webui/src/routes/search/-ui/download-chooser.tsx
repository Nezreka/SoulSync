import { Dialog } from '@base-ui/react/dialog';
import { useEffect, useState } from 'react';

import { DialogFrame } from '@/components/dialog';

import type { DownloadMode, DownloadTarget } from '../-basic.types';

import { artColors, formatSize, qualityLabel } from '../-basic.helpers';
import styles from './download-chooser.module.css';

const LAST_MODE_KEY = 'soulsync.basicSearch.downloadMode';

const OPTIONS: {
  mode: DownloadMode;
  title: string;
  text: string;
  tag?: string;
  icon: React.ReactNode;
}[] = [
  {
    mode: 'plain',
    title: 'Download as-is',
    text: 'Keeps the file exactly as shared: its own name, its own tags. Lands in Transfer.',
    icon: (
      <svg viewBox="0 0 20 20" fill="none" aria-hidden="true">
        <path
          d="M10 3v9M6 8.5l4 4 4-4M4 16h12"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    ),
  },
  {
    mode: 'enriched',
    title: 'Enriched download',
    text: 'Matches it to a release, then tags, adds cover art and files it into your library.',
    tag: 'Recommended',
    icon: (
      <svg viewBox="0 0 20 20" fill="none" aria-hidden="true">
        <path
          d="M10 2.5 11.6 8.4 17.5 10l-5.9 1.6L10 17.5l-1.6-5.9L2.5 10l5.9-1.6z"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinejoin="round"
        />
      </svg>
    ),
  },
  {
    mode: 'manual',
    title: 'Tag it yourself',
    text: 'For recordings no service knows, like bootlegs, live sets and mixtapes. You fill in the details.',
    icon: (
      <svg viewBox="0 0 20 20" fill="none" aria-hidden="true">
        <path
          d="M13.5 3.5 16.5 6.5 7 16H4v-3zM11.5 5.5l3 3"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    ),
  },
];

function lastMode(): DownloadMode {
  try {
    const stored = localStorage.getItem(LAST_MODE_KEY);
    if (stored === 'plain' || stored === 'enriched' || stored === 'manual') return stored;
  } catch {
    // private mode, blocked storage: fall through to the default
  }
  return 'enriched';
}

/** what the header says the target is */
function describe(target: DownloadTarget) {
  if (target.kind === 'album') {
    const { album } = target;
    const count = album.tracks?.length || album.track_count || 0;
    return {
      title: album.album_title || 'Unknown album',
      artist: album.artist || 'Unknown artist',
      detail: `${count} ${count === 1 ? 'track' : 'tracks'}`,
      quality: qualityLabel(album),
      size: formatSize(album.total_size),
      uploader: album.username,
      seed: `${album.artist ?? ''}${album.album_title ?? ''}`,
      files: count,
    };
  }
  const track = target.kind === 'track' ? target.track : target.album.tracks[target.trackIndex];
  const albumName = target.kind === 'albumTrack' ? target.album.album_title : (track?.album ?? '');
  return {
    title: track?.title || 'Unknown title',
    artist:
      track?.artist ||
      (target.kind === 'albumTrack' ? target.album.artist : '') ||
      'Unknown artist',
    detail: albumName || '',
    quality: track ? qualityLabel(track) : '',
    size: formatSize(track?.size),
    uploader: track?.username || (target.kind === 'albumTrack' ? target.album.username : ''),
    seed: `${track?.artist ?? ''}${albumName || track?.title || ''}`,
    files: 1,
  };
}

/**
 * Pick how a result comes in. opens from any Download button.
 *
 * the last pick is remembered, so someone who always wants files as-is isn't
 * re-choosing every time. Enter or the primary button goes.
 */
export function DownloadChooser({
  target,
  onClose,
  onChoose,
}: {
  target: DownloadTarget | null;
  onClose: () => void;
  onChoose: (target: DownloadTarget, mode: DownloadMode) => void;
}) {
  const [mode, setMode] = useState<DownloadMode>(lastMode);

  // re-read on each open, another tab may have changed it
  useEffect(() => {
    if (target) setMode(lastMode());
  }, [target]);

  if (!target) return null;
  const info = describe(target);
  const [light, dark] = artColors(info.seed || '?');

  const go = () => {
    try {
      localStorage.setItem(LAST_MODE_KEY, mode);
    } catch {
      // not remembered, that's fine
    }
    onClose();
    onChoose(target, mode);
  };

  return (
    <DialogFrame open onOpenChange={(open) => !open && onClose()} className={styles.popup}>
      <div className={styles.head}>
        <div
          className={styles.art}
          aria-hidden="true"
          style={{
            background: `radial-gradient(120% 90% at 20% 15%, ${light}, transparent 60%), linear-gradient(145deg, ${dark}, #111218)`,
          }}
        />
        <div className={styles.meta}>
          <Dialog.Title className={styles.title}>{info.title}</Dialog.Title>
          <div className={styles.sub}>
            {info.artist}
            {info.detail ? (
              <>
                <span className={styles.sep}>·</span>
                {info.detail}
              </>
            ) : null}
            {info.quality ? (
              <>
                <span className={styles.sep}>·</span>
                {info.quality}
              </>
            ) : null}
          </div>
        </div>
        <Dialog.Close className={styles.close} aria-label="Close">
          ×
        </Dialog.Close>
      </div>

      <div
        className={styles.body}
        role="radiogroup"
        aria-label="How should this come in?"
        onKeyDown={(event) => {
          if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            const index = OPTIONS.findIndex((option) => option.mode === mode);
            const step = event.key === 'ArrowDown' ? 1 : -1;
            setMode(OPTIONS[(index + step + OPTIONS.length) % OPTIONS.length].mode);
          }
        }}
      >
        {OPTIONS.map((option) => (
          <button
            key={option.mode}
            type="button"
            role="radio"
            aria-checked={mode === option.mode}
            className={styles.option}
            data-mode={option.mode}
            onClick={() => setMode(option.mode)}
            onDoubleClick={go}
          >
            <span className={styles.icon}>{option.icon}</span>
            <span>
              <span className={styles.optionTitle}>
                {option.title}
                {option.tag ? <span className={styles.tag}>{option.tag}</span> : null}
              </span>
              <span className={styles.optionText}>{option.text}</span>
            </span>
            <span className={styles.radio} aria-hidden="true" />
          </button>
        ))}
      </div>

      <div className={styles.foot}>
        <span className={styles.note}>
          {info.files > 1 ? `${info.files} files · ` : ''}
          {info.size}
          {info.uploader ? ` from ${info.uploader}` : ''}
        </span>
        <button type="button" className={styles.button} onClick={onClose}>
          Cancel
        </button>
        <button type="button" className={`${styles.button} ${styles.primary}`} onClick={go}>
          {mode === 'plain' ? 'Download' : 'Continue'}
        </button>
      </div>
    </DialogFrame>
  );
}
