/**
 * stats for nerds: everything about the stream, live. what the file is,
 * where it comes from and how long finding it took, whether it plays as it
 * is or through the converter (and what that converts, on what), the
 * tracks in use, and the picture the browser is actually drawing: its
 * size, frames dropped, what's buffered ahead, the speed.
 *
 * the element's numbers are read twice a second while it's open, and not
 * at all while it isn't.
 */

import { useEffect, useState } from 'react';

import type { PlayerState } from '../../-engine/engine';

import { viaName } from '../../-engine/describe';
import { clock } from '../../-engine/timeline';
import { audioLabel, subtitleLabel } from '../../-engine/tracks';
import { CloseIcon } from './icons';
import s from './playback.module.css';

interface Live {
  width: number;
  height: number;
  dropped: number | null;
  frames: number | null;
  ahead: number;
  readyState: number;
  networkState: number;
}

const READY = ['nothing', 'metadata', 'current frame', 'future frames', 'enough to play'];
const NETWORK = ['empty', 'idle', 'loading', 'no source'];

function bytes(n?: number | null): string | null {
  if (!n) return null;
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v < 10 && i > 0 ? 2 : 1)} ${units[i]}`;
}

function useLive(el: HTMLVideoElement | null, position: number, offset: number): Live | null {
  const [live, setLive] = useState<Live | null>(null);
  useEffect(() => {
    if (!el) return;
    const read = () => {
      const q = el.getVideoPlaybackQuality?.();
      const end = el.buffered.length ? el.buffered.end(el.buffered.length - 1) : 0;
      setLive({
        width: el.videoWidth,
        height: el.videoHeight,
        dropped: q ? q.droppedVideoFrames : null,
        frames: q ? q.totalVideoFrames : null,
        ahead: Math.max(0, end - el.currentTime),
        readyState: el.readyState,
        networkState: el.networkState,
      });
    };
    read();
    const t = window.setInterval(read, 500);
    return () => window.clearInterval(t);
  }, [el, position, offset]);
  return live;
}

export function StatsOverlay({
  state,
  video,
  onClose,
}: {
  state: PlayerState;
  video: HTMLVideoElement | null;
  onClose: () => void;
}) {
  const live = useLive(video, Math.floor(state.position), 0);
  const item = state.item;
  if (!item) return null;
  const file = state.source?.file;
  const verdict = state.verdict;
  const t = state.transcode;
  const audio = state.tracks?.audio.find((a) => a.index === state.audioTrack);
  const sub = state.tracks?.subtitles.find((x) => x.id === state.subtitle);
  const bitrate =
    file?.size_bytes && (file.runtime_seconds || state.duration)
      ? `${((file.size_bytes * 8) / (file.runtime_seconds || state.duration) / 1_000_000).toFixed(1)} Mbps`
      : null;

  const rows: [string, string | null | undefined][] = [
    ['Title', item.title],
    [
      'Item',
      `${item.type} ${item.id}${state.source?.fileId ? ` · file ${state.source.fileId}` : ''}`,
    ],
    ['Container', verdict?.container?.replace('.', '').toUpperCase() || null],
    ['Codecs', [verdict?.video, verdict?.audio].filter(Boolean).join(' / ').toUpperCase() || null],
    ['Resolution', file?.height ? `${file.height}p` : null],
    ['HDR', file?.dynamic_range ?? (file ? 'SDR' : null)],
    ['File size', bytes(file?.size_bytes)],
    ['Average bitrate', bitrate],
    ['Comes from', state.source ? viaName(state.source.via) : null],
    [
      'Finding it took',
      state.source?.tookMs != null ? `${(state.source.tookMs / 1000).toFixed(2)}s` : null,
    ],
    [
      'Direct play',
      verdict
        ? verdict.verdict === 'yes'
          ? 'Yes'
          : verdict.verdict === 'maybe'
            ? 'Possibly'
            : 'No'
        : null,
    ],
    ['Why not', verdict?.reasons?.length ? verdict.reasons.join('; ') : null],
    ['Playing as', t ? 'Converted' : 'Direct'],
    [
      'Video',
      t ? (t.video === 'copy' ? 'Copied' : `To H.264${t.encoder ? ` on ${t.encoder}` : ''}`) : null,
    ],
    ['Audio', t ? (t.audio === 'copy' ? 'Copied' : 'To AAC stereo') : null],
    ['Quality', t?.quality && t.quality !== 'original' ? t.quality : state.quality],
    ['HDR tone mapping', t?.tonemap ? 'On' : null],
    ['Audio track', audio ? `${audioLabel(audio).label} (${audioLabel(audio).detail})` : null],
    [
      'Subtitles',
      sub
        ? `${subtitleLabel(sub).label} · ${sub.kind === 'image' ? 'drawn in' : `${state.cues.length} lines`}`
        : 'Off',
    ],
    ['Picture', live?.width ? `${live.width} × ${live.height}` : null],
    [
      'Frames dropped',
      live?.frames != null
        ? `${live.dropped} of ${live.frames}${live.frames ? ` (${(((live.dropped ?? 0) / live.frames) * 100).toFixed(2)}%)` : ''}`
        : null,
    ],
    ['Buffered ahead', live ? `${live.ahead.toFixed(1)}s` : null],
    ['Position', `${clock(state.position)} / ${clock(state.duration)}`],
    ['Speed', `${state.rate}x`],
    ['Volume', state.muted ? 'Muted' : `${Math.round(state.volume * 100)}%`],
    ['Player', `${state.status}${state.phase ? ` · ${state.phase}` : ''}`],
    [
      'Browser',
      live
        ? `${READY[live.readyState] ?? live.readyState} · network ${NETWORK[live.networkState] ?? live.networkState}`
        : null,
    ],
    ['Type', state.source?.mime ?? null],
  ];

  return (
    <section className={s.stats} aria-label="Stats for nerds">
      <div className={s.statsHead}>
        <span className={s.statsTitle}>Stats for nerds</span>
        <button type="button" className={s.ctl} aria-label="Close stats" onClick={onClose} data-nav>
          <CloseIcon />
        </button>
      </div>
      <dl className={s.statsList}>
        {rows
          .filter(([, v]) => v !== null && v !== undefined && v !== '')
          .map(([k, v]) => (
            <div key={k} className={s.statsRow}>
              <dt>{k}</dt>
              <dd>{v}</dd>
            </div>
          ))}
      </dl>
    </section>
  );
}
