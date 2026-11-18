/**
 * Now playing: the server pulse, then one card per stream. A card opens to
 * the file against what is sent, why it is converted, and the transcoder,
 * connection, player and session facts, with Stop and (Jellyfin) Send
 * message.
 */

import type { FormEvent, KeyboardEvent } from 'react';

import { memo, useEffect, useId, useRef, useState } from 'react';

import type {
  ActivityPayload,
  BandwidthSeries,
  Pulse,
  Session,
} from '@/shell/server-activity-types';

import { imageSrc, sendMessage, stopStream } from './api';
import {
  METHOD_LABEL,
  clock,
  connectionNote,
  deviceLabel,
  duration,
  isBookKind,
  mbps,
  mbpsNumber,
  pausedPhrase,
  qualityBadges,
  sessionSubtitle,
  splitReason,
  streamsMix,
  timeOfDay,
  transcoderEngine,
  transcoderLine,
  transcoderMix,
  whoLine,
} from './format';
import { ChevronDown, MessageIcon, OpenIcon, QuietIcon, ServerIcon, StopIcon } from './icons';
import { linkLabel, openLink } from './links';
import { useGlide, usePresence } from './live';
import n from './now-playing.module.css';
import {
  Avatar,
  Badge,
  Button,
  EmptyState,
  ErrorState,
  Fact,
  MethodBadge,
  Poster,
  Skel,
  cx,
  useDrawn,
} from './ui';

// ── the pulse strip ──

/** the blurred backdrop behind a card never needs more than this */
const BACKDROP_WIDTH = 480;
export function PulseStrip({
  pulse,
  bandwidth,
  at,
}: {
  pulse: Pulse;
  bandwidth: BandwidthSeries;
  at: number;
}) {
  const cap = pulse.upload_cap_kbps;
  const capShare = cap ? pulse.remote_bandwidth_kbps / cap : 0;
  return (
    <section className={n.pulse} aria-label="Server right now">
      <div className={n.pulseGrid}>
        <div className={n.pulseCell}>
          <div className={n.pulseKey}>Streams</div>
          <div className={n.pulseValue}>{pulse.streams}</div>
          <div className={n.pulseSub}>
            {streamsMix(pulse)
              .split(' · ')
              .map((bit) => (
                <span key={bit}>{bit}</span>
              ))}
          </div>
        </div>
        <div className={n.pulseCell}>
          <div className={n.pulseKey}>Bandwidth</div>
          <div className={n.pulseValue}>
            {mbpsNumber(pulse.total_bandwidth_kbps)}
            <small>Mbps</small>
          </div>
          <div className={n.pulseSub}>
            {cap
              ? `${mbpsNumber(pulse.remote_bandwidth_kbps)} remote of ${mbps(cap)} upload`
              : `${mbpsNumber(pulse.remote_bandwidth_kbps)} remote, ${mbpsNumber(pulse.local_bandwidth_kbps)} home`}
          </div>
          {cap ? (
            <div
              className={n.cap}
              role="meter"
              aria-label="Remote bandwidth against your upload"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(capShare * 100)}
              data-level={capShare >= 1 ? 'over' : capShare >= 0.8 ? 'high' : undefined}
            >
              <i style={{ transform: `scaleX(${Math.min(1, capShare)})` }} />
            </div>
          ) : null}
        </div>
        <div className={n.pulseCell}>
          <div className={n.pulseKey}>Transcoder</div>
          <div className={n.pulseValue}>
            {pulse.transcoders.running}
            <small>running</small>
          </div>
          <div className={n.pulseSub}>
            {pulse.transcoders.running ? <span>{transcoderMix(pulse)}</span> : null}
            {pulse.cpu ? <span>CPU {Math.round(pulse.cpu.host_pct)}%</span> : null}
            {!pulse.transcoders.running && !pulse.cpu ? <span>None running</span> : null}
          </div>
          {pulse.cpu ? (
            <div
              className={n.cap}
              role="meter"
              aria-label="Server CPU"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(pulse.cpu.host_pct)}
              data-level={
                pulse.cpu.host_pct >= 90 ? 'over' : pulse.cpu.host_pct >= 75 ? 'high' : undefined
              }
            >
              <i style={{ transform: `scaleX(${Math.min(100, pulse.cpu.host_pct) / 100})` }} />
            </div>
          ) : null}
        </div>
      </div>
      <Sparkline series={bandwidth} at={at} />
    </section>
  );
}

