import { useRef, useState } from 'react';

import { openDownloadTaskInspector } from '@/features/downloads/inspector-modal';

import type { AdlDownload, AdlTaskDetail } from '../-adl.types';
import type { DlFact } from './dl-parts';

import { fetchTaskDetail } from '../-adl.api';
import {
  formatBytes,
  formatSpeed,
  liveDetailLines,
  statusClass,
  statusLabel,
  verificationHistoryId,
} from '../-adl.helpers';
import { unverifiedAudit, unverifiedPlay } from '../-adl.verif-actions';
import {
  clockTime,
  formatTag,
  isHighQuality,
  musicJourney,
  parseStamp,
  transferEta,
} from '../-dl.format';
import {
  DlAvatar,
  DlButton,
  DlIcon,
  DlLead,
  DlMore,
  DlSheet,
  DlShield,
  copyText,
} from './dl-parts';

/**
 * The retry tag.
 *
 * Only while a row is still in flight. a finished row's retry count is
 * history, not status. acoustid-triggered retries say so, because that means
 * the last candidate got quarantined, not just dropped.
 */
function RetryTag({ dl }: { dl: AdlDownload }) {
  const cls = statusClass(dl.status);
  if (!dl.retry_info || (cls !== 'active' && cls !== 'queued')) return null;
  const acoustid = ['acoustid', 'acoustid_unverified'].includes(String(dl.retry_trigger ?? ''));
  const because = dl.retry_trigger
    ? acoustid
      ? ', previous candidate quarantined (AcoustID)'
      : `, previous candidate triggered by ${dl.retry_trigger}`
    : '';
  return (
    <span
      className="dlc-tag dlc-tag-retry adl-retry-info"
      title={`Retry engine: trying the next-best candidate (attempt ${dl.retry_info}${because})`}
    >
      Retry {dl.retry_info}
      {acoustid ? ' · AcoustID' : ''}
    </span>
  );
}

/** The AcoustID shield on a finished row, or nothing. */
function VerifShield({ dl }: { dl: AdlDownload }) {
  if (dl.status !== 'completed') return null;
  switch (dl.verification_status) {
    case 'verified':
      return (
        <DlShield kind="ok" title="AcoustID verified: the fingerprint matches the expected track" />
      );
    case 'human_verified':
      return (
        <DlShield kind="human" title="You verified this one. The AcoustID scanner skips it." />
      );
    case 'unverified':
      return (
        <DlShield
          kind="warn"
          title="Imported, but AcoustID couldn't confirm it. It's waiting in Review."
        />
      );
    case 'force_imported':
      return (
        <DlShield
          kind="warn"
          title="Force-imported as the best available copy after repeated mismatches"
        />
      );
    default:
      return null;
  }
}

/** the row's second line, picked by what the row is doing right now. */
function subLine(
  dl: AdlDownload,
  compact: boolean,
): { text: string; tone?: 'bad' | 'live'; who?: boolean } {
  const d = dl.live_detail;
  const cls = statusClass(dl.status);
  const who = [dl.artist, compact ? '' : dl.album].filter(Boolean).join(' · ');
  if (cls === 'failed' || cls === 'cancelled') {
    return {
      text:
        dl.error ||
        (dl.status === 'not_found' ? 'Nothing matched on any source' : statusLabel(dl.status).text),
      tone: 'bad',
    };
  }
  if (dl.status === 'searching') {
    const bits = [d?.source ? `Searching ${d.source}` : 'Searching'];
    if (d?.query_count) bits.push(`query ${(d.query_index ?? 0) + 1} of ${d.query_count}`);
    if (d?.results != null) bits.push(`${d.results} result${d.results === 1 ? '' : 's'}`);
    return { text: bits.join(' · '), tone: 'live' };
  }
  if (dl.status === 'downloading') {
    const source = dl.download_source || d?.source || '';
    const isPeer = /soulseek/i.test(source) && d?.username;
    const bits = [
      source,
      isPeer ? `from ${d?.username}` : '',
      d?.picked?.quality?.toUpperCase() || '',
      d?.size ? formatBytes(d.size) : '',
    ];
    const line = bits.filter(Boolean).join(' · ');
    return line ? { text: line, tone: 'live' } : { text: who, who: true };
  }
  if (dl.status === 'post_processing')
    return { text: 'Checking the file and importing it', tone: 'live' };
  return { text: who, who: true };
}

/**
 * The top of the open sheet: the cover big, the artist with their photo, the
 * album under it. what the row is, before what happened to it.
 */
