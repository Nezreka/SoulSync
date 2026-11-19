import { Menu } from '@base-ui/react/menu';
import { useEffect, useState } from 'react';

import { Button } from '@/components/form';
import { openRequestPickInspector } from '@/features/downloads/inspector-modal';
import { AudiobookReleasesModal } from '@/routes/audiobooks/-ui/audiobook-releases-modal';

import type {
  AwaitingMedia,
  AwaitingMusicGroup,
  AwaitingQueue,
  AwaitingVideoTitle,
} from '../-requests.types';

import { relativeTime } from '../-requests.helpers';
import styles from './requests-page.module.css';

/** one row of the pick queue, whichever side it came from */
export type PickItem =
  | { source: 'music'; group: AwaitingMusicGroup }
  | { source: 'media'; media: AwaitingMedia }
  | { source: 'video'; video: AwaitingVideoTitle };

/** music, books, then movies and TV, each oldest approval first: the one
 *  that has waited longest is picked first */
export function pickItems(queue: AwaitingQueue): PickItem[] {
  const when = (item: PickItem) =>
    String(
      item.source === 'music'
        ? item.group.approved_at
        : item.source === 'media'
          ? item.media.resolved_at
          : item.video.approved_at,
    ) || '';
  return [
    ...queue.music.map((group): PickItem => ({ source: 'music', group })),
    ...queue.media.map((media): PickItem => ({ source: 'media', media })),
    ...queue.video.map((video): PickItem => ({ source: 'video', video })),
  ].sort((a, b) => when(a).localeCompare(when(b)));
}

export function pickKey(item: PickItem): string {
  if (item.source === 'music') return `pm:${item.group.profile_id}:${item.group.key}`;
  if (item.source === 'media') return `pb:${item.media.id}`;
  return `pv:${item.video.id}`;
}

function kindWord(item: PickItem): string {
  if (item.source === 'music') return item.group.kind === 'album' ? 'Album' : 'Track';
  if (item.source === 'media') return 'Audiobook';
  return { movie: 'Movie', show: 'Show', episode: 'Episode' }[item.video.kind];
}

function titleOf(item: PickItem): string {
  if (item.source === 'music') return item.group.title || 'Untitled';
  if (item.source === 'media') return item.media.title || 'Untitled';
  return item.video.title || 'Untitled';
}

function subOf(item: PickItem): string {
  if (item.source === 'music') {
    const n = item.group.track_count || item.group.tracks.length;
    const bits = [item.group.artist || ''];
    if (item.group.kind === 'album') bits.push(`${n} track${n === 1 ? '' : 's'} to pick`);
    return bits.filter(Boolean).join(' · ');
  }
  if (item.source === 'media') return item.media.subtitle || '';
  const v = item.video;
  if (v.kind === 'show') {
    const eps = v.seasons.reduce((n, s) => n + s.episodes.length, 0);
    return `${eps} episode${eps === 1 ? '' : 's'} to pick`;
  }
  return v.year ? String(v.year) : '';
}

function imageOf(item: PickItem): string {
  if (item.source === 'music') return item.group.image_url || '';
  if (item.source === 'media') return item.media.image_url || '';
  return item.video.poster_url || '';
}

/** "Kim asked · approved 2 hours ago" */
export function pickWho(item: PickItem, now = Date.now()): string {
  const who =
    item.source === 'music'
      ? item.group.requester_name
      : item.source === 'media'
        ? item.media.requester_name
        : item.video.requesters.join(', ');
  const when =
    item.source === 'music'
      ? item.group.approved_at
      : item.source === 'media'
        ? item.media.resolved_at
        : item.video.approved_at;
  const ago = relativeTime(when, now);
  return `${who || 'Someone'} asked` + (ago ? ` · approved ${ago}` : '');
}

/** what a video title's Pick buttons search for: the movie or episode, or
 *  one per season of a show (a season with one held episode searches it) */