const SPARK_W = 400;
const SPARK_H = 38;
/** a gap longer than this breaks the line: no poll ran then */
const GAP_S = 30;

export function sparkPaths(series: BandwidthSeries, at: number) {
  const span = series.window_s || 600;
  const start = at - span;
  const points = series.points.filter((p) => p.t >= start - 5).sort((a, b) => a.t - b.t);
  const top = Math.max(series.peak_kbps, ...points.map((p) => p.total_kbps), 1000) * 1.15;
  const x = (t: number) => Math.max(0, Math.min(SPARK_W, ((t - start) / span) * SPARK_W));
  const y = (kbps: number) => SPARK_H - 3 - (kbps / top) * (SPARK_H - 7);
  const runs: (typeof points)[] = [];
  for (const p of points) {
    const run = runs[runs.length - 1];
    if (run && p.t - run[run.length - 1].t <= GAP_S) run.push(p);
    else runs.push([p]);
  }
  // one sample is still a line: hold it back to the previous poll
  for (const run of runs) if (run.length === 1) run.unshift({ ...run[0], t: run[0].t - 3 });
  const line = (key: 'total_kbps' | 'remote_kbps') =>
    runs
      .map((run) =>
        run.map((p, i) => `${i ? 'L' : 'M'}${x(p.t).toFixed(1)} ${y(p[key]).toFixed(1)}`).join(' '),
      )
      .join(' ');
  const area = runs
    .map((run) => {
      const first = x(run[0].t).toFixed(1);
      const last = x(run[run.length - 1].t).toFixed(1);
      const path = run.map((p) => `L${x(p.t).toFixed(1)} ${y(p.total_kbps).toFixed(1)}`).join(' ');
      return `M${first} ${SPARK_H} ${path} L${last} ${SPARK_H} Z`;
    })
    .join(' ');
  const end = points[points.length - 1];
  return {
    total: line('total_kbps'),
    remote: line('remote_kbps'),
    area,
    end: end ? { x: x(end.t), y: y(end.total_kbps) } : null,
  };
}

function Sparkline({ series, at }: { series: BandwidthSeries; at: number }) {
  const gradient = useId().replace(/:/g, '');
  const sparse = series.points.length < 3;
  const paths = sparkPaths(sparse ? { ...series, points: [] } : series, at);
  const minutes = Math.round((series.window_s || 600) / 60);
  return (
    <div className={n.spark}>
      <div className={n.sparkHead}>
        <span>Bandwidth, last {minutes} minutes</span>
        <span>peak {mbps(series.peak_kbps)}</span>
      </div>
      <div className={n.sparkBox}>
        {sparse ? <span className={n.sparkWait}>Drawing as the minutes pass</span> : null}
        <svg
          viewBox={`0 0 ${SPARK_W} ${SPARK_H}`}
          preserveAspectRatio="none"
          role="img"
          aria-label={`Bandwidth over the last ${minutes} minutes, peak ${mbps(series.peak_kbps)}`}
        >
          <defs>
            <linearGradient id={gradient} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" style={{ stopColor: 'var(--accent)', stopOpacity: 0.32 }} />
              <stop offset="1" style={{ stopColor: 'var(--accent)', stopOpacity: 0 }} />
            </linearGradient>
          </defs>
          <path d={paths.area} fill={`url(#${gradient})`} />
          <path className={n.sparkRemote} d={paths.remote} vectorEffect="non-scaling-stroke" />
          <path className={n.sparkLine} d={paths.total} vectorEffect="non-scaling-stroke" />
        </svg>
        {paths.end ? (
          <span
            className={n.sparkNow}
            style={{
              left: `${(paths.end.x / SPARK_W) * 100}%`,
              top: `${(paths.end.y / SPARK_H) * 100}%`,
            }}
            aria-hidden="true"
          />
        ) : null}
      </div>
    </div>
  );
}

