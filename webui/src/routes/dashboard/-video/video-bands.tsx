/**
 * The video dashboard's main column, each band in the music rail's masthead.
 * Every band renders nothing until it has something to say: an empty rail
 * headed "Continue watching" is worse than no rail.
 */

import type { MouseEvent, ReactNode } from 'react';

import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import type { ActiveDownload, ContinueItem, Coverage, RecentItem } from './-vdash.model';

import { continueQuery, coverageQuery, downloadsQuery } from './-vdash.api';
import {
  continueHref,
  coverageRings,
  DL_MAX_ROWS,
  dlArt,
  dlHasBar,
  dlPct,
  dlStatus,
  dlSub,
  dlTitle,
  inFlight,
  posterOf,
  recentHref,
  recentSub,
  remaining,
  watchedPct,
} from './-vdash.model';
import styles from './video-dashboard.module.css';

/** a plain left click routes in-app; modified clicks keep the real href */
function plainClick(e: MouseEvent) {
  return e.button === 0 && !e.metaKey && !e.ctrlKey && !e.shiftKey && !e.altKey;
}

function openDetail(kind: string, id: number) {
  document.dispatchEvent(
    new CustomEvent('soulsync:video-open-detail', { detail: { kind, id, source: 'library' } }),
  );
}

export function goVideo(page: string) {
  document.dispatchEvent(new CustomEvent('soulsync:video-navigate', { detail: { page } }));
}

function Masthead({
  title,
  subtitle,
  action,
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
}) {
  return (
    <div className="dash-rail-head">
      <div className="dash-rail-titleblock">
        <h2 className="dash-rail-title">{title}</h2>
        {subtitle ? <span className="dash-rail-subtitle">{subtitle}</span> : null}
      </div>
      {action}
    </div>
  );
}

// ── continue watching ───────────────────────────────────────────────────────
// landscape, because a still of the scene you stopped on is what you
// recognise; a poster is how you browse, not how you resume

function ContinueCard({ it }: { it: ContinueItem }) {
  const pct = watchedPct(it);
  const meta = it.reason === 'up_next' ? 'Up next' : remaining(it);
  const img = it.image_url || '';
  return (
    <a
      className="vcw-card"
      href={continueHref(it)}
      title={it.subtitle ? `${it.title} · ${it.subtitle}` : it.title}
      onClick={(e) => {
        if (!plainClick(e)) return;
        e.preventDefault();
        if (it.kind === 'show' && it.show_id) openDetail('show', it.show_id);
        else openDetail('movie', it.id);
      }}
    >
      <span className="vcw-art" style={img ? { backgroundImage: `url("${img}")` } : undefined}>
        {img ? null : (
          <span className="vcw-art-fallback">{(it.title || '?').charAt(0).toUpperCase()}</span>
        )}
        {it.reason === 'up_next' ? <span className="vcw-badge">Up next</span> : null}
        {/* an arrow, not a play triangle: the media server plays, this opens the title */}
        <span className="vcw-open" aria-hidden="true">
          ›
        </span>
        {pct > 0 ? (
          <span className="vcw-bar">
            <span className="vcw-bar-fill" style={{ width: `${pct}%` }}></span>
          </span>
        ) : null}
      </span>
      <span className="vcw-meta">
        <span className="vcw-name">{it.title}</span>
        <span className="vcw-sub">{it.subtitle || ''}</span>
        {meta ? <span className="vcw-left">{meta}</span> : null}
      </span>
    </a>
  );
}

export function ContinueBand() {
  const { data } = useQuery(continueQuery());
  if (!data?.length) return null;
  return (
    <article className="dash-card dash-card--rail" data-card="continue">
      <Masthead title="Continue watching" subtitle="Pick up where you left off on your server." />
      <div className="vcw-rail">
        {data.map((it) => (
          <ContinueCard key={`${it.kind}-${it.id}`} it={it} />
        ))}
      </div>
    </article>
  );
}

// ── downloading now ─────────────────────────────────────────────────────────

