/**
 * The video dashboard hero: the music hero's shape on video's data. Greeting,
 * the library's size, the library console strip on the left; the video
 * worker orbs on their own stage on the right.
 *
 * data-foreign-orbs keeps the music orbs (worker-orbs.js) off this stage,
 * since the page is #dashboard-page and .orb-stage like theirs.
 * video-worker-orbs.js anchors `[data-video-orb-stage]` (canvas, nucleus,
 * hover) and reads the containers below every frame, and video-enrichment.js
 * writes each button's state classes and tooltip text straight into this DOM.
 * So the stage renders once and never re-renders (memo, no props): a React
 * update would put back what the vanilla wrote.
 */

import type { CSSProperties, ReactNode } from 'react';

import { memo, useEffect, useRef, useState } from 'react';

import { greetingForHour } from '../-dash.hello';

interface VideoWorker {
  svc: string;
  accent: string;
  title: string;
  header: string;
  logo: { src: string; alt: string } | { glyph: string };
  idle: string;
}

/** transcribed from the vanilla dashboard header, one per enrichment worker */
const WORKERS: VideoWorker[] = [
  {
    svc: 'tmdb',
    accent: '56, 189, 248',
    title: 'TMDB Enrichment',
    header: 'TMDB Enrichment',
    logo: { src: '/static/img/brands/tmdb.svg', alt: 'TMDB' },
    idle: 'No active matches',
  },
  {
    svc: 'tvdb',
    accent: '168, 85, 247',
    title: 'TVDB Enrichment',
    header: 'TVDB Enrichment',
    logo: { src: '/static/img/brands/tvdb.svg', alt: 'TVDB' },
    idle: 'No active matches',
  },
  {
    svc: 'omdb',
    accent: '245, 197, 24',
    title: 'OMDb Ratings',
    header: 'OMDb Ratings',
    logo: { glyph: '★' },
    idle: 'No active matches',
  },
  {
    svc: 'youtube',
    accent: '255, 59, 59',
    title: 'YouTube Dates',
    header: 'YouTube Dates',
    logo: { glyph: '▶' },
    idle: 'No channels queued',
  },
  {
    svc: 'fanart',
    accent: '228, 80, 154',
    title: 'fanart.tv Artwork',
    header: 'fanart.tv Artwork',
    logo: { glyph: '\u{1F3A8}' },
    idle: 'No active matches',
  },
  {
    svc: 'opensubtitles',
    accent: '34, 160, 121',
    title: 'OpenSubtitles',
    header: 'OpenSubtitles',
    logo: { glyph: '\u{1F4AC}' },
    idle: 'No active matches',
  },
  {
    svc: 'ryd',
    accent: '239, 68, 68',
    title: 'YouTube Votes (Return YouTube Dislike)',
    header: 'YouTube Votes',
    logo: { glyph: '\u{1F44D}' },
    idle: 'No active matches',
  },
  {
    svc: 'sponsorblock',
    accent: '0, 180, 160',
    title: 'SponsorBlock',
    header: 'SponsorBlock',
    logo: { glyph: '⏭' },
    idle: 'No active matches',
  },
  {
    svc: 'dearrow',
    accent: '31, 119, 206',
    title: 'DeArrow (better YouTube titles)',
    header: 'DeArrow',
    logo: { glyph: '\u{1F3F9}' },
    idle: 'No active matches',
  },
  {
    svc: 'trakt',
    accent: '237, 28, 36',
    title: 'Trakt (audience rating)',
    header: 'Trakt',
    logo: { glyph: '★' },
    idle: 'No active matches',
  },
  {
    svc: 'tvmaze',
    accent: '61, 214, 192',
    title: 'TVmaze (TV rating)',
    header: 'TVmaze',
    logo: { glyph: '\u{1F4FA}' },
    idle: 'No active matches',
  },
  {
    svc: 'anilist',
    accent: '2, 169, 255',
    title: 'AniList (anime score)',
    header: 'AniList',
    logo: { glyph: '\u{1F38C}' },
    idle: 'No active matches',
  },
  {
    svc: 'wikidata',
    accent: '51, 153, 102',
    title: 'Wikidata (official site)',
    header: 'Wikidata',
    logo: { glyph: '\u{1F517}' },
    idle: 'No active matches',
  },
];

export const VIDEO_WORKER_IDS = WORKERS.map((w) => w.svc);

/** busy = a worker button the vanilla marked active. counted off the DOM,
 * because that is where video-enrichment.js keeps the state. */
function useBusyWorkers(ref: React.RefObject<HTMLDivElement | null>) {
  const [busy, setBusy] = useState(0);
  useEffect(() => {
    const root = ref.current;
    if (!root) return undefined;
    const count = () => setBusy(root.querySelectorAll('.video-enrich-button.active').length);
    count();
    const mo = new MutationObserver(count);
    mo.observe(root, { subtree: true, attributes: true, attributeFilter: ['class'] });
    return () => mo.disconnect();
  }, [ref]);
  return busy;
}