// ── one stream ──

const STATE_LABEL = { playing: 'Playing', paused: 'Paused', buffering: 'Buffering' } as const;

type Mode = null | 'stop' | 'message';

export const StreamCard = memo(function StreamCard({
  session,
  receivedAt,
  now,
  multiServer,
  open,
  leaving,
  presets,
  onToggle,
  onControlled,
  onNavigate,
}: {
  session: Session;
  receivedAt: number;
  now: number;
  multiServer: boolean;
  open: boolean;
  leaving?: boolean;
  presets: string[];
  onToggle: (id: string) => void;
  onControlled: () => void;
  onNavigate: () => void;
}) {
  const s = session;
  const detailId = useId();
  const [mode, setMode] = useState<Mode>(null);
  const live = useGlide(s, receivedAt, now);
  const book = isBookKind(s.kind) ? s.book?.progress : null;
  const art = imageSrc(s.backdrop, s.art || s.thumb, BACKDROP_WIDTH);
  const badges = qualityBadges(s);
  const who = whoLine(s, multiServer);
  const drawn = useDrawn(open);
  useEffect(() => {
    if (!open) setMode(null);
  }, [open]);

  const pct = s.duration_ms ? (live / s.duration_ms) * 100 : s.progress_pct;
  const bufferEnd =
    s.transcoder?.buffered_s != null && s.duration_ms
      ? Math.min(100, ((s.offset_ms + s.transcoder.buffered_s * 1000) / s.duration_ms) * 100)
      : null;
  const elapsed = live - s.offset_ms;

  return (
    <article
      className={n.card}
      data-state={s.state}
      data-open={open || undefined}
      data-leaving={leaving || undefined}
      aria-label={`${s.title}, ${STATE_LABEL[s.state].toLowerCase()} for ${s.user}`}
    >
      {art ? (
        <div className={n.art} style={{ backgroundImage: `url("${art}")` }} aria-hidden="true" />
      ) : null}
      <div className={n.scrim} aria-hidden="true" />
      <div className={n.inner}>
        <button
          type="button"
          className={n.head}
          aria-expanded={open}
          aria-controls={detailId}
          onClick={() => onToggle(s.id)}
        >
          <Poster thumb={s.thumb} image={s.image} kind={s.kind} />
          <span className={n.info}>
            <span className={n.titleRow}>
              <span className={n.title}>{s.title}</span>
              <span className={n.state} data-state={s.state}>
                {s.state !== 'playing' ? <i aria-hidden="true" /> : null}
                {STATE_LABEL[s.state]}
              </span>
              <span className={n.chevron} aria-hidden="true">
                <ChevronDown size={13} />
              </span>
            </span>
            <span className={n.sub}>{sessionSubtitle(s)}</span>
            <span className={n.who}>
              <Avatar name={s.user} url={s.user_avatar} />
              <b>{s.user}</b>
              {who.map((bit) => (
                <span key={bit} className={n.whoBit}>
                  <span className={n.sep} aria-hidden="true">
                    ·
                  </span>
                  {bit}
                </span>
              ))}
            </span>
            <span className={n.badges}>
              <MethodBadge method={s.playback.method} />
              {badges.map((badge) => (
                <Badge key={badge.key} badge={badge} />
              ))}
            </span>
          </span>
        </button>

        <div className={n.progress}>
          {book ? (
            <BookProgressBars session={s} elapsed={elapsed} />
          ) : (
            <>
              <div
                className={n.bar}
                role="progressbar"
                aria-label="Progress"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={Math.round(pct)}
              >
                {bufferEnd != null ? (
                  <span
                    className={n.buffer}
                    style={{ transform: `scaleX(${Math.max(pct, bufferEnd) / 100})` }}
                  />
                ) : null}
                <span className={n.fill} style={{ transform: `scaleX(${pct / 100})` }} />
              </div>
              <div className={n.times}>
                <span>{clock(live)}</span>
                <span>{s.duration_ms ? `-${clock(s.duration_ms - live)}` : ''}</span>
              </div>
            </>
          )}
        </div>
      </div>

      <div className={n.detailWrap} id={detailId} data-open={open || undefined} inert={!open}>
        <div className={n.detailClip}>
          {drawn ? (
            <StreamDetail
              session={s}
              mode={mode}
              setMode={setMode}
              presets={presets}
              onControlled={onControlled}
              onNavigate={onNavigate}
            />
          ) : null}
        </div>
      </div>
    </article>
  );
});