export function videoPickTargets(v: AwaitingVideoTitle): Array<{
  label: string;
  scope: 'movie' | 'season' | 'episode';
  season?: number;
  episode?: number;
}> {
  if (v.kind === 'movie') return [{ label: 'Pick release', scope: 'movie' }];
  if (v.kind === 'episode') {
    return [
      {
        label: 'Pick release',
        scope: 'episode',
        season: Number(v.season_number),
        episode: Number(v.episode_number),
      },
    ];
  }
  return v.seasons.map((s) =>
    s.episodes.length === 1
      ? {
          label: `S${String(s.season_number).padStart(2, '0')}E${String(s.episodes[0]).padStart(2, '0')}`,
          scope: 'episode' as const,
          season: s.season_number,
          episode: s.episodes[0],
        }
      : { label: `Season ${s.season_number}`, scope: 'season' as const, season: s.season_number },
  );
}

/** the video side's own release picker (video-download-view.js). false
 *  when it isn't loaded. */
export function openVideoPicker(
  v: AwaitingVideoTitle,
  target: ReturnType<typeof videoPickTargets>[number],
): boolean {
  const manualSearch = window.VideoDownload?.manualSearch;
  if (!manualSearch) return false;
  // a show's title is the show; an episode request's row title carries "S01E02"
  const title = v.kind === 'episode' ? v.title.replace(/ S\d+E\d+$/, '') : v.title;
  manualSearch({
    title,
    scope: target.scope,
    season: target.season ?? null,
    episode: target.episode ?? null,
    year: v.year ?? null,
    poster: v.poster_url ?? null,
    mediaId: v.tmdb_id,
    mediaSource: 'tmdb',
    tmdbId: v.tmdb_id,
  });
  return true;
}

export function PickQueue({
  items,
  loading,
  failed,
  busy,
  onRetry,
  onPicked,
  onBookGrabbed,
  onAuto,
}: {
  items: PickItem[];
  loading: boolean;
  failed: boolean;
  busy: boolean;
  onRetry: () => void;
  /** something was sent to download from a picker: refresh the queue */
  onPicked: () => void;
  onBookGrabbed: (media: AwaitingMedia) => void;
  onAuto: (item: PickItem) => void;
}) {
  const [book, setBook] = useState<AwaitingMedia | null>(null);

  // the video picker is the video side's modal: it says when it grabbed
  useEffect(() => {
    const onStarted = () => onPicked();
    document.addEventListener('soulsync:video-download-started', onStarted);
    return () => document.removeEventListener('soulsync:video-download-started', onStarted);
  }, [onPicked]);

  if (failed) {
    return (
      <div className={styles.empty}>
        Couldn’t load what’s waiting for a pick.{' '}
        <button type="button" className={styles.linkButton} onClick={onRetry}>
          Try again
        </button>
      </div>
    );
  }
  if (loading) return <div className={styles.empty}>Loading…</div>;
  if (items.length === 0) {
    return <div className={styles.empty}>Nothing waiting for a release pick.</div>;
  }
  return (
    <>
      <p className={styles.pickIntro}>
        Approved, waiting for you to choose the release. Nothing downloads these on its own. Pick
        one and it downloads, imports and lands for whoever asked, like any other request.
      </p>
      <ul className={styles.list}>
        {items.map((item) => (
          <PickRow
            key={pickKey(item)}
            item={item}
            busy={busy}
            onPicked={onPicked}
            onBook={setBook}
            onAuto={() => onAuto(item)}
          />
        ))}
      </ul>
      {book ? (
        <AudiobookReleasesModal
          asin={book.asin || book.item_key}
          title={book.title}
          onClose={() => setBook(null)}
          onGrabbed={() => onBookGrabbed(book)}
        />
      ) : null}
    </>
  );
}

