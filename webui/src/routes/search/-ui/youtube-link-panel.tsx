import { useEffect, useState } from 'react';

import type { DownloadTarget } from '../-basic.types';
import type { YoutubeLinkRow, YoutubeLinkState } from '../-youtube-link';

import { formatDuration, resultTitle } from '../-basic.helpers';
import { downloadYoutubeRows } from '../-youtube-link';
import styles from './basic.module.css';

/**
 * what a pasted youtube link points at (#2322): the one video with its
 * Download button, or a playlist's videos with Download all and a tick per
 * row. a row's Download opens the same chooser every files result does, so
 * as-is, matched and tag-it-yourself all work. the bulk buttons send as-is.
 */
export function YoutubeLinkPanel({
  state,
  onDownload,
}: {
  state: YoutubeLinkState;
  onDownload: (target: DownloadTarget) => void;
}) {
  const [picked, setPicked] = useState<ReadonlySet<number>>(new Set());
  const [sending, setSending] = useState(false);
  const data = state.data;

  // a new link is a new list, ticks from the last one point at other videos
  useEffect(() => {
    setPicked(new Set());
  }, [data]);

  if (state.status === 'loading' || state.status === 'error' || !data) {
    return (
      <div className={styles.list} id="search-results-area">
        <div className={`${styles.empty} bs-status-bar`}>
          <p role="status">
            {state.status === 'error' ? state.error : 'Reading the YouTube link...'}
          </p>
        </div>
      </div>
    );
  }

  const rows = data.results;
  const playlist = data.kind === 'playlist';
  const send = async (list: YoutubeLinkRow[]) => {
    if (!list.length || sending) return;
    setSending(true);
    try {
      await downloadYoutubeRows(list);
    } finally {
      setSending(false);
    }
  };
  const toggle = (index: number) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });

  return (
    <div className={styles.list} id="search-results-area" data-youtube-link={data.kind}>
      <div className={styles.linkHead}>
        {data.thumbnail ? (
          <img className={styles.linkThumb} src={data.thumbnail} alt="" loading="lazy" />
        ) : null}
        <div className={styles.meta}>
          <div className={styles.title}>
            <span className={styles.titleText}>{data.title || 'YouTube link'}</span>
          </div>
          <div className={styles.sub}>
            {playlist ? 'YouTube playlist' : 'YouTube video'}
            {data.channel ? (
              <>
                <span className={styles.sep}>·</span>
                {data.channel}
              </>
            ) : null}
            {playlist ? (
              <>
                <span className={styles.sep}>·</span>
                {data.count} {data.count === 1 ? 'video' : 'videos'}
              </>
            ) : null}
          </div>
          {data.truncated ? (
            <div className={styles.sub}>Showing the first {data.count}, the playlist has more.</div>
          ) : null}
        </div>
        {playlist && rows.length ? (
          <div className={styles.linkActions}>
            <button
              type="button"
              className={styles.download}
              disabled={sending || !picked.size}
              onClick={() => void send(rows.filter((_, index) => picked.has(index)))}
            >
              Download {picked.size || ''} picked
            </button>
            <button
              type="button"
              className={styles.download}
              disabled={sending}
              onClick={() => void send(rows)}
            >
              Download all
            </button>
          </div>
        ) : null}
      </div>

      {!rows.length ? (
        <div className={styles.empty}>
          <p role="status">Nothing in this playlist can be downloaded.</p>
        </div>
      ) : null}

      {rows.map((row, index) => {
        const title = resultTitle(row) || row.video_title || 'Untitled video';
        return (
          <div key={`${row.filename}:${index}`} className={styles.item}>
            <div
              className={`${styles.row}${playlist ? ` ${styles.rowPick}` : ''}`}
              data-result-kind="track"
            >
              {playlist ? (
                <input
                  type="checkbox"
                  className={styles.pick}
                  checked={picked.has(index)}
                  aria-label={`Pick ${title}`}
                  onChange={() => toggle(index)}
                />
              ) : null}
              {row.thumbnail ? (
                <img className={styles.linkArt} src={row.thumbnail} alt="" loading="lazy" />
              ) : (
                <div className={styles.linkArt} aria-hidden="true" />
              )}
              <div className={styles.meta}>
                <div className={styles.title}>
                  <span className={styles.titleText}>{title}</span>
                </div>
                <div className={styles.sub}>
                  {row.artist || row.channel || 'Unknown artist'}
                  {row.channel && row.channel !== row.artist ? (
                    <>
                      <span className={styles.sep}>·</span>
                      {row.channel}
                    </>
                  ) : null}
                </div>
              </div>
              <div className={styles.facts}>
                <span className={styles.dim}>{formatDuration(row.duration)}</span>
              </div>
              <button
                type="button"
                className={styles.download}
                aria-label={`Download ${title}`}
                onClick={() => onDownload({ kind: 'track', track: row })}
              >
                <svg viewBox="0 0 16 16" fill="none" aria-hidden="true">
                  <path
                    d="M8 2.5v8M4.5 7 8 10.5 11.5 7M3 13.5h10"
                    stroke="currentColor"
                    strokeWidth="1.7"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
                <span className={styles.downloadLabel}>Download</span>
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}
