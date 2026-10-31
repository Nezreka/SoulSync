/**
 * The video downloads list: season-pack grouping, the rows, and the detail
 * sheet (cast and art stay, Broque's hard requirement).
 *
 * Same row grammar as the music side (dl-parts), so a download reads the
 * same whichever side it's on.
 */

import { useEffect, useRef } from 'react';

import { useProfile } from '@/platform/shell/route-controllers';

import type { VdlDownload, VdlFilter, VdlMeta, VdlYtMeta } from '../-vdl.types';
import type { DlFact } from './dl-parts';

import {
  clockTime,
  formatTag,
  isHighQuality,
  parseStamp,
  vdlBackups,
  videoJourney,
} from '../-dl.format';
import {
  vdlCanBlock,
  vdlCardTitle,
  vdlFmtAge,
  vdlFmtElapsed,
  vdlFmtEta,
  vdlFmtRuntime,
  vdlFmtSize,
  vdlFmtSpeed,
  vdlFmtViews,
  vdlGroupKey,
  vdlIsActive,
  vdlIsFail,
  vdlIsPackRow,
  vdlIsRemovable,
  vdlKindIcon,
  vdlMatchesFilter,
  vdlPad2,
  vdlParseCtx,
  vdlSplitGroup,
  vdlRowType,
  vdlStatusOf,
} from '../-vdl.helpers';
import { DlButton, DlIcon, DlLead, DlMore, DlSheet, copyText } from './dl-parts';

/* ── shared actions ────────────────────────────────────────────────────── */

/** Open a movie/show/channel detail page from anywhere (music or video side). */
export function vdlOpenDetail(kind: string, id: number | string, source?: string): void {
  window.SoulSyncVideo?.openDetail?.({ kind, id, source });
}

/** The Import page is an admin tool. same gate the old page used. */
export function useVdlCanImport(): boolean {
  const profile = useProfile();
  return !profile || profile.isAdmin || profile.profileId === 1;
}

function vdlGotoVideoImport(): void {
  if (document.body.getAttribute('data-side') !== 'video') window._switchAppSide?.('video');
  document.dispatchEvent(new CustomEvent('soulsync:video-navigate', { detail: 'video-import' }));
}

function clampPct(v: number | null | undefined): number {
  return Math.max(0, Math.min(100, Number(v) || 0));
}

export interface VdlCardActions {
  onCancel: (id: number) => void;
  onRetry: (id: number) => void;
  onBlock: (d: VdlDownload) => void;
  onRemove: (id: number) => void;
  onToggle: (id: number) => void;
}

/** where "open" goes for this row: the title page, the channel, or youtube. */
function openTarget(d: VdlDownload): { label: string; run: () => void } | null {
  if (!d.media_id) return null;
  const ctx = vdlParseCtx(d);
  if (vdlRowType(d) === 'youtube') {
    // a channel is the show on the youtube side; its page is the show page
    if (ctx.channel_id) {
      return {
        label: 'Open show',
        run: () => vdlOpenDetail('channel', ctx.channel_id as string, 'youtube'),
      };
    }
    return {
      label: 'Open on YouTube',
      run: () =>
        window.open(
          `https://www.youtube.com/watch?v=${encodeURIComponent(String(d.media_id))}`,
          '_blank',
          'noopener',
        ),
    };
  }
  const movie = d.kind === 'movie';
  return {
    label: movie ? 'Open movie' : 'Open show',
    run: () =>
      vdlOpenDetail(
        movie ? 'movie' : 'show',
        parseInt(String(d.media_id), 10) || String(d.media_id),
        d.media_source || 'library',
      ),
  };
}

/* ── the row ───────────────────────────────────────────────────────────── */

