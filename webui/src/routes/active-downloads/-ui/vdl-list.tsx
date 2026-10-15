/**
 * The video downloads list: season-pack grouping, cards, and the expanded
 * detail drawer (cast info + images — Broque's hard requirement).
 *
 * A React port of webui/static/video/video-downloads-page.js's render path,
 * reusing the same adl-/vdpg- markup vocabulary so the existing CSS
 * applies unchanged.
 */

import { useEffect, useRef } from 'react';

import { useProfile } from '@/platform/shell/route-controllers';

import type { VdlDownload, VdlFilter, VdlMeta, VdlYtMeta } from '../-vdl.types';

import {
  vdlBlockSxe,
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
  vdlGroupWorthy,
  vdlIsActive,
  vdlIsFail,
  vdlIsPackRow,
  vdlIsRetryable,
  vdlKindIcon,
  vdlMatchesFilter,
  vdlPad2,
  vdlParseCtx,
  vdlSplitGroup,
  vdlStatusOf,
  vdlType,
} from '../-vdl.helpers';

const toast = (message: string, type: string) => window.showToast?.(message, type);

/* ── icons (the old page's inline SVGs) ────────────────────────────────── */

function XIcon() {
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.4"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <line x1="18" y1="6" x2="6" y2="18" />
      <line x1="6" y1="6" x2="18" y2="18" />
    </svg>
  );
}
function RetryIcon() {
  return (
    <svg
      width="13"
      height="13"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <polyline points="23 4 23 10 17 10" />
      <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
    </svg>
  );
}
function ImportIcon() {
  return (
    <svg
      width="13"
      height="13"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.1"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <polyline points="7 10 12 15 17 10" />
      <line x1="12" y1="15" x2="12" y2="3" />
    </svg>
  );
}
function BlockIcon() {
  return (
    <svg
      width="13"
      height="13"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.1"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="9" />
      <line x1="5.6" y1="5.6" x2="18.4" y2="18.4" />
    </svg>
  );
}
function OpenIcon() {
  return (
    <svg
      width="13"
      height="13"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.1"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
      <polyline points="15 3 21 3 21 9" />
      <line x1="10" y1="14" x2="21" y2="3" />
    </svg>
  );
}

/* ── shared actions ────────────────────────────────────────────────────── */

/** Open a movie/show/channel detail page from anywhere (music or video side). */
export function vdlOpenDetail(kind: string, id: number | string, source?: string): void {
  window.SoulSyncVideo?.openDetail?.({ kind, id, source });
}

/** The Import page is an admin tool — same gate the old page used. */
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

/* ── collapsed card ────────────────────────────────────────────────────── */

export interface VdlCardActions {
  onCancel: (id: number) => void;
  onRetry: (id: number) => void;
  onBlock: (d: VdlDownload) => void;
  onToggle: (id: number) => void;
}

/** The collapsed row's meta context line (release / size·speed·eta·user·pct / import phases). */
function cardContext(d: VdlDownload, importElapsedMs: number): string {
  const status = d.status;
  const pct = clampPct(d.progress);
  const importing = status === 'importing';
  const moving = importing && d.import_phase === 'moving';
  const iprog = clampPct(d.import_progress);
  if (status === 'completed' && d.dest_path) return `→ ${d.dest_path}`;
  if (status === 'searching') return 'Trying another release…';
  if (importing) {
    let ctx: string;
    if (d.import_phase === 'merging') ctx = 'Merging video + audio…';
    else if (moving && iprog > 0) ctx = `Moving into your library…  ${Math.round(iprog)}%`;
    else ctx = 'Moving into your library…';
    if (importElapsedMs > 2000) ctx += `  ·  ${vdlFmtElapsed(importElapsedMs)}`;
    return ctx;
  }
  if (status === 'queued') return 'Waiting for a free slot…';
  if (vdlIsActive(status)) {
    return [
      vdlFmtSize(d.size_bytes),
      d.speed_bps ? `↓ ${vdlFmtSpeed(d.speed_bps)}` : '',
      vdlFmtEta(d.eta_seconds),
      d.username ? `👤 ${d.username}` : '',
      `${Math.round(pct)}%`,
    ]
      .filter(Boolean)
      .join('  ·  ');
  }
  return d.release_title && d.release_title !== (d.title || '')
    ? d.release_title
    : vdlFmtSize(d.size_bytes);
}