function DownloadCard({ d }: { d: ActiveDownload }) {
  const [noArt, setNoArt] = useState(false);
  const pct = dlPct(d);
  const title = dlTitle(d);
  const art = dlArt(d);
  const sub = dlSub(d);
  const bar = dlHasBar(d);
  return (
    <div className="vdn-card" title={d.year ? `${title} (${d.year})` : title}>
      <span className={noArt || !art ? 'vdn-art is-noart' : 'vdn-art'}>
        {art && !noArt ? (
          <img src={art} alt="" loading="lazy" onError={() => setNoArt(true)} />
        ) : null}
        <span className="vdn-letter">{title.charAt(0).toUpperCase()}</span>
      </span>
      <span className="vdn-body">
        <span className="vdn-name">{title}</span>
        <span className="vdn-line">
          <span className={`vdn-pill vdn-pill--${d.status || ''}`}>{dlStatus(d)}</span>
          {pct && bar ? <span className="vdn-pct">{pct}%</span> : null}
        </span>
        {sub ? <span className="vdn-sub">{sub}</span> : null}
        {bar ? (
          <span className="vdn-bar">
            <span className="vdn-bar-fill" style={{ width: `${pct}%` }}></span>
          </span>
        ) : null}
      </span>
    </div>
  );
}

export function DownloadsBand() {
  const { data } = useQuery(downloadsQuery());
  const rows = inFlight(data);
  if (!rows.length) return null;
  const shown = rows.slice(0, DL_MAX_ROWS);
  const extra = rows.length - shown.length;
  return (
    <article className="dash-card dash-side-card" data-card="downloads">
      <header className="dash-side-head">
        <h3 className="dash-side-title">
          Downloading now <span className="vdn-count">{rows.length}</span>
        </h3>
        <button type="button" className="dash-side-link" onClick={() => goVideo('video-downloads')}>
          Downloads
        </button>
      </header>
      <div className="vdn-grid">
        {shown.map((d) => (
          <DownloadCard key={String(d.id)} d={d} />
        ))}
        {extra > 0 ? <div className="vdn-more">and {extra} more</div> : null}
      </div>
    </article>
  );
}

// ── recently added ──────────────────────────────────────────────────────────

type RecentTab = 'all' | 'movie' | 'show';

function PosterCard({ it, now }: { it: RecentItem; now: number }) {
  const [broken, setBroken] = useState(false);
  const sub = recentSub(it, now);
  return (
    <a
      className={`dash-rail-card ${styles.poster}`}
      href={recentHref(it)}
      title={it.title}
      onClick={(e) => {
        if (!plainClick(e)) return;
        e.preventDefault();
        openDetail(it.kind, it.id);
      }}
    >
      <span className={styles.posterArt}>
        {broken ? (
          <span className={styles.posterLetter}>{it.title.charAt(0).toUpperCase()}</span>
        ) : (
          <img src={posterOf(it, 300)} alt="" loading="lazy" onError={() => setBroken(true)} />
        )}
        <span className={styles.posterKind}>{it.kind === 'show' ? 'Show' : 'Movie'}</span>
      </span>
      <span className={styles.posterName}>{it.title}</span>
      {sub ? <span className={styles.posterSub}>{sub}</span> : null}
    </a>
  );
}

export function RecentBand({ items }: { items: RecentItem[] | undefined }) {
  const [tab, setTab] = useState<RecentTab>('all');
  if (!items?.length) return null;
  const movies = items.filter((i) => i.kind === 'movie').length;
  const shows = items.length - movies;
  const shown = tab === 'all' ? items : items.filter((i) => i.kind === tab);
  const now = Date.now();
  const tabs: [RecentTab, string][] = [['all', 'Everything']];
  // a tab that would only repeat Everything is noise
  if (movies && shows) tabs.push(['movie', 'Movies'], ['show', 'Shows']);
  return (
    <article className="dash-card dash-card--rail" data-card="recent">
      <Masthead
        title="Recently added"
        subtitle="The newest movies and shows on your server."
        action={
          tabs.length > 1 ? (
            <div className="dash-band-tabs" role="tablist" aria-label="Filter recently added">
              {tabs.map(([id, label]) => (
                <button
                  key={id}
                  type="button"
                  role="tab"
                  aria-selected={tab === id}
                  className={`dash-band-tab${tab === id ? ' active' : ''}`}
                  onClick={() => setTab(id)}
                >
                  {label}
                </button>
              ))}
            </div>
          ) : null
        }
      />
      <div className="dash-rail">
        {shown.map((it) => (
          <PosterCard key={`${it.kind}-${it.id}`} it={it} now={now} />
        ))}
      </div>
    </article>
  );
}