/** The row's second line, picked by what the download is doing right now. */
function rowSub(
  d: VdlDownload,
  importElapsedMs: number,
  inGroup: boolean,
): { text: string; tone?: 'bad' | 'live' } {
  const ctx = vdlParseCtx(d);
  const status = d.status;
  if (vdlIsFail(status)) return { text: d.error || vdlStatusOf(status).label, tone: 'bad' };
  if (status === 'searching') return { text: 'Trying another release', tone: 'live' };
  if (status === 'queued') return { text: 'Waiting for a free slot', tone: 'live' };
  if (status === 'importing') {
    const iprog = clampPct(d.import_progress);
    let text =
      d.import_phase === 'merging'
        ? 'Merging video and audio'
        : d.import_phase === 'moving' && iprog > 0
          ? `Moving into your library · ${Math.round(iprog)}%`
          : 'Moving into your library';
    if (importElapsedMs > 2000) text += ` · ${vdlFmtElapsed(importElapsedMs)}`;
    return { text, tone: 'live' };
  }
  if (status === 'downloading' && clampPct(d.progress) === 0 && !d.speed_bps) {
    // nothing has moved yet: "0 MB of 0.9 GB" read as stalled, not starting
    return { text: ['Connecting', d.username, d.source].filter(Boolean).join(' · '), tone: 'live' };
  }
  if (vdlIsActive(status)) {
    const pct = clampPct(d.progress);
    const got = d.size_bytes
      ? `${vdlFmtSize((d.size_bytes * pct) / 100)} of ${vdlFmtSize(d.size_bytes)}`
      : '';
    return { text: [d.username, d.source, got].filter(Boolean).join(' · '), tone: 'live' };
  }
  const channel = vdlRowType(d) === 'youtube' ? ctx.channel || ctx.channel_title : '';
  if (status === 'completed') {
    return {
      text:
        [channel, inGroup ? '' : 'In your library'].filter(Boolean).join(' · ') ||
        'In your library',
    };
  }
  return { text: channel || d.release_title || '' };
}