function BookProgressBars({ session, elapsed }: { session: Session; elapsed: number }) {
  const p = session.book!.progress!;
  const ch = p.chapter;
  const chPos = Math.min(ch.duration_ms, ch.position_ms + elapsed);
  const chPct = ch.duration_ms ? (chPos / ch.duration_ms) * 100 : 0;
  const bookPos = Math.min(p.duration_ms, p.position_ms + elapsed);
  const bookPct = p.duration_ms ? (bookPos / p.duration_ms) * 100 : p.percent;
  const left = Math.max(0, p.left_ms - elapsed);
  return (
    <>
      <div
        className={n.bar}
        role="progressbar"
        aria-label={`Chapter ${ch.index} progress`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(chPct)}
      >
        <span className={n.fill} style={{ transform: `scaleX(${chPct / 100})` }} />
      </div>
      <div className={n.times}>
        <span>Chapter {clock(chPos)}</span>
        <span>-{clock(ch.duration_ms - chPos)}</span>
      </div>
      <div
        className={cx(n.bar, n.bookBar)}
        role="progressbar"
        aria-label="Book progress"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(bookPct)}
      >
        <span className={n.fill} style={{ transform: `scaleX(${bookPct / 100})` }} />
      </div>
      <div className={n.times}>
        <span>Book {Math.round(bookPct)}%</span>
        <span>{duration(left)} left in the book</span>
      </div>
    </>
  );
}

// ── the open card ──