function VdlCard({
  d,
  open,
  meta,
  ensureMeta,
  canImport,
  actions,
}: {
  d: VdlDownload;
  open: boolean;
  meta: VdlMeta | VdlYtMeta | null | undefined;
  ensureMeta: (d: VdlDownload) => void;
  canImport: boolean;
  actions: VdlCardActions;
}) {
  const info = vdlStatusOf(d.status);
  const cls = info.cls;
  const active = vdlIsActive(d.status);
  const showBar = active;
  const importing = d.status === 'importing';
  const moving = importing && d.import_phase === 'moving';
  const iprog = clampPct(d.import_progress);
  const indet =
    d.status === 'queued' || d.status === 'searching' || (importing && !(moving && iprog > 0));
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

  const vt = vdlType(d.kind);
  const ctx = vdlParseCtx(d);
  const ytChannel = vt === 'youtube' ? ctx.channel || ctx.channel_title : null;

  const onRowClick = (e: React.MouseEvent) => {
    const target = e.target as HTMLElement;
    // clicks on controls/links, or inside the open drawer, don't toggle
    if (target.closest('[data-vdl-drawer]') || target.closest('button, a')) return;
    actions.onToggle(d.id);
  };

  const openBtn = !d.media_id ? null : vt === 'youtube' ? (
    ctx.channel_id ? (
      <button
        key="open"
        type="button"
        className="vdpg-open"
        title="Open channel"
        onClick={() => vdlOpenDetail('channel', ctx.channel_id as string, 'youtube')}
      >
        <OpenIcon />
      </button>
    ) : (
      <a
        key="open"
        className="vdpg-open"
        href={`https://www.youtube.com/watch?v=${encodeURIComponent(String(d.media_id))}`}
        target="_blank"
        rel="noopener"
        title="Open on YouTube"
      >
        <OpenIcon />
      </a>
    )
  ) : (
    <button
      key="open"
      type="button"
      className="vdpg-open"
      title={`Open ${d.kind === 'movie' ? 'movie' : 'show'} page`}
      onClick={() =>
        vdlOpenDetail(
          d.kind === 'movie' ? 'movie' : 'show',
          parseInt(String(d.media_id), 10) || String(d.media_id),
          d.media_source || 'library',
        )
      }
    >
      <OpenIcon />
    </button>
  );

  const stateBtn = active ? (
    <button
      key="cancel"
      type="button"
      className="adl-row-cancel"
      title="Cancel"
      onClick={() => actions.onCancel(d.id)}
    >
      <XIcon />
    </button>
  ) : vdlIsFail(d.status) ? (
    <button
      key="retry"
      type="button"
      className="vdpg-row-retry"
      title="Retry"
      onClick={() => actions.onRetry(d.id)}
    >
      <RetryIcon />
    </button>
  ) : null;

  const importBtn =
    d.status === 'import_failed' && canImport ? (
      <button
        key="import"
        type="button"
        className="vdpg-row-retry vdpg-row-import"
        title="Manual Import — file it by hand on the Import page"
        onClick={vdlGotoVideoImport}
      >
        <ImportIcon />
      </button>
    ) : null;

  const blockBtn = vdlCanBlock(d) ? (
    <button
      key="block"
      type="button"
      className="vdpg-row-retry vdpg-row-block"
      title={
        d.status === 'failed' ? 'Block this release and retry with another' : 'Block this release'
      }
      onClick={() => actions.onBlock(d)}
    >
      <BlockIcon />
    </button>
  ) : null;

  const label = info.label + (Number(d.attempts) > 1 ? ` · ${d.attempts}x` : '');
  const errTxt = vdlIsFail(d.status) && d.error ? d.error : '';

  return (
    <div
      className={`vdpg-card adl-row adl-row-${cls}${open ? ' vdpg-card-open' : ''}`}
      data-dl-id={d.id}
      data-vtype={vt}
      onClick={onRowClick}
    >
      <div className="vdpg-artwrap">
        <div
          className={`adl-row-art vdpg-art${d.poster_url ? ' vdpg-has-poster' : ' adl-row-art-empty'}`}
          style={d.poster_url ? { backgroundImage: `url('${d.poster_url}')` } : undefined}
        >
          {d.poster_url ? null : vdlKindIcon(d.kind)}
        </div>
        <span className="vdpg-tbadge">{vdlKindIcon(d.kind)}</span>
      </div>
      <div className="adl-row-info">
        <div className="adl-row-title">{vdlCardTitle(d)}</div>
        <div className="adl-row-meta">
          {d.quality_label ? <span className="vdpg-qchip">{d.quality_label}</span> : null}
          {d.status === 'completed' && d.upgrade_watch ? (
            <span
              className="vdpg-upchip"
              title="Below your quality cutoff — still watching for a better copy"
            >
              ⇪ upgrade watch
            </span>
          ) : null}
          {ytChannel ? <span className="vdpg-mchan">{ytChannel}</span> : null}
          <span
            className={`vdpg-mctx${d.status === 'completed' && d.dest_path ? ' vdpg-dest' : ''}`}
          >
            {cardContext(d, importElapsed)}
          </span>
        </div>
        {errTxt ? <div className="adl-row-error">{errTxt}</div> : null}
        {showBar ? (
          <div className={`vdpg-prog${indet ? ' vdpg-prog-indet' : ''}`}>
            <div
              className="vdpg-prog-fill"
              style={{ width: `${indet ? 100 : moving ? iprog : pct}%` }}
            />
          </div>
        ) : null}
      </div>
      <div className={`adl-row-status ${cls}`}>
        <span className={`adl-status-dot ${cls}`} />
        <span>{label}</span>
      </div>
      <div className="vdpg-rowact">
        {openBtn}
        {importBtn}
        {blockBtn}
        {stateBtn}
      </div>
      {open ? (
        <div className="vdpg-drawer" data-vdl-drawer>
          <VdlDrawer d={d} meta={meta} canImport={canImport} actions={actions} />
        </div>
      ) : null}
    </div>
  );
}