function VdlCard({
  d,
  open,
  meta,
  ensureMeta,
  canImport,
  actions,
  inGroup,
}: {
  d: VdlDownload;
  open: boolean;
  meta: VdlMeta | VdlYtMeta | null | undefined;
  ensureMeta: (d: VdlDownload) => void;
  canImport: boolean;
  actions: VdlCardActions;
  /** an episode inside a season group: numbered, no show name repeated. */
  inGroup?: boolean;
}) {
  const info = vdlStatusOf(d.status);
  const cls = info.cls;
  const active = vdlIsActive(d.status);
  const failed = vdlIsFail(d.status);
  const importing = d.status === 'importing';
  const pct = clampPct(d.progress);

  /* when this card entered the import phase, for the elapsed timer */
  const importStartRef = useRef(0);
  useEffect(() => {
    importStartRef.current = importing ? importStartRef.current || Date.now() : 0;
  }, [importing]);
  const importElapsed = importStartRef.current ? Date.now() - importStartRef.current : 0;

  useEffect(() => {
    if (open) ensureMeta(d);
  }, [open, d, ensureMeta]);

  const ctx = vdlParseCtx(d);
  const vt = vdlRowType(d);
  const target = openTarget(d);
  const canBlock = vdlCanBlock(d);
  const needsImport = d.status === 'import_failed' && canImport;
  const downloading = d.status === 'downloading';
  const sub = rowSub(d, importElapsed, Boolean(inGroup));
  const tag = formatTag(d.quality_label);
  const when = d.status === 'completed' ? clockTime(parseStamp(d.completed_at)) : '';

  const title = inGroup && ctx.episode != null ? `Episode ${ctx.episode}` : vdlCardTitle(d);

  const menu = [
    ...(target ? [{ key: 'open', label: target.label, onSelect: target.run }] : []),
    ...(needsImport
      ? [{ key: 'import', label: 'Manual import', onSelect: vdlGotoVideoImport }]
      : []),
    ...(d.dest_path
      ? [
          {
            key: 'copy',
            label: 'Copy path',
            onSelect: () => copyText(d.dest_path as string, 'Path copied'),
          },
        ]
      : []),
    ...(vdlIsRemovable(d.status)
      ? [{ key: 'remove', label: 'Remove from list', onSelect: () => actions.onRemove(d.id) }]
      : []),
    ...(canBlock
      ? [
          {
            key: 'block',
            label: d.status === 'failed' ? 'Block release and retry' : 'Block release',
            danger: true,
            onSelect: () => actions.onBlock(d),
          },
        ]
      : []),
  ];

  const onRowClick = (e: React.MouseEvent) => {
    const t = e.target as HTMLElement;
    // clicks on controls or inside the open sheet don't toggle
    if (t.closest('[data-vdl-drawer]') || t.closest('button, a')) return;
    actions.onToggle(d.id);
  };

  return (
    <>
      <div
        className={`dlc-row dlc-row-${cls}${downloading ? ' dlc-row-live' : ''}${inGroup ? ' dlc-row-compact' : ''}${open ? ' dlc-row-open' : ''}`}
        data-dl-id={d.id}
        data-vtype={vt}
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onClick={onRowClick}
        onKeyDown={(e) => {
          if (e.target !== e.currentTarget) return;
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            actions.onToggle(d.id);
          }
        }}
      >
        <DlLead
          art={inGroup ? null : d.poster_url}
          poster={vt !== 'youtube'}
          number={inGroup ? (ctx.episode ?? '·') : null}
          progress={downloading ? pct : null}
          busy={d.status === 'queued' || d.status === 'searching' || importing}
          failed={failed}
          fallback={vdlKindIcon(d.kind)}
        />
        <div className="dlc-main">
          <div className="dlc-title">
            {title}
            {d.status === 'completed' && d.upgrade_watch ? (
              <span
                className="dlc-tag dlc-tag-watch"
                title="Below your quality cutoff, still watching for a better copy"
              >
                upgrade watch
              </span>
            ) : null}
          </div>
          {sub.text ? (
            <div className={`dlc-sub${sub.tone ? ` dlc-sub-${sub.tone}` : ''}`}>{sub.text}</div>
          ) : null}
        </div>
        <div className="dlc-meta">
          {tag ? (
            <span className={`dlc-tag${isHighQuality(tag) ? ' dlc-tag-hq' : ''}`}>{tag}</span>
          ) : null}
          {downloading ? (
            <>
              {/* says DOWNLOADING in words: a lone "0%" didn't read as anything */}
              <span className="dlc-live-pill">
                <span className="dlc-live-dot" aria-hidden="true" />
                {pct === 0 && !d.speed_bps ? 'Starting' : `Downloading · ${Math.round(pct)}%`}
              </span>
              {d.speed_bps ? <span className="dlc-speed">{vdlFmtSpeed(d.speed_bps)}</span> : null}
              {vdlFmtEta(d.eta_seconds) ? <span>{vdlFmtEta(d.eta_seconds)}</span> : null}
            </>
          ) : d.status === 'completed' ? (
            when ? (
              <span className="dlc-when">{when}</span>
            ) : null
          ) : failed && d.error ? null : (
            <span className={`dlc-state dlc-state-${active ? 'active' : cls}`}>
              {info.label + (Number(d.attempts) > 1 ? ` · ${d.attempts}x` : '')}
            </span>
          )}
        </div>
        <div className="dlc-acts">
          {active ? (
            <button
              type="button"
              className="dlc-icon-btn dlc-hover dlc-cancel"
              title="Cancel"
              aria-label="Cancel download"
              onClick={(e) => {
                e.stopPropagation();
                actions.onCancel(d.id);
              }}
            >
              {DlIcon.x}
            </button>
          ) : null}
          {needsImport ? (
            <DlButton
              primary
              icon={DlIcon.import}
              title="File it by hand on the Import page"
              onClick={vdlGotoVideoImport}
            >
              Import
            </DlButton>
          ) : failed ? (
            <DlButton
              primary
              icon={DlIcon.retry}
              title="Retry"
              onClick={() => actions.onRetry(d.id)}
            >
              Retry
            </DlButton>
          ) : null}
          <DlMore items={menu} />
        </div>
        {downloading && pct > 0 ? (
          <div className="dlc-rowbar" aria-hidden="true">
            <i style={{ width: `${pct}%` }} />
          </div>
        ) : downloading ? (
          // started, no bytes yet: the sweep says it's alive
          <div className="dlc-rowbar dlc-rowbar-indet" aria-hidden="true">
            <i />
          </div>
        ) : d.status === 'queued' || d.status === 'searching' || importing ? (
          // no number to show yet: a sweep says it's alive. moving with a
          // real percent gets a real bar
          d.import_phase === 'moving' && clampPct(d.import_progress) > 0 ? (
            <div className="dlc-rowbar" aria-hidden="true">
              <i style={{ width: `${clampPct(d.import_progress)}%` }} />
            </div>
          ) : (
            <div className="dlc-rowbar dlc-rowbar-indet" aria-hidden="true">
              <i />
            </div>
          )
        ) : null}
      </div>
      {open ? (
        <div data-vdl-drawer>
          <VdlSheet d={d} meta={meta} canImport={canImport} actions={actions} />
        </div>
      ) : null}
    </>
  );
}