function MusicHero({ dl, detail }: { dl: AdlDownload; detail: AdlTaskDetail | null }) {
  const [deadCover, setDeadCover] = useState<string | null>(null);
  const cover = dl.artwork || detail?.thumb_url || '';
  const showCover = Boolean(cover) && deadCover !== cover;
  const podcast = dl.event_type === 'podcast';
  const position =
    dl.batch_total > 1 && dl.batch_position
      ? `Track ${dl.batch_position} of ${dl.batch_total}`
      : '';
  const tag = dl.status === 'completed' ? formatTag(dl.quality || detail?.quality) : '';
  return (
    <div className="dlc-hero dlc-mhero">
      {showCover ? (
        <div className="dlc-hero-wash" style={{ backgroundImage: `url('${cover}')` }} />
      ) : null}
      {showCover ? (
        <img className="dlc-mhero-cover" src={cover} alt="" onError={() => setDeadCover(cover)} />
      ) : dl.artist_image ? (
        <img className="dlc-mhero-cover dlc-mhero-cover-round" src={dl.artist_image} alt="" />
      ) : (
        <div className="dlc-mhero-cover dlc-art-empty">
          {(dl.title || '?').charAt(0).toUpperCase()}
        </div>
      )}
      <div className="dlc-hero-body">
        <div className="dlc-eyebrow">{podcast ? 'Podcast episode' : position || 'Track'}</div>
        <div className="dlc-hero-title">{dl.title || 'Unknown Track'}</div>
        {dl.artist ? (
          <div className="dlc-mhero-artist">
            <DlAvatar src={dl.artist_image} size="md" />
            <span>{dl.artist}</span>
          </div>
        ) : null}
        {dl.album || tag ? (
          <div className="dlc-hero-line">
            {dl.album}
            {tag ? (
              <span className={`dlc-tag${isHighQuality(tag) ? ' dlc-tag-hq' : ''}`}>{tag}</span>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}

/**
 * The expanded sheet's facts. every value is something the row or the
 * fetched detail actually carries. nothing is guessed.
 */
function sheetFacts(dl: AdlDownload, detail: AdlTaskDetail | null, compact: boolean): DlFact[] {
  const d = dl.live_detail;
  const cls = statusClass(dl.status);
  // in flight: the engine's full narration (#1156). query ladder, per-source
  // counts, peer stats, remote queue, held reasons. every line it sent.
  const live = cls === 'active' || cls === 'queued' ? liveDetailLines(dl) : [];
  if (live.length) {
    const file = d?.filename || '';
    return live.map(
      ([label, value]): DlFact =>
        label === 'File' ? [label, value, { mono: true, copy: file || undefined }] : [label, value],
    );
  }
  const source = dl.download_source || d?.source || detail?.source || '';
  const peer = d?.username && /soulseek/i.test(source) ? ` · ${d.username}` : '';
  const position =
    dl.batch_total > 1 && dl.batch_position
      ? `track ${dl.batch_position} of ${dl.batch_total}`
      : '';
  const file = dl.file_path || detail?.file_path || d?.filename || '';
  const asked = dl.expected_title || detail?.expected?.title || '';
  const askedArtist = dl.expected_artist || detail?.expected?.artist || '';
  const got = detail?.downloaded?.title || dl.title;
  return [
    ['Source', source ? `${source}${peer}` : ''],
    [
      'Quality',
      formatTag(dl.quality || detail?.quality) ||
        (d?.picked?.quality ? d.picked.quality.toUpperCase() : ''),
    ],
    ['Size', d?.size ? formatBytes(d.size) : ''],
    ['Speed', dl.status === 'downloading' ? formatSpeed(d?.speed) : ''],
    ['AcoustID', dl.acoustid_result || detail?.acoustid_result || ''],
    [
      'Batch',
      compact
        ? ''
        : [dl.batch_name !== source ? dl.batch_name : '', position].filter(Boolean).join(' · '),
    ],
    // only worth a line when the file we got isn't the one we asked for
    [
      'Asked for',
      asked && asked.toLowerCase() !== String(got || '').toLowerCase()
        ? [askedArtist, asked].filter(Boolean).join(' · ')
        : '',
    ],
    ['File', file, { mono: true, copy: file || undefined }],
  ];
}

export interface AdlRowProps {
  dl: AdlDownload;
  /**
   * Rendered only for cancellable rows with real cancel coordinates.
   *
   * May return a promise. the row locks its button until it settles.
   */
  onCancel?: (dl: AdlDownload) => void | Promise<void>;
  /** Rendered only for queued rows; moves the track to the next unstarted slot. */
  onDownloadNext?: (dl: AdlDownload) => void | Promise<void>;
  /** Group rows: no batch line, the group header already says it. */
  compact?: boolean;
  /** tracklist mode inside an album group: the track number takes the art's place. */
  numbered?: boolean;
  /**
   * Whether a failed row can open the candidate inspector. off for
   * audiobook / music-video / client batches: the inspector searches music
   * sources and /download-candidate restarts a music track.
   */
  canInspect?: boolean;
}

export function AdlRow({
  dl,
  onCancel,
  onDownloadNext,
  compact,
  numbered,
  canInspect = true,
}: AdlRowProps) {
  const cls = statusClass(dl.status);
  /**
   * Locks the cancel button while its request is in flight.
   *
   * two clicks fire two cancels, and the second fails on a task the first
   * already took. `disabled` alone only stops the NEXT event loop turn: two
   * clicks in the same tick both read stale state. the ref is the part that
   * is actually synchronous. state drives the class and disabled attribute.
   *
   * local state is safe here: rows are keyed by task_id, so it survives the
   * 2s poll re-render.
   */
  const cancellingRef = useRef(false);
  const [cancelling, setCancelling] = useState(false);
  const downloadNextRef = useRef(false);
  const [downloadNextPending, setDownloadNextPending] = useState(false);

  /**
   * Row expansion (#1156). terminal real-task rows fetch their merged detail
   * once on first open; history rows and in-flight rows render from what the
   * row already carries.
   */
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<AdlTaskDetail | null>(null);
  const detailRequested = useRef(false);
  const terminal = cls === 'completed' || cls === 'failed' || cls === 'cancelled';

  const toggleOpen = () => {
    const next = !open;
    setOpen(next);
    if (next && terminal && !dl.is_persistent_history && !detailRequested.current) {
      detailRequested.current = true;
      void fetchTaskDetail(dl.task_id).then(setDetail);
    }
  };

  /**
   * Cancel needs (playlist_id, track_index), not task_id. a terminal row has
   * nothing to cancel. track_index is checked against undefined because 0
   * is a valid index.
   */
  const cancellable =
    (cls === 'active' || cls === 'queued') &&
    Boolean(dl.playlist_id) &&
    dl.track_index !== undefined;
  const canDownloadNext =
    ['pending', 'queued'].includes(String(dl.status)) && !dl.is_persistent_history;
  // a live failed task is still in memory, so the inspector can search for
  // it and /download-candidate can restart it. history rows can't.
  const canSearch =
    canInspect &&
    (dl.status === 'failed' || dl.status === 'not_found') &&
    !dl.is_persistent_history;
  const historyId = dl.status === 'completed' ? verificationHistoryId(dl) : null;
  const file = dl.file_path || detail?.file_path || '';

  const downloading = dl.status === 'downloading';
  const pct = Math.round(dl.progress || 0);
  const speed = downloading ? formatSpeed(dl.live_detail?.speed) : '';
  const eta = downloading ? transferEta(dl) : '';
  const sub = subLine(dl, Boolean(compact));
  const tag = dl.status === 'completed' ? formatTag(dl.quality) : '';
  const when =
    dl.status === 'completed'
      ? clockTime(dl.is_persistent_history ? parseStamp(dl.created_at) : parseStamp(dl.timestamp))
      : '';
  const label = statusLabel(dl.status).text;

  const play = historyId ? () => void unverifiedPlay(dl, historyId) : undefined;
  const search = canSearch
    ? () =>
        openDownloadTaskInspector(dl.task_id, {
          name: dl.title,
          artist: dl.artist,
          album: dl.album,
        })
    : undefined;

  const cancel = () => {
    if (!onCancel || cancellingRef.current) return;
    cancellingRef.current = true;
    setCancelling(true);
    void (async () => {
      try {
        await onCancel(dl);
      } catch {
        // the handler owns the toast. the row just keeps a rejection from
        // escaping or leaving the button stuck disabled.
      } finally {
        cancellingRef.current = false;
        setCancelling(false);
      }
    })();
  };

  const menu = [
    ...(play ? [{ key: 'play', label: 'Play', onSelect: play }] : []),
    ...(historyId
      ? [
          {
            key: 'audit',
            label: 'Download details',
            onSelect: () => void unverifiedAudit(historyId),
          },
        ]
      : []),
    ...(file
      ? [{ key: 'copy', label: 'Copy file path', onSelect: () => copyText(file, 'Path copied') }]
      : []),
  ];

  return (
    <>
      <div
        className={`dlc-row dlc-row-${cls}${compact ? ' dlc-row-compact' : ''}${open ? ' dlc-row-open' : ''}`}
        data-task-id={dl.task_id}
        data-batch-id={dl.batch_id || ''}
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onClick={toggleOpen}
        onKeyDown={(e) => {
          if (e.target !== e.currentTarget) return;
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            toggleOpen();
          }
        }}
      >
        <DlLead
          art={numbered ? null : dl.artwork}
          number={numbered ? (dl.batch_position ?? '·') : null}
          progress={downloading ? dl.progress || 0 : null}
          busy={dl.status === 'searching' || dl.status === 'post_processing'}
          failed={cls === 'failed'}
          onPlay={play}
          avatar={dl.artist_image}
          fallback={(dl.title || '?').charAt(0).toUpperCase()}
        />

        <div className="dlc-main">
          <div className="dlc-title">{dl.title || 'Unknown Track'}</div>
          {sub.text ? (
            <div className={`dlc-sub${sub.tone ? ` dlc-sub-${sub.tone}` : ''}`}>
              {sub.who ? <DlAvatar src={dl.artist_image} /> : null}
              {sub.text}
            </div>
          ) : null}
        </div>

        <div className="dlc-meta">
          <RetryTag dl={dl} />
          {downloading ? (
            <>
              <span className="dlc-state dlc-state-active">{pct > 0 ? `${pct}%` : label}</span>
              {speed ? <span className="dlc-speed">{speed}</span> : null}
              {eta ? <span>{eta}</span> : null}
            </>
          ) : dl.status === 'completed' ? (
            <>
              {tag ? (
                <span
                  className={`dlc-tag${isHighQuality(tag) ? ' dlc-tag-hq' : ''}`}
                  title="Audio quality of the downloaded file (read from the file itself)"
                >
                  {tag}
                </span>
              ) : null}
              {dl.download_source && !compact ? <span>{dl.download_source}</span> : null}
              <VerifShield dl={dl} />
              {when ? <span className="dlc-when">{when}</span> : null}
            </>
          ) : sub.tone === 'bad' && dl.error ? null : (
            // a failed row's red line already says what broke. don't say "Failed" twice
            <span className={`dlc-state dlc-state-${dl.status === 'searching' ? 'search' : cls}`}>
              {label}
            </span>
          )}
        </div>

        <div className="dlc-acts">
          {search ? (
            <DlButton
              primary
              icon={DlIcon.search}
              title="See what every source has and pick one"
              onClick={search}
            >
              Search
            </DlButton>
          ) : null}
          {canDownloadNext && onDownloadNext ? (
            <button
              type="button"
              className={`dlc-icon-btn dlc-hover dlc-next${downloadNextPending ? ' dlc-pending' : ''}`}
              title="Download this track next"
              aria-label="Download track next"
              disabled={downloadNextPending}
              onClick={(event) => {
                event.stopPropagation();
                if (downloadNextRef.current) return;
                downloadNextRef.current = true;
                setDownloadNextPending(true);
                void (async () => {
                  try {
                    await onDownloadNext(dl);
                  } catch {
                    // the page handler owns the toast
                  } finally {
                    downloadNextRef.current = false;
                    setDownloadNextPending(false);
                  }
                })();
              }}
            >
              {DlIcon.next}
            </button>
          ) : null}
          {cancellable && onCancel ? (
            <button
              type="button"
              className={`dlc-icon-btn dlc-hover dlc-cancel${cancelling ? ' dlc-pending' : ''}`}
              title="Cancel this download"
              aria-label="Cancel download"
              disabled={cancelling}
              onClick={(event) => {
                event.stopPropagation();
                cancel();
              }}
            >
              {DlIcon.x}
            </button>
          ) : null}
          {!search ? <DlMore items={menu} /> : null}
        </div>

        {downloading && pct > 0 ? (
          <div className="dlc-rowbar" aria-hidden="true">
            <i style={{ width: `${pct}%` }} />
          </div>
        ) : null}
      </div>

      {open ? (
        <DlSheet
          hero={<MusicHero dl={dl} detail={detail} />}
          stepsTitle={
            cls === 'failed' || cls === 'cancelled'
              ? 'What happened'
              : terminal
                ? 'Journey'
                : 'Progress'
          }
          // a finished live task renders from the row first, then fills in
          // once its merged detail lands
          steps={musicJourney(dl, detail, Date.now())}
          factsTitle="Details"
          facts={sheetFacts(dl, detail, Boolean(compact))}
          actions={
            play || search || historyId || (cancellable && onCancel) ? (
              <>
                {play ? (
                  <DlButton primary icon={DlIcon.play} onClick={play}>
                    Play
                  </DlButton>
                ) : null}
                {search ? (
                  <DlButton primary icon={DlIcon.search} onClick={search}>
                    Search manually
                  </DlButton>
                ) : null}
                {historyId ? (
                  <DlButton onClick={() => void unverifiedAudit(historyId)}>
                    Download details
                  </DlButton>
                ) : null}
                <span className="dlc-spacer" />
                {cancellable && onCancel ? (
                  <DlButton disabled={cancelling} onClick={cancel}>
                    Cancel download
                  </DlButton>
                ) : null}
              </>
            ) : null
          }
        />
      ) : null}
    </>
  );
}