/* ── expanded drawer ─────────────────────────────────────────────────────── */

function Fact({
  k,
  v,
  wide,
  err,
  mono,
}: {
  k: string;
  v?: string | null;
  wide?: boolean;
  err?: boolean;
  mono?: boolean;
}) {
  if (!v) return null;
  return (
    <div className={`vdpg-f${wide ? ' vdpg-f-wide' : ''}${err ? ' vdpg-f-err' : ''}`}>
      <span className="vdpg-fk">{k}</span>
      <span className={`vdpg-fv${mono ? ' vdpg-mono' : ''}`}>{v}</span>
    </div>
  );
}

function CastRow({ meta }: { meta: VdlMeta }) {
  const cast = (meta.cast || []).slice(0, 8);
  if (!cast.length) return null;
  return (
    <>
      <div className="vdpg-dr-st">Cast</div>
      <div className="vdpg-dr-cast">
        {cast.map((c, i) => (
          <div className="vdpg-cast" key={`${c.name}-${i}`}>
            {c.photo ? (
              <span className="vdpg-cast-pic" style={{ backgroundImage: `url('${c.photo}')` }} />
            ) : (
              <span className="vdpg-cast-pic vdpg-cast-none">{(c.name || '?').charAt(0)}</span>
            )}
            <span className="vdpg-cast-nm">{c.name}</span>
            {c.character ? <span className="vdpg-cast-ch">{c.character}</span> : null}
          </div>
        ))}
      </div>
    </>
  );
}