/* ── the sheet ─────────────────────────────────────────────────────────── */

function CastRow({ meta }: { meta: VdlMeta }) {
  const cast = (meta.cast || []).slice(0, 8);
  if (!cast.length) return null;
  return (
    <div className="dlc-cast">
      {cast.map((c, i) => (
        <div className="dlc-cast-one" key={`${c.name}-${i}`}>
          {c.photo ? (
            <span className="dlc-cast-pic" style={{ backgroundImage: `url('${c.photo}')` }} />
          ) : (
            <span className="dlc-cast-pic dlc-cast-none">{(c.name || '?').charAt(0)}</span>
          )}
          <span className="dlc-cast-text">
            <span className="dlc-cast-name">{c.name}</span>
            {c.character ? <span className="dlc-cast-role">{c.character}</span> : null}
          </span>
        </div>
      ))}
    </div>
  );
}

/** the art + story band at the top of the sheet: who, what, which episode, who's in it. */
function VdlHero({ d, meta }: { d: VdlDownload; meta: VdlMeta | VdlYtMeta | null | undefined }) {
  const isYt = vdlRowType(d) === 'youtube';
  const ctx = vdlParseCtx(d);
  const loading = meta === null || meta === undefined;
  const m = (meta || {}) as VdlMeta;
  const ym = (meta || {}) as VdlYtMeta;

  if (isYt) {
    const thumb = ym.thumbnail_url || d.poster_url;
    const bits = [
      ctx.channel || ctx.channel_title,
      ym.duration,
      ym.view_count ? `${vdlFmtViews(ym.view_count)} views` : '',
      ctx.published_at ? String(ctx.published_at).slice(0, 10) : '',
    ].filter(Boolean);
    return (
      <div className="dlc-hero">
        {thumb ? (
          <div className="dlc-hero-wash" style={{ backgroundImage: `url('${thumb}')` }} />
        ) : null}
        {thumb ? (
          <div className="dlc-still" style={{ backgroundImage: `url('${thumb}')` }} />
        ) : null}
        <div className="dlc-hero-body">
          <div className="dlc-hero-title">{d.title || ym.title || 'Video'}</div>
          {bits.length ? <div className="dlc-hero-line">{bits.join(' · ')}</div> : null}
          {ctx.description ? <p className="dlc-hero-text">{ctx.description}</p> : null}
        </div>
      </div>
    );
  }

  const bits = [
    m.year || d.year,
    m.rating ? `★ ${Math.round(Number(m.rating) * 10) / 10}` : '',
    vdlFmtRuntime(m.runtime_minutes),
    m.network || m.studio,
    m.status,
  ].filter(Boolean);
  const ep = m.episode;
  const genres = (m.genres || []).slice(0, 4).join(' · ');
  const provs = m.providers || [];
  const still = ep?.still_url || (ep ? null : m.backdrop_url);

  return (
    <div className="dlc-hero">
      {m.backdrop_url ? (
        <div className="dlc-hero-wash" style={{ backgroundImage: `url('${m.backdrop_url}')` }} />
      ) : null}
      {still ? (
        <div className="dlc-still" style={{ backgroundImage: `url('${still}')` }} />
      ) : d.poster_url ? (
        <div
          className="dlc-still dlc-still-poster"
          style={{ backgroundImage: `url('${d.poster_url}')` }}
        />
      ) : null}
      <div className="dlc-hero-body">
        {m.logo ? (
          <img className="dlc-hero-logo" src={m.logo} alt={String(m.title || d.title || '')} />
        ) : (
          <div className="dlc-hero-title">{m.title || d.title || 'Download'}</div>
        )}
        {bits.length ? <div className="dlc-hero-line">{bits.join(' · ')}</div> : null}
        {ep ? (
          <div className="dlc-hero-ep">
            <span className="dlc-eyebrow dlc-eyebrow-accent">
              S{vdlPad2(ep.season)}E{vdlPad2(ep.episode)}
              {ep.air_date ? ` · ${ep.air_date}` : ''}
            </span>
            {ep.title ? <span className="dlc-hero-eptitle">{ep.title}</span> : null}
          </div>
        ) : m.tagline ? (
          <div className="dlc-hero-tagline">{m.tagline}</div>
        ) : null}
        {ep?.overview ? (
          <p className="dlc-hero-text">{ep.overview}</p>
        ) : loading ? (
          <p className="dlc-hero-text dlc-muted">Loading…</p>
        ) : m.overview ? (
          <p className="dlc-hero-text">{m.overview}</p>
        ) : null}
        {genres || m.trailer_url || provs.length ? (
          <div className="dlc-hero-row">
            {genres ? <span className="dlc-hero-genres">{genres}</span> : null}
            {m.trailer_url ? (
              <a
                className="dlc-btn dlc-trailer"
                href={m.trailer_url}
                target="_blank"
                rel="noopener"
                onClick={(e) => e.stopPropagation()}
              >
                {DlIcon.play}
                Trailer
              </a>
            ) : null}
            {provs.length ? (
              <span className="dlc-provs">
                <span className="dlc-provs-t">Watch on</span>
                {provs.map((p, i) =>
                  p.logo ? (
                    <img
                      key={i}
                      className="dlc-prov"
                      src={p.logo}
                      alt={p.name || ''}
                      title={p.name || ''}
                    />
                  ) : (
                    <span key={i} className="dlc-prov dlc-prov-txt">
                      {p.name || ''}
                    </span>
                  ),
                )}
              </span>
            ) : null}
          </div>
        ) : null}
        <CastRow meta={m} />
      </div>
    </div>
  );
}