function PickRow({
  item,
  busy,
  onPicked,
  onBook,
  onAuto,
}: {
  item: PickItem;
  busy: boolean;
  onPicked: () => void;
  onBook: (media: AwaitingMedia) => void;
  onAuto: () => void;
}) {
  const [showTracks, setShowTracks] = useState(false);
  const [imageFailed, setImageFailed] = useState(false);
  const image = imageOf(item);
  const downloading = item.source === 'video' && item.video.downloading;

  const pickTrack = (group: AwaitingMusicGroup, track: { id: string; title: string }) =>
    openRequestPickInspector({
      profileId: group.profile_id,
      trackId: track.id,
      name: track.title,
      artist: group.artist,
      album: group.album,
      onPicked,
    });

  const videoPick = (target: ReturnType<typeof videoPickTargets>[number]) => {
    if (item.source !== 'video') return;
    if (!openVideoPicker(item.video, target)) {
      window.showToast?.('The video release picker isn’t loaded. Reload the page.', 'error');
    }
  };

  let actions = null;
  if (item.source === 'music') {
    const { group } = item;
    actions =
      group.tracks.length === 1 ? (
        <Button
          variant="primary"
          size="sm"
          disabled={busy}
          onClick={() => pickTrack(group, group.tracks[0])}
        >
          Pick release
        </Button>
      ) : (
        <Button
          variant="primary"
          size="sm"
          disabled={busy}
          aria-expanded={showTracks}
          onClick={() => setShowTracks((v) => !v)}
        >
          {showTracks ? 'Hide tracks' : 'Pick releases'}
        </Button>
      );
  } else if (item.source === 'media') {
    actions = (
      <Button variant="primary" size="sm" disabled={busy} onClick={() => onBook(item.media)}>
        Pick release
      </Button>
    );
  } else {
    const targets = videoPickTargets(item.video);
    actions = (
      <div className={styles.pickTargets}>
        {targets.map((t, i) => (
          <Button
            key={`${t.scope}:${t.season ?? ''}:${t.episode ?? ''}`}
            variant={i === 0 ? 'primary' : 'secondary'}
            size="sm"
            disabled={busy}
            onClick={() => videoPick(t)}
          >
            {t.label}
          </Button>
        ))}
      </div>
    );
  }

  return (
    <li className={styles.row} data-status="awaiting_selection">
      <div className={styles.thumb} aria-hidden="true">
        {image && !imageFailed ? (
          <img src={image} alt="" loading="lazy" onError={() => setImageFailed(true)} />
        ) : (
          <span className={styles.thumbGlyph}>
            {item.source === 'music' ? '♪' : item.source === 'media' ? '▤' : '▶'}
          </span>
        )}
      </div>

      <div className={styles.main}>
        <div className={styles.titleLine}>
          <span className={styles.rowTitle}>{titleOf(item)}</span>
          <span className={styles.kind}>{kindWord(item)}</span>
        </div>
        {subOf(item) ? <div className={styles.sub}>{subOf(item)}</div> : null}
        <div className={styles.who}>{pickWho(item)}</div>
        {item.source === 'music' && showTracks ? (
          <ol className={styles.pickTracks}>
            {item.group.tracks.map((t) => (
              <li key={t.id} className={styles.pickTrack}>
                <span className={styles.pickTrackTitle}>{t.title}</span>
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={busy}
                  aria-label={`Pick a release for ${t.title}`}
                  onClick={() => pickTrack(item.group, t)}
                >
                  Pick
                </Button>
              </li>
            ))}
          </ol>
        ) : null}
      </div>

      <div className={styles.side}>
        <span
          className={styles.status}
          data-status={downloading ? 'approved' : 'awaiting_selection'}
        >
          {downloading ? 'Downloading' : 'Waiting for a pick'}
        </span>
        {actions}
        <Menu.Root>
          <Menu.Trigger className={styles.more} aria-label={`More for ${titleOf(item)}`}>
            ⋯
          </Menu.Trigger>
          <Menu.Portal>
            <Menu.Positioner className={styles.menuPositioner} align="end" sideOffset={6}>
              <Menu.Popup className={styles.menu}>
                <Menu.Item className={styles.menuItem} onClick={onAuto}>
                  Download automatically instead
                </Menu.Item>
              </Menu.Popup>
            </Menu.Positioner>
          </Menu.Portal>
        </Menu.Root>
      </div>
    </li>
  );
}