// ── studios ─────────────────────────────────────────────────────────────────
// the payoff band, last like music's Listen band: what you can make out of a
// library once its metadata is in. admin only, the editors are management.

const RING_R = 20;
const RING_C = 2 * Math.PI * RING_R;

function CoverageRings({ data }: { data: Coverage | undefined }) {
  const rings = coverageRings(data);
  if (!rings.length) return null;
  return (
    <div
      className={styles.rings}
      title="Overlays and collections draw posters, ratings and logos from your TMDB and TVDB metadata. Fuller rings mean richer results."
    >
      <span className={styles.ringsLabel}>Metadata ready</span>
      {rings.map((r) => (
        <span key={r.key} className={styles.ring} data-ring={r.key}>
          <svg viewBox="0 0 46 46" aria-hidden="true">
            <circle className={styles.ringBg} cx="23" cy="23" r={RING_R} />
            <circle
              className={styles.ringFill}
              cx="23"
              cy="23"
              r={RING_R}
              strokeDasharray={RING_C.toFixed(1)}
              strokeDashoffset={(RING_C - (RING_C * r.pct) / 100).toFixed(1)}
            />
          </svg>
          <span className={styles.ringPct}>{r.pct}</span>
          <span className={styles.ringLabel}>
            <b>{r.provider}</b> {r.kind}
          </span>
        </span>
      ))}
    </div>
  );
}

function StudioWash({ src }: { src: string }) {
  return <img className={styles.studioWash} src={src} alt="" aria-hidden="true" loading="lazy" />;
}

export function StudiosBand({ recent }: { recent: RecentItem[] | undefined }) {
  const { data } = useQuery(coverageQuery());
  const posters = (recent ?? []).slice(0, 3);
  const lead = (recent ?? []).find((i) => i.kind === 'movie') ?? posters[0];
  // the fan's middle poster is the one on top
  const fanLead = posters[1] ?? posters[0];
  return (
    <article className="dash-card dash-card--rail" data-card="studios">
      <Masthead
        title="Make it yours"
        subtitle="Poster overlays and smart collections, built from your library's metadata."
        action={<CoverageRings data={data} />}
      />
      <div className={styles.studios}>
        <button
          type="button"
          className={styles.studio}
          data-studio="overlay"
          onClick={() => window.VideoOverlayEditor?.open?.()}
        >
          {lead ? <StudioWash src={posterOf(lead, 300)} /> : null}
          <span className={styles.studioArt} aria-hidden="true">
            <span className={styles.studioCanvas}>
              {lead ? (
                <img
                  className={styles.studioPoster}
                  src={posterOf(lead, 300)}
                  alt=""
                  loading="lazy"
                />
              ) : null}
              <span className={styles.studioBadges}>
                <span>4K</span>
                <span>IMDb 8.4</span>
                <span>HDR</span>
              </span>
            </span>
          </span>
          <span className={styles.studioText}>
            <span className={styles.studioKicker}>Artwork</span>
            <span className={styles.studioName}>Overlay Studio</span>
            <span className={styles.studioBlurb}>
              Rating, resolution and logo badges on your posters, designed on a live canvas.
            </span>
            <span className={styles.studioCta}>
              Open studio <span aria-hidden="true">&rarr;</span>
            </span>
          </span>
        </button>
        <button
          type="button"
          className={styles.studio}
          data-studio="collection"
          onClick={() => window.VideoCollectionEditor?.open?.()}
        >
          {fanLead ? <StudioWash src={posterOf(fanLead, 300)} /> : null}
          <span className={styles.studioArt} aria-hidden="true">
            <span className={styles.studioStack}>
              {posters.map((p) => (
                <img key={`${p.kind}-${p.id}`} src={posterOf(p, 300)} alt="" loading="lazy" />
              ))}
            </span>
          </span>
          <span className={styles.studioText}>
            <span className={styles.studioKicker}>Collections</span>
            <span className={styles.studioName}>Collection Studio</span>
            <span className={styles.studioBlurb}>
              Franchises, universes, decades and charts, synced to your server.
            </span>
            <span className={styles.studioCta}>
              Open studio <span aria-hidden="true">&rarr;</span>
            </span>
          </span>
        </button>
      </div>
    </article>
  );
}