function VdlSheet({
  d,
  meta,
  canImport,
  actions,
}: {
  d: VdlDownload;
  meta: VdlMeta | VdlYtMeta | null | undefined;
  canImport: boolean;
  actions: VdlCardActions;
}) {
  const isYt = vdlRowType(d) === 'youtube';
  const ctx = vdlParseCtx(d);
  const m = (meta || {}) as VdlMeta;
  const active = vdlIsActive(d.status);
  const failed = vdlIsFail(d.status);
  const target = openTarget(d);
  const peer = ctx.peer;
  const backups = vdlBackups(d);
  const availability = peer
    ? [
        peer.slots != null ? (peer.slots > 0 ? 'free slot' : 'no free slot') : '',
        peer.queue != null ? `queue ${peer.queue}` : '',
        vdlFmtSpeed(peer.speed),
      ]
        .filter(Boolean)
        .join(' · ')
    : '';

  const facts: DlFact[] = isYt
    ? [
        ['Channel', ctx.channel || ctx.channel_title || ''],
        ['Quality', d.quality_label || ''],
        ['Size', d.size_bytes ? vdlFmtSize(d.size_bytes) : ''],
        ['Path', d.dest_path || '', { mono: true, copy: d.dest_path || undefined }],
      ]
    : [
        ['Release', d.release_title || '', { mono: true }],
        ['Source', [d.username, d.source].filter(Boolean).join(' · ')],
        ['Format', [d.resolution, d.codec].filter(Boolean).join(' · ')],
        ['Target', d.quality_label || ''],
        ['Size', d.size_bytes ? vdlFmtSize(d.size_bytes) : ''],
        ['Peer', availability],
        ['Backups', backups ? `${backups} release${backups === 1 ? '' : 's'} to fall back on` : ''],
        ['Attempts', Number(d.attempts) > 1 ? `${d.attempts}` : ''],
        [vdlRowType(d) === 'movie' ? 'Director' : 'Creator', m.director || ''],
        ['Path', d.dest_path || '', { mono: true, copy: d.dest_path || undefined }],
      ];

  const needsImport = d.status === 'import_failed' && canImport;
  const canBlock = vdlCanBlock(d);

  return (
    <DlSheet
      hero={<VdlHero d={d} meta={meta} />}
      stepsTitle={failed ? 'What happened' : d.status === 'completed' ? 'Journey' : 'Progress'}
      steps={videoJourney(d, Date.now())}
      factsTitle={isYt ? 'Video' : 'Release'}
      facts={facts}
      actions={
        <>
          {needsImport ? (
            <DlButton primary icon={DlIcon.import} onClick={vdlGotoVideoImport}>
              Manual import
            </DlButton>
          ) : null}
          {failed ? (
            <DlButton
              primary={!needsImport}
              icon={DlIcon.retry}
              onClick={() => actions.onRetry(d.id)}
            >
              Retry
            </DlButton>
          ) : null}
          {target ? (
            <DlButton primary={d.status === 'completed'} onClick={target.run}>
              {target.label}
            </DlButton>
          ) : null}
          {d.dest_path ? (
            <DlButton
              icon={DlIcon.copy}
              onClick={() => copyText(d.dest_path as string, 'Path copied')}
            >
              Copy path
            </DlButton>
          ) : null}
          <span className="dlc-spacer" />
          {canBlock ? (
            <DlButton icon={DlIcon.block} onClick={() => actions.onBlock(d)}>
              {d.status === 'failed' ? 'Block and retry' : 'Block release'}
            </DlButton>
          ) : null}
          {active ? (
            <DlButton onClick={() => actions.onCancel(d.id)}>Cancel download</DlButton>
          ) : null}
        </>
      }
    />
  );
}