function CompareTable({ session }: { session: Session }) {
  const pb = session.playback;
  const rows: { key: string; from: string | null; to: string | null; changed: boolean }[] = [];
  if (pb.video) {
    rows.push({
      key: 'Video',
      from: pb.video.source?.label ?? null,
      to: pb.video.stream?.label ?? pb.video.source?.label ?? null,
      changed: pb.video.changed,
    });
  }
  if (pb.audio) {
    rows.push({
      key: 'Audio',
      from: pb.audio.source?.label ?? null,
      to: pb.audio.stream?.label ?? pb.audio.source?.label ?? null,
      changed: pb.audio.changed,
    });
  }
  if (pb.subtitles) {
    rows.push({
      key: 'Subtitles',
      from: pb.subtitles.source.label,
      to: pb.subtitles.stream.label,
      changed: pb.subtitles.changed,
    });
  }
  if (pb.container.source || pb.container.stream) {
    rows.push({
      key: 'Container',
      from: pb.container.source,
      to: pb.container.stream ?? pb.container.source,
      changed: pb.container.changed,
    });
  }
  if (pb.bitrate_kbps.source || pb.bitrate_kbps.stream) {
    const from = pb.bitrate_kbps.source;
    const to = pb.bitrate_kbps.stream ?? from;
    rows.push({
      key: 'Bitrate',
      from: from ? mbps(from) : null,
      to: to ? mbps(to) : null,
      changed: !!from && !!to && Math.abs(from - to) > Math.max(200, from * 0.05),
    });
  }
  if (!rows.length) return null;

  // played as stored: one column says it plainly
  if (pb.method === 'direct_play') {
    return (
      <div className={n.asIs}>
        <div className={n.cmpHead}>The file, played as is</div>
        <dl className={n.asIsList}>
          {rows.map((row) => (
            <div key={row.key}>
              <dt>{row.key}</dt>
              <dd>{row.from ?? row.to ?? 'Not known'}</dd>
            </div>
          ))}
        </dl>
      </div>
    );
  }
  return (
    <table className={n.cmp}>
      <thead>
        <tr>
          <td />
          <th scope="col">Source</th>
          <td />
          <th scope="col">Streaming</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.key}>
            <th scope="row">{row.key}</th>
            <td>{row.from ?? 'Not known'}</td>
            <td className={n.cmpArrow} aria-label="becomes">
              →
            </td>
            <td data-changed={row.changed || undefined}>{row.to ?? 'Not known'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function StreamDetail({
  session: s,
  mode,
  setMode,
  presets,
  onControlled,
  onNavigate,
}: {
  session: Session;
  mode: Mode;
  setMode: (mode: Mode) => void;
  presets: string[];
  onControlled: () => void;
  onNavigate: () => void;
}) {
  const stopButton = useRef<HTMLButtonElement>(null);
  const messageButton = useRef<HTMLButtonElement>(null);
  const t = s.transcoder;
  const c = s.connection;
  const p = s.player;
  const paused = pausedPhrase(s.pause_count, s.paused_ms);
  const serverWord = s.server === 'plex' ? 'Plex' : 'Jellyfin';
  const server =
    s.server_name && s.server_name.toLowerCase() !== s.server
      ? `${serverWord} · ${s.server_name}`
      : serverWord;
  const reason = s.reason ? splitReason(s.reason.text) : null;
  const playerDetail = [
    [p.platform, p.platform_version].filter(Boolean).join(' '),
    p.name && p.name !== p.product ? p.name : null,
  ]
    .filter(Boolean)
    .join(' · ');
  const place = c.location?.label;
  const note = connectionNote(c.secure, c.relayed);
  const close = (back: 'stop' | 'message') => {
    setMode(null);
    requestAnimationFrame(() => (back === 'stop' ? stopButton : messageButton).current?.focus());
  };

  return (
    <div className={n.detail}>
      {s.reason && reason ? (
        <div className={n.reason} data-method={s.playback.method}>
          <span className={n.reasonDot} aria-hidden="true" />
          <div>
            <b>{reason[0]}</b> {reason[1]}
            {s.reason.inferred ? (
              <em>
                Reason worked out by SoulSync from the stream details.{' '}
                {s.server === 'plex' ? "Plex doesn't state one." : ''}
              </em>
            ) : null}
          </div>
        </div>
      ) : null}

      <CompareTable session={s} />

      <div className={n.facts}>
        {t ? (
          <Fact
            label="Transcoder"
            value={transcoderEngine(t)}
            sub={transcoderLine(t)}
            meter={t.progress_pct}
          />
        ) : (
          <Fact
            label="Stream"
            value={s.bandwidth_kbps ? mbps(s.bandwidth_kbps) : METHOD_LABEL[s.playback.method]}
            sub={
              s.playback.method === 'direct_play'
                ? 'Sent as stored, nothing converted'
                : 'Repackaged, the picture and sound untouched'
            }
          />
        )}
        <Fact
          label="Connection"
          value={[c.remote ? 'Remote' : 'Home', c.ip].filter(Boolean).join(' · ')}
          sub={
            [place ?? (c.remote ? 'Place not known yet' : null), note]
              .filter(Boolean)
              .join(' · ') || null
          }
        />
        <Fact
          label="Player"
          value={[p.product || deviceLabel(p), p.version].filter(Boolean).join(' ')}
          sub={playerDetail || null}
        />
        <Fact
          label="Session"
          value={s.started_at ? `Started ${timeOfDay(s.started_at)}` : 'Just started'}
          sub={[paused ?? 'No pauses', server].join(' · ')}
        />
      </div>

      {mode ? (
        <ControlSheet
          session={s}
          mode={mode}
          presets={presets}
          onCancel={() => close(mode)}
          onDone={onControlled}
        />
      ) : (
        <div className={n.actions}>
          {s.controls.stop ? (
            <Button tone="stop" onClick={() => setMode('stop')} ref={stopButton}>
              <StopIcon size={11} />
              Stop stream…
            </Button>
          ) : s.controls.stop_note ? (
            <span className={n.note}>{s.controls.stop_note}</span>
          ) : null}
          {s.controls.message ? (
            <Button tone="ghost" onClick={() => setMode('message')} ref={messageButton}>
              <MessageIcon size={13} />
              Send message…
            </Button>
          ) : null}
          {s.link ? (
            <span className={n.linkSlot}>
              <Button
                tone="link"
                onClick={() => {
                  onNavigate();
                  void openLink(s.link!, s.music?.artist ?? s.title);
                }}
              >
                {linkLabel(s.link)}
                <OpenIcon size={13} />
              </Button>
            </span>
          ) : null}
        </div>
      )}
    </div>
  );
}

/** the first sentence of a preset is its chip */
export function presetChip(text: string): string {
  const first = text.split(/(?<=[.!?])\s/)[0] ?? text;
  return first.replace(/[.!?]$/, '').split(',')[0];
}

function ControlSheet({
  session: s,
  mode,
  presets,
  onCancel,
  onDone,
}: {
  session: Session;
  mode: 'stop' | 'message';
  presets: string[];
  onCancel: () => void;
  onDone: () => void;
}) {
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const area = useRef<HTMLTextAreaElement>(null);
  const fieldId = useId();
  const device = deviceLabel(s.player) || 'player';
  const stop = mode === 'stop';

  useEffect(() => {
    area.current?.focus({ preventScroll: true });
  }, []);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy || done) return;
    if (!stop && !text.trim()) {
      setError('Write a message first.');
      area.current?.focus();
      return;
    }
    setBusy(true);
    setError(null);
    const result = stop
      ? await stopStream(s.session_key, text.trim())
      : await sendMessage(s.session_key, text.trim());
    setBusy(false);
    if (!result.ok || !result.data.ok) {
      setError(
        (result.ok ? result.data.error : result.error) ||
          (stop ? "The stream didn't stop." : "The message didn't send."),
      );
      return;
    }
    if (stop) {
      setDone(
        result.data.message_shown === false
          ? `Stopped. ${s.user}'s ${device} can't show messages, so the stream just ended.`
          : `Stopped. ${s.user} saw your message.`,
      );
      onDone();
    } else {
      setDone(`Sent. It shows on ${s.user}'s ${device} for a few seconds.`);
      setTimeout(onCancel, 2200);
    }
  };

  const onKey = (event: KeyboardEvent) => {
    if (event.key === 'Escape') {
      event.stopPropagation();
      onCancel();
    }
    if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) void submit(event);
  };

  return (
    <form className={n.sheet} onSubmit={submit} onKeyDown={onKey} data-mode={mode}>
      <div className={n.sheetTitle}>
        {stop ? `Stop ${s.title} for ${s.user}?` : `Send ${s.user} a message`}
      </div>
      <div className={n.sheetSub}>
        {stop
          ? `${s.user}'s ${device} shows this message when the stream ends.`
          : `It shows on ${s.user}'s ${device}. The stream keeps playing.`}
      </div>
      <label className={n.srOnly} htmlFor={fieldId}>
        Message
      </label>
      <textarea
        id={fieldId}
        ref={area}
        className={n.textarea}
        rows={2}
        maxLength={240}
        value={text}
        disabled={busy || !!done}
        placeholder={stop ? 'The server administrator ended this stream.' : 'Write a message'}
        onChange={(event) => setText(event.target.value)}
      />
      {presets.length ? (
        <div className={n.presets} role="group" aria-label="Ready-made messages">
          {presets.map((preset) => (
            <button
              key={preset}
              type="button"
              className={n.preset}
              data-on={text === preset || undefined}
              aria-pressed={text === preset}
              title={preset}
              disabled={busy || !!done}
              onClick={() => {
                setText(preset);
                area.current?.focus();
              }}
            >
              {presetChip(preset)}
            </button>
          ))}
          <button
            type="button"
            className={n.preset}
            disabled={busy || !!done}
            onClick={() => {
              setText('');
              area.current?.focus();
            }}
          >
            Custom…
          </button>
        </div>
      ) : null}
      {error ? (
        <div className={n.sheetError} role="alert">
          {error}
        </div>
      ) : null}
      {done ? (
        <div className={n.sheetDone} role="status">
          {done}
        </div>
      ) : (
        <div className={n.sheetRow}>
          <Button tone="ghost" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
          <Button type="submit" tone={stop ? 'go' : 'primary'} busy={busy} disabled={busy}>
            {busy ? (stop ? 'Stopping…' : 'Sending…') : stop ? 'Stop stream' : 'Send message'}
          </Button>
        </div>
      )}
    </form>
  );
}