function VdlDrawer({
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
  const isYt = vdlType(d.kind) === 'youtube';
  const ctx = vdlParseCtx(d);
  const loading = meta === null || meta === undefined;
  const m = (meta || {}) as VdlMeta;
  const ym = (meta || {}) as VdlYtMeta;
  const active = vdlIsActive(d.status);

  /* navigator.clipboard is unavailable on plain-HTTP installs (self-hosted
   * over a LAN IP) — fall back to the legacy execCommand path before giving
   * up, so copy keeps working there instead of just toasting. */
  const legacyCopy = (text: string) => {
    try {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand('copy');
      document.body.removeChild(ta);
      toast(ok ? 'Path copied' : 'Copy not supported here', ok ? 'success' : 'info');
    } catch {
      toast('Copy not supported here', 'info');
    }
  };

  const copyPath = () => {
    const path = d.dest_path;
    if (!path) return;
    if (navigator.clipboard) {
      void navigator.clipboard.writeText(path).then(
        () => toast('Path copied', 'success'),
        () => legacyCopy(path),
      );
    } else {
      legacyCopy(path);
    }
  };

  let head: React.ReactNode;
  if (isYt) {
    const thumb = ym.thumbnail_url || d.poster_url;
    const bits = [
      ctx.channel || ctx.channel_title,
      ym.duration,
      ym.view_count ? `${vdlFmtViews(ym.view_count)} views` : '',
      ctx.published_at ? String(ctx.published_at).slice(0, 10) : '',
    ].filter(Boolean);
    head = (
      <div className="vdpg-dr-head">
        {thumb ? (
          <div className="vdpg-dr-ytthumb" style={{ backgroundImage: `url('${thumb}')` }} />
        ) : null}
        <div className="vdpg-dr-title">{d.title || ym.title || 'Video'}</div>
        {bits.length ? <div className="vdpg-dr-metaline">{bits.join('  ·  ')}</div> : null}
      </div>
    );
  } else {
    const bits = [
      m.year || d.year,
      m.rating ? `⭐ ${Math.round(Number(m.rating) * 10) / 10}` : '',
      vdlFmtRuntime(m.runtime_minutes),
      m.network || m.studio,
      m.status,
    ].filter(Boolean);
    head = (
      <div className="vdpg-dr-head">
        {m.logo ? (
          <img className="vdpg-dr-logo" src={m.logo} alt={String(m.title || d.title || '')} />
        ) : (
          <div className="vdpg-dr-title">{m.title || d.title || 'Download'}</div>
        )}
        {bits.length ? <div className="vdpg-dr-metaline">{bits.join('  ·  ')}</div> : null}
        {m.tagline ? <div className="vdpg-dr-tagline">{m.tagline}</div> : null}
      </div>
    );
  }

  const ep = !isYt ? m.episode : null;
  const lead = isYt ? (
    ctx.description ? (
      <p className="vdpg-dr-syn">{ctx.description}</p>
    ) : null
  ) : ep ? (
    <div className="vdpg-dr-ep">
      {ep.still_url ? (
        <div className="vdpg-dr-epstill" style={{ backgroundImage: `url('${ep.still_url}')` }} />
      ) : null}
      <div className="vdpg-dr-epbody">
        <div className="vdpg-dr-epnum">
          S{vdlPad2(ep.season)}E{vdlPad2(ep.episode)}
          {ep.air_date ? `   ·   ${ep.air_date}` : ''}
        </div>
        <div className="vdpg-dr-eptitle">{ep.title || ''}</div>
        {ep.overview ? <p className="vdpg-dr-epov">{ep.overview}</p> : null}
      </div>
    </div>
  ) : loading ? (
    <p className="vdpg-dr-syn vdpg-dr-muted">Loading…</p>
  ) : m.overview ? (
    <p className="vdpg-dr-syn">{m.overview}</p>
  ) : (
    <p className="vdpg-dr-syn vdpg-dr-muted">No synopsis available.</p>
  );

  const genres = !isYt ? (m.genres || []).slice(0, 4).join('  ·  ') : '';
  const provs = !isYt ? m.providers || [] : [];
  const watch =
    !isYt && (m.trailer_url || provs.length) ? (
      <div className="vdpg-dr-watch">
        {m.trailer_url ? (
          <a
            className="vdpg-dr-btn vdpg-dr-trailer"
            href={m.trailer_url}
            target="_blank"
            rel="noopener"
          >
            ▶ Trailer
          </a>
        ) : null}
        {provs.length ? (
          <span className="vdpg-dr-provs">
            <span className="vdpg-dr-provs-t">Watch on</span>
            {provs.map((p, i) =>
              p.logo ? (
                <img
                  key={i}
                  className="vdpg-prov"
                  src={p.logo}
                  alt={p.name || ''}
                  title={p.name || ''}
                />
              ) : (
                <span key={i} className="vdpg-prov vdpg-prov-txt">
                  {p.name || ''}
                </span>
              ),
            )}
          </span>
        ) : null}
      </div>
    ) : null;

  const peer = ctx.peer;
  const availability = peer
    ? [
        peer.slots != null ? (peer.slots > 0 ? '✓ free slot' : 'no free slot') : '',
        peer.queue != null ? `queue ${peer.queue}` : '',
        vdlFmtSpeed(peer.speed),
      ]
        .filter(Boolean)
        .join('   ·   ')
    : '';

  const ytChannelId = ym.channel_id || ctx.channel_id;
  const btns: React.ReactNode[] = [];
  if (d.media_id && !isYt) {
    btns.push(
      <button
        key="open"
        type="button"
        className="vdpg-dr-btn"
        onClick={() =>
          vdlOpenDetail(
            d.kind === 'movie' ? 'movie' : 'show',
            parseInt(String(d.media_id), 10) || String(d.media_id),
            d.media_source || 'library',
          )
        }
      >
        Open in library
      </button>,
    );
  }
  if (isYt && ytChannelId) {
    btns.push(
      <button
        key="chan"
        type="button"
        className="vdpg-dr-btn"
        onClick={() => vdlOpenDetail('channel', ytChannelId, 'youtube')}
      >
        Open channel
      </button>,
    );
  }
  if (isYt && d.media_id) {
    btns.push(
      <a
        key="yt"
        className="vdpg-dr-btn"
        href={`https://www.youtube.com/watch?v=${encodeURIComponent(String(d.media_id))}`}
        target="_blank"
        rel="noopener"
      >
        Open on YouTube
      </a>,
    );
  }
  if (active) {
    btns.push(
      <button
        key="cancel"
        type="button"
        className="vdpg-dr-btn vdpg-dr-danger"
        onClick={() => actions.onCancel(d.id)}
      >
        Cancel
      </button>,
    );
  } else if (vdlIsFail(d.status)) {
    if (d.status === 'import_failed' && canImport) {
      btns.push(
        <button
          key="import"
          type="button"
          className="vdpg-dr-btn vdpg-dr-accent"
          onClick={vdlGotoVideoImport}
        >
          Manual Import
        </button>,
      );
    }
    btns.push(
      <button
        key="retry"
        type="button"
        className="vdpg-dr-btn vdpg-dr-accent"
        onClick={() => actions.onRetry(d.id)}
      >
        Retry
      </button>,
    );
  }

  return (
    <>
      {!isYt && m.backdrop_url ? (
        <div className="vdpg-dr-back" style={{ backgroundImage: `url('${m.backdrop_url}')` }} />
      ) : null}
      <div className="vdpg-dr-body">
        {head}
        {lead}
        {!isYt ? (
          <>
            {genres ? <div className="vdpg-dr-genres">{genres}</div> : null}
            {watch}
            <CastRow meta={m} />
          </>
        ) : null}
        <div className="vdpg-dr-st">Download</div>
        <div className="vdpg-dr-facts">
          <Fact k="Status" v={vdlStatusOf(d.status).label} />
          {isYt ? (
            <>
              <Fact k="Channel" v={ctx.channel || ctx.channel_title || undefined} />
              <Fact k="Quality" v={d.quality_label || undefined} />
            </>
          ) : (
            <>
              <Fact k={vdlType(d.kind) === 'movie' ? 'Director' : 'Creator'} v={m.director} />
              <Fact k="Quality target" v={d.quality_label || undefined} />
              <Fact k="Release" v={d.release_title || undefined} />
              <Fact
                k="Format"
                v={[d.resolution, d.source, d.codec].filter(Boolean).join(' · ') || undefined}
              />
              <Fact k="Source" v={d.username ? `👤 ${d.username}` : undefined} />
              <Fact k="Availability" v={availability || undefined} />
            </>
          )}
          <Fact k="Size" v={d.size_bytes ? vdlFmtSize(d.size_bytes) : undefined} />
          <Fact k="Attempts" v={Number(d.attempts) > 1 ? `${d.attempts}x` : undefined} />
          {d.status === 'completed' && d.upgrade_watch ? (
            <Fact
              k="Upgrade"
              v="Below cutoff — still on the wishlist, watching for a better copy"
            />
          ) : null}
          {d.dest_path ? (
            <div className="vdpg-f vdpg-f-wide">
              <span className="vdpg-fk">Path</span>
              <span className="vdpg-fv vdpg-mono">{d.dest_path}</span>
              <button type="button" className="vdpg-copy" title="Copy path" onClick={copyPath}>
                ⧉
              </button>
            </div>
          ) : null}
          {vdlIsFail(d.status) && d.error ? (
            <div className="vdpg-f vdpg-f-wide vdpg-f-err">
              <span className="vdpg-fk">Error</span>
              <span className="vdpg-fv">{d.error}</span>
            </div>
          ) : null}
        </div>
        {btns.length ? <div className="vdpg-dr-actions">{btns}</div> : null}
      </div>
    </>
  );
}

/* ── season-pack group card ──────────────────────────────────────────────── */

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
  renderCard: (d: VdlDownload) => React.ReactNode;
}) {
  const { pack, episodes } = vdlSplitGroup(members);
  const first = pack || members[0];
  const c0 = vdlParseCtx(first);
  const total = episodes.length;
  let done = 0;
  let act = 0;
  let fail = 0;
  let pctSum = 0;
  for (const d of episodes) {
    if (d.status === 'completed') {
      done++;
      pctSum += 100;
    } else if (vdlIsActive(d.status)) {
      act++;
      pctSum += clampPct(d.progress);
    } else {
      fail++;
    }
  }
  const packActive = Boolean(pack && vdlIsActive(pack.status));
  const seasonTxt = c0.season != null ? `Season ${c0.season}` : 'Episodes';
  const tally = total
    ? `${done}/${total} imported${fail ? `  ·  ${fail} failed` : ''}`
    : packActive
      ? 'season pack downloading…'
      : 'season pack';
  const pct = packActive ? clampPct(pack?.progress) : total ? Math.round(pctSum / total) : 0;
  const status = packActive
    ? pack?.status === 'importing'
      ? 'importing'
      : 'downloading'
    : act
      ? `${act} active`
      : total && done === total
        ? '✓ done'
        : total
          ? `${fail} failed`
          : pack?.status || '';

  const visibleEps = episodes.filter((d) => vdlMatchesFilter(d.status, filter));
  const headVisible = pack ? vdlMatchesFilter(pack.status, filter) : false;

  const onGroupCancel = () => {
    const ids = episodes.filter((d) => vdlIsActive(d.status)).map((d) => d.id);
    // the pack is the head, not a card in the body — without this, cancelling
    // a batch whose episodes don't exist yet would cancel nothing at all.
    if (pack && packActive && !ids.includes(pack.id)) ids.push(pack.id);
    onCancel(ids);
  };

  return (
    <div
      className={`vdpg-group${collapsed ? ' vdpg-group--collapsed' : ''}`}
      data-vdpg-group={groupKey}
      style={{ display: visibleEps.length || headVisible ? '' : 'none' }}
    >
      <div className="vdpg-group-head" onClick={onToggle}>
        <span className="vdpg-group-caret">{collapsed ? '▸' : '▾'}</span>
        <div
          className={`vdpg-group-art${first.poster_url ? ' vdpg-has-poster' : ''}`}
          style={first.poster_url ? { backgroundImage: `url('${first.poster_url}')` } : undefined}
        >
          {first.poster_url ? null : '📺'}
        </div>
        <div className="vdpg-group-info">
          <div className="vdpg-group-title">{first.title || 'Show'}</div>
          <div className="vdpg-group-sub">
            {seasonTxt} · {tally}
          </div>
          {packActive || act ? (
            <div className="vdpg-prog vdpg-group-prog">
              <div className="vdpg-prog-fill" style={{ width: `${pct}%` }} />
            </div>
          ) : null}
        </div>
        <div className="vdpg-group-status">{status}</div>
        <div className="vdpg-group-act">
          {act || packActive ? (
            <button
              type="button"
              className="adl-row-cancel"
              title="Cancel all in this batch"
              onClick={(e) => {
                e.stopPropagation();
                onGroupCancel();
              }}
            >
              <XIcon />
            </button>
          ) : null}
        </div>
      </div>
      <div className="vdpg-group-body">
        {episodes.map((d) => (
          <div key={d.id} style={{ display: vdlMatchesFilter(d.status, filter) ? '' : 'none' }}>
            {renderCard(d)}
          </div>
        ))}
      </div>
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
  onToggleExpanded: (id: number) => void;
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
  onToggleExpanded,
  onToggleGroup,
  cardActions,
  onCancelGroup,
}: VdlListProps) {
  const canImport = useVdlCanImport();

  if (!loaded) {
    return (
      <div className="adl-list" data-vdl-list>
        <VdlSkeleton />
      </div>
    );
  }

  const renderCard = (d: VdlDownload) => (
    <VdlCard
      d={d}
      open={expanded.has(d.id)}
      meta={metaFor(d)}
      ensureMeta={ensureMeta}
      canImport={canImport}
      actions={cardActions}
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

  let shown = 0;
  const body = units.map((u) => {
    if ('single' in u) {
      const d = u.single;
      const vis = vdlMatchesFilter(d.status, filter);
      if (vis) shown++;
      return (
        <div key={d.id} style={{ display: vis ? '' : 'none' }}>
          {renderCard(d)}
        </div>
      );
    }
    const { pack, episodes } = vdlSplitGroup(u.members);
    const visN = episodes.filter((d) => vdlMatchesFilter(d.status, filter)).length;
    const headVis = pack ? vdlMatchesFilter(pack.status, filter) : false;
    if (visN || headVis) shown++;
    return (
      <VdlGroup
        key={u.group}
        groupKey={u.group}
        members={u.members}
        filter={filter}
        collapsed={collapsedGroups.has(u.group)}
        onToggle={() => onToggleGroup(u.group)}
        onCancel={onCancelGroup}
        renderCard={renderCard}
      />
    );
  });

  return (
    <div className="adl-list" data-vdl-list>
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