/* ── season group ──────────────────────────────────────────────────────── */

function VdlGroup({
  groupKey,
  members,
  filter,
  collapsed,
  onToggle,
  onCancel,
  renderCard,
}: {
  groupKey: string;
  members: VdlDownload[];
  filter: VdlFilter;
  collapsed: boolean;
  onToggle: () => void;
  onCancel: (ids: number[]) => void;
  renderCard: (d: VdlDownload, inGroup?: boolean) => React.ReactNode;
}) {
  const { pack, episodes } = vdlSplitGroup(members);
  const first = pack || members[0];
  const c0 = vdlParseCtx(first);
  const total = episodes.length;
  let done = 0;
  let act = 0;
  let fail = 0;
  let pctSum = 0;
  let speed = 0;
  let eta = 0;
  for (const d of episodes) {
    if (d.status === 'completed') {
      done++;
      pctSum += 100;
    } else if (vdlIsActive(d.status)) {
      act++;
      pctSum += clampPct(d.progress);
      speed += Number(d.speed_bps) || 0;
      eta = Math.max(eta, Number(d.eta_seconds) || 0);
    } else {
      fail++;
    }
  }
  const packActive = Boolean(pack && vdlIsActive(pack.status));
  if (packActive && pack) {
    speed = Number(pack.speed_bps) || speed;
    eta = Number(pack.eta_seconds) || eta;
  }
  const pct = packActive ? clampPct(pack?.progress) : total ? Math.round(pctSum / total) : 0;
  const doneW = total ? (done / total) * 100 : 0;
  const failW = total ? (fail / total) * 100 : 0;

  const tally = total
    ? `${done} of ${total} imported`
    : packActive
      ? pack?.status === 'importing'
        ? 'Season pack importing'
        : 'Season pack downloading'
      : 'Season pack';

  const visibleEps = episodes.filter((d) => vdlMatchesFilter(d.status, filter));
  const headVisible = pack ? vdlMatchesFilter(pack.status, filter) : false;

  const onGroupCancel = () => {
    const ids = episodes.filter((d) => vdlIsActive(d.status)).map((d) => d.id);
    // the pack is the head, not a card in the body. without this, cancelling
    // a batch whose episodes don't exist yet would cancel nothing at all.
    if (pack && packActive && !ids.includes(pack.id)) ids.push(pack.id);
    onCancel(ids);
  };
  const target = openTarget(first);
  const menu = [
    ...(target ? [{ key: 'open', label: target.label, onSelect: target.run }] : []),
    ...(act || packActive
      ? [
          {
            key: 'cancel',
            label: 'Cancel all in this season',
            danger: true,
            onSelect: onGroupCancel,
          },
        ]
      : []),
  ];
  const open = !collapsed;

  return (
    <div
      className={`dlc-list dlc-group${open ? ' dlc-group-open' : ''}${act || packActive ? ' dlc-group-live' : ''}`}
      data-vdpg-group={groupKey}
      style={{ display: visibleEps.length || headVisible ? '' : 'none' }}
    >
      <div
        className="dlc-ghead"
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onClick={onToggle}
        onKeyDown={(e) => {
          if (e.target !== e.currentTarget) return;
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            onToggle();
          }
        }}
      >
        {first.poster_url ? (
          <div className="dlc-wash" style={{ backgroundImage: `url('${first.poster_url}')` }} />
        ) : null}
        {first.poster_url ? (
          <img className="dlc-gart dlc-gart-poster" src={first.poster_url} alt="" />
        ) : (
          <div className="dlc-gart dlc-gart-poster dlc-gart-empty">{vdlKindIcon('show')}</div>
        )}
        <div className="dlc-main">
          <div className="dlc-eyebrow">
            TV · {c0.season != null ? `Season ${c0.season}` : 'Episodes'}
          </div>
          <div className="dlc-gtitle">
            {first.title || 'Show'}
            {first.year ? <span className="dlc-gyear"> ({first.year})</span> : null}
          </div>
          <div className="dlc-gsub">
            <span>{tally}</span>
            {act ? <span>{act} downloading</span> : null}
            {fail ? <span className="dlc-gbad">{fail} failed</span> : null}
            {speed ? <span>{vdlFmtSpeed(speed)}</span> : null}
            {(act || packActive) && vdlFmtEta(eta) ? <span>{vdlFmtEta(eta)}</span> : null}
          </div>
        </div>
        <div className="dlc-gside">
          {target ? <DlButton onClick={target.run}>{target.label}</DlButton> : null}
          <DlMore items={menu} label="Season actions" />
          <span
            className={`dlc-icon-btn dlc-chev${open ? ' dlc-chev-open' : ''}`}
            aria-hidden="true"
          >
            {DlIcon.chevron}
          </span>
        </div>
      </div>
      <div className="dlc-gbar" aria-hidden="true">
        {packActive ? (
          <i className="dlc-gbar-active dlc-shimmer" style={{ width: `${pct}%` }} />
        ) : (
          <>
            <i className="dlc-gbar-done" style={{ width: `${doneW}%` }} />
            <i className="dlc-gbar-fail" style={{ width: `${failW}%` }} />
            <i
              className={`dlc-gbar-active${act ? ' dlc-shimmer' : ''}`}
              style={{ width: `${Math.max(0, Math.min(100 - doneW - failW, pct - doneW))}%` }}
            />
          </>
        )}
      </div>
      {open ? (
        <div className="dlc-group-rows">
          {episodes.map((d) => (
            <div key={d.id} style={{ display: vdlMatchesFilter(d.status, filter) ? '' : 'none' }}>
              {renderCard(d, true)}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/* ── the list ───────────────────────────────────────────────────────────── */

export interface VdlListProps {
  downloads: VdlDownload[];
  filter: VdlFilter;
  loaded: boolean;
  expanded: ReadonlySet<number>;
  collapsedGroups: ReadonlySet<string>;
  metaFor: (d: VdlDownload) => VdlMeta | VdlYtMeta | null | undefined;
  ensureMeta: (d: VdlDownload) => void;
  onToggleGroup: (key: string) => void;
  cardActions: VdlCardActions;
  onCancelGroup: (ids: number[]) => void;
}

/** Skeleton shimmer rows for the first paint — no flash of "no downloads". */
function VdlSkeleton() {
  return (
    <div data-vdl-skel>
      {[0, 1, 2].map((i) => (
        <div className="vdpg-skel-row" key={i}>
          <div className="vdpg-skel-art" />
          <div className="vdpg-skel-lines">
            <div className="vdpg-skel-line" />
            <div className="vdpg-skel-line vdpg-skel-line--short" />
          </div>
        </div>
      ))}
    </div>
  );
}

export function VdlList({
  downloads,
  filter,
  loaded,
  expanded,
  collapsedGroups,
  metaFor,
  ensureMeta,
  onToggleGroup,
  cardActions,
  onCancelGroup,
}: VdlListProps) {
  const canImport = useVdlCanImport();

  if (!loaded) {
    return (
      <div className="adl-list dlc-stack" data-vdl-list>
        <VdlSkeleton />
      </div>
    );
  }

  const renderCard = (d: VdlDownload, inGroup?: boolean) => (
    <VdlCard
      d={d}
      open={expanded.has(d.id)}
      meta={metaFor(d)}
      ensureMeta={ensureMeta}
      canImport={canImport}
      actions={cardActions}
      inGroup={inGroup}
    />
  );

  // Group same-show+season episode batches (≥2) into one parent card; keep
  // everything else standalone. Server order preserved — a group sits where
  // its first member appears.
  const counts: Record<string, number> = {};
  const packKeys: Record<string, boolean> = {};
  for (const d of downloads) {
    const k = vdlGroupKey(d);
    if (!k) continue;
    counts[k] = (counts[k] || 0) + 1;
    if (vdlIsPackRow(d)) packKeys[k] = true;
  }
  const order: { group: string; members: VdlDownload[] }[] = [];
  const singles: VdlDownload[] = [];
  const seen = new Set<string>();
  for (const d of downloads) {
    const k = vdlGroupKey(d);
    if (k && (counts[k] >= 2 || packKeys[k])) {
      if (!seen.has(k)) {
        seen.add(k);
        const members = downloads.filter(
          (m) => vdlGroupKey(m) === k && (counts[k] >= 2 || packKeys[k]),
        );
        order.push({ group: k, members });
      }
    } else {
      singles.push(d);
    }
  }
  // interleave: a group sits where its first member appears (server order)
  const units: ({ group: string; members: VdlDownload[] } | { single: VdlDownload })[] = [];
  const groupByFirst = new Map<number, { group: string; members: VdlDownload[] }>();
  for (const g of order) groupByFirst.set(g.members[0].id, g);
  const groupedIds = new Set(order.flatMap((g) => g.members.map((m) => m.id)));
  for (const d of downloads) {
    if (groupedIds.has(d.id)) {
      const g = groupByFirst.get(d.id);
      if (g) units.push(g);
    } else {
      units.push({ single: d });
    }
  }

  // consecutive standalone rows share one surface, the way a group's rows do
  let shown = 0;
  const body: React.ReactNode[] = [];
  let run: VdlDownload[] = [];
  const flush = () => {
    if (!run.length) return;
    const visible = run.filter((d) => vdlMatchesFilter(d.status, filter)).length;
    shown += visible;
    body.push(
      <div key={`run-${run[0].id}`} className="dlc-list" style={{ display: visible ? '' : 'none' }}>
        {run.map((d) => (
          <div key={d.id} style={{ display: vdlMatchesFilter(d.status, filter) ? '' : 'none' }}>
            {renderCard(d)}
          </div>
        ))}
      </div>,
    );
    run = [];
  };
  for (const u of units) {
    if ('single' in u) {
      run.push(u.single);
      continue;
    }
    flush();
    const { pack, episodes } = vdlSplitGroup(u.members);
    const visN = episodes.filter((d) => vdlMatchesFilter(d.status, filter)).length;
    const headVis = pack ? vdlMatchesFilter(pack.status, filter) : false;
    if (visN || headVis) shown++;
    body.push(
      <VdlGroup
        key={u.group}
        groupKey={u.group}
        members={u.members}
        filter={filter}
        collapsed={collapsedGroups.has(u.group)}
        onToggle={() => onToggleGroup(u.group)}
        onCancel={onCancelGroup}
        renderCard={renderCard}
      />,
    );
  }
  flush();

  return (
    <div className="adl-list dlc-stack" data-vdl-list>
      {body}
      {shown === 0 ? (
        <div className="adl-empty vdpg-empty">
          {downloads.length === 0 ? (
            <>
              <div className="vdpg-empty-ic">📥</div>
              <div className="vdpg-empty-t">No downloads yet</div>
              <div className="vdpg-empty-s">
                Hit <strong>Grab</strong> on a search result — or add something to the wishlist and
                let the automations fetch it — and it&apos;ll show up here.
              </div>
            </>
          ) : (
            <>
              <div className="vdpg-empty-ic">{filter === 'failed' ? '🎉' : '📂'}</div>
              <div className="vdpg-empty-t">
                Nothing {filter === 'all' ? 'here' : filter} right now
              </div>
              <div className="vdpg-empty-s">
                {filter === 'failed'
                  ? 'No failures — everything went through clean.'
                  : 'Switch filters to see the rest of the queue.'}
              </div>
            </>
          )}
        </div>
      ) : null}
    </div>
  );
}

/** Re-exported for the review pane's age display. */
export { vdlFmtAge };