// ── the view ──

function NowPlayingSkeleton() {
  return (
    <div aria-busy="true" aria-label="Loading what's playing">
      <div className={n.pulse}>
        <div className={n.pulseGrid}>
          {[0, 1, 2].map((i) => (
            <div key={i} className={n.pulseCell}>
              <Skel w={56} h={10} />
              <Skel w={44} h={22} className={n.skelGap} />
              <Skel w={110} h={10} className={n.skelGap} />
            </div>
          ))}
        </div>
        <div className={n.spark}>
          <Skel w="100%" h={38} />
        </div>
      </div>
      <div className={n.list}>
        {[0, 1, 2].map((i) => (
          <div key={i} className={n.card} data-skeleton>
            <div className={n.inner}>
              <div className={n.head}>
                <Skel w={58} h={87} />
                <span className={n.info}>
                  <Skel w="62%" h={14} />
                  <Skel w="34%" h={11} className={n.skelGap} />
                  <Skel w="54%" h={11} className={n.skelGapLg} />
                  <span className={n.badges}>
                    <Skel w={86} h={20} />
                    <Skel w={64} h={20} />
                    <Skel w={56} h={20} />
                  </span>
                </span>
              </div>
              <div className={n.progress}>
                <Skel w="100%" h={3} />
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export function NowPlaying({
  data,
  error,
  loading,
  receivedAt,
  now,
  presets,
  onRetry,
  onClose,
  onSeeHistory,
}: {
  data: ActivityPayload | null;
  error: string | null;
  loading: boolean;
  receivedAt: number;
  now: number;
  presets: string[];
  onRetry: () => void;
  onClose: () => void;
  onSeeHistory: () => void;
}) {
  const [open, setOpen] = useState<Set<string>>(() => new Set());
  const sessions = data?.ok ? data.sessions : [];
  const shown = usePresence(sessions, (x) => x.id);
  const multiServer = (data?.servers?.length ?? 0) > 1;

  const toggle = (id: string) =>
    setOpen((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  if (!data) {
    if (loading) return <NowPlayingSkeleton />;
    return <ErrorState message={error || "Couldn't load what's playing."} onRetry={onRetry} />;
  }

  if (!data.ok) {
    if (data.reason === 'no_server') {
      return (
        <EmptyState
          icon={<ServerIcon size={22} />}
          title="Connect Plex or Jellyfin"
          action={
            <Button
              tone="ghost"
              onClick={() => {
                onClose();
                void window.navigateToPage?.('settings');
              }}
            >
              Open Settings
            </Button>
          }
        >
          Server Activity shows who is playing what on your media server, how it plays and why. Add
          Plex or Jellyfin under Settings, Connections, and streams appear here.
        </EmptyState>
      );
    }
    return (
      <EmptyState
        icon={<ServerIcon size={22} />}
        title={data.reason === 'unreachable' ? "Can't reach your server" : 'Something went wrong'}
        action={
          <Button tone="ghost" onClick={onRetry}>
            Try again
          </Button>
        }
      >
        {data.message || 'SoulSync asked the media server what is playing and got no answer.'}
      </EmptyState>
    );
  }

  return (
    <div className={n.view}>
      <PulseStrip pulse={data.summary} bandwidth={data.bandwidth} at={data.at} />
      {shown.length ? (
        <div className={n.list}>
          {shown.map(({ item, key, leaving }) => (
            <StreamCard
              key={key}
              session={item}
              receivedAt={receivedAt}
              now={now}
              multiServer={multiServer}
              open={open.has(key)}
              leaving={leaving}
              presets={presets}
              onToggle={toggle}
              onControlled={onRetry}
              onNavigate={onClose}
            />
          ))}
        </div>
      ) : (
        <EmptyState
          icon={<QuietIcon size={22} />}
          title="Nothing playing right now"
          action={
            <Button tone="ghost" onClick={onSeeHistory}>
              See history
            </Button>
          }
        >
          Streams show up here the moment someone presses play, on Plex or Jellyfin. What was played
          before is in History.
        </EmptyState>
      )}
    </div>
  );
}