const OrbButtons = memo(function OrbButtons() {
  return (
    <div className="header-actions">
      {WORKERS.map((w) => (
        <div
          key={w.svc}
          className="video-enrich-container"
          style={{ '--ve-accent': w.accent } as CSSProperties}
        >
          <button
            className="video-enrich-button"
            type="button"
            data-video-enrich={w.svc}
            title={w.title}
          >
            {'src' in w.logo ? (
              <img className="video-enrich-logo" src={w.logo.src} alt={w.logo.alt} />
            ) : (
              <span className="video-enrich-glyph" aria-hidden="true">
                {w.logo.glyph}
              </span>
            )}
            <span className="video-enrich-spinner" aria-hidden="true"></span>
          </button>
          <div className="video-enrich-tooltip" data-video-enrich-tooltip={w.svc}>
            <div className="tooltip-content">
              <div className="tooltip-header">{w.header}</div>
              <div className="tooltip-body">
                <div className="tooltip-status">
                  Status: <span data-video-enrich-status="">Idle</span>
                </div>
                <div className="tooltip-current" data-video-enrich-current="">
                  {w.idle}
                </div>
                <div className="tooltip-progress" data-video-enrich-progress="">
                  Progress: 0 / 0
                </div>
              </div>
            </div>
          </div>
        </div>
      ))}
      {/* 13 orbs never fill the last row, so the hub button gets its own */}
      <span className="orb-stage-break" aria-hidden="true"></span>
      <button
        className="em-manage-btn"
        type="button"
        data-video-manage-workers=""
        title="Manage video enrichment workers"
      >
        <span className="em-manage-btn-icon">
          <img src="/static/trans2.png" alt="SoulSync" className="em-manage-btn-logo" />
        </span>
        <span className="em-manage-btn-label">Manage Workers</span>
      </button>
    </div>
  );
});

function OrbStage() {
  const ref = useRef<HTMLDivElement | null>(null);
  const busy = useBusyWorkers(ref);
  return (
    <div className="orb-stage" data-video-orb-stage="" data-foreign-orbs="" ref={ref}>
      <OrbButtons />
      <div className="orb-stage-caption" aria-live="polite">
        <span className={busy > 0 ? 'orb-stage-dot orb-stage-dot--busy' : 'orb-stage-dot'}></span>
        {busy === 0 ? 'Workers resting' : busy === 1 ? '1 worker busy' : `${busy} workers busy`}
      </div>
    </div>
  );
}

function useProfileName() {
  const [name, setName] = useState((window._currentProfileName ?? '').trim());
  useEffect(() => {
    const onChange = () => setName((window._currentProfileName ?? '').trim());
    window.addEventListener('ss:webui-profile-context-changed', onChange);
    return () => window.removeEventListener('ss:webui-profile-context-changed', onChange);
  }, []);
  return name;
}

export interface HeroNumberView {
  id: string;
  label: string;
  value: string;
}

export function VideoHero({
  numbers,
  onNumber,
  library,
}: {
  numbers: HeroNumberView[];
  onNumber: () => void;
  library: ReactNode;
}) {
  const profileName = useProfileName();
  const greeting = greetingForHour(new Date().getHours());
  const today = new Date().toLocaleDateString('en-US', {
    weekday: 'long',
    month: 'long',
    day: 'numeric',
  });
  const question = greeting.endsWith('?');
  const lead = question ? greeting.slice(0, -1) : greeting;
  return (
    <div className="dashboard-header dash-hero" data-video-hero="" data-foreign-orbs="">
      <div className="dashboard-header-sweep" aria-hidden="true">
        <span></span>
      </div>
      <div className="header-text header-hello dash-hero-main">
        <p className="dash-hero-eyebrow">{today}</p>
        <h2 className="hello-greeting">
          <span>
            {profileName ? (
              <>
                <span className="hello-greet-lead">{lead}, </span>
                <span className="hello-greet-name">{profileName}</span>
                {question ? '?' : null}
              </>
            ) : (
              greeting
            )}
          </span>
        </h2>
        {numbers.length ? (
          <div className="hello-stats dash-hero-numbers">
            {numbers.map((n) => (
              <button
                key={n.id}
                type="button"
                className="hello-stat dash-hero-number"
                onClick={onNumber}
              >
                <span className="dash-hero-number-value">{n.value}</span>
                <span className="dash-hero-number-label">{n.label}</span>
              </button>
            ))}
          </div>
        ) : null}
        {library}
      </div>
      <OrbStage />
    </div>
  );
}
