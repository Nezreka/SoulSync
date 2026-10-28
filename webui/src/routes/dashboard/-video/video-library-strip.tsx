/**
 * The library console strip under the hero numbers: the music strip's markup
 * (so it inherits its look) on the video server. Scans go through
 * video-scan.js, the one place every surface starts and follows a video
 * scan: this sends 'soulsync:video-scan-start' and listens to its progress
 * and done events.
 */

import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';

import type { ScanStatus, VideoDashboardStats } from './-vdash.model';

import { MORE_ICON, useDismiss } from '../-ui/library-card';
import { VDASH_KEY } from './-vdash.api';
import { libraryStrip } from './-vdash.model';

const CARD_CLASS: Record<string, string> = {
  checking: 'library-status-card',
  scanning: 'library-status-card scanning',
  'no-server': 'library-status-card needs-setup',
  empty: 'library-status-card empty-library',
  ready: 'library-status-card has-data',
};

function startScan(mode: 'full' | 'deep') {
  document.dispatchEvent(
    new CustomEvent('soulsync:video-scan-start', { detail: { mode, media_type: 'all' } }),
  );
}

function goVideo(page: string) {
  document.dispatchEvent(new CustomEvent('soulsync:video-navigate', { detail: { page } }));
}

export function VideoLibraryStrip({
  stats,
  scan,
  isAdmin,
}: {
  stats: VideoDashboardStats | undefined;
  scan: ScanStatus | undefined;
  isAdmin: boolean;
}) {
  const qc = useQueryClient();
  // the live scan, from video-scan.js's events; null = use the fetched status
  const [live, setLive] = useState<ScanStatus | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);
  useDismiss(menuOpen, () => setMenuOpen(false), menuRef);

  useEffect(() => {
    const onProgress = (e: Event) => {
      const s = (e as CustomEvent<ScanStatus>).detail;
      if (s?.state === 'scanning') setLive(s);
    };
    const onDone = () => {
      setLive(null);
      void qc.invalidateQueries({ queryKey: VDASH_KEY });
    };
    document.addEventListener('soulsync:video-scan-progress', onProgress);
    document.addEventListener('soulsync:video-scan-done', onDone);
    return () => {
      document.removeEventListener('soulsync:video-scan-progress', onProgress);
      document.removeEventListener('soulsync:video-scan-done', onDone);
    };
  }, [qc]);

  const current = live ?? scan;
  const view = libraryStrip(stats, current, Date.now());
  const scanning = view.state === 'scanning';
  const pct = current?.percent ?? null;

  const pick = (fn: () => void) => () => {
    setMenuOpen(false);
    fn();
  };

  return (
    <div className="dash-hero-library" data-card="library">
      <div className={CARD_CLASS[view.state]}>
        <div className="library-status-header">
          <div className="library-status-info">
            <span className="library-status-dot" aria-hidden="true"></span>
            <h4 className="library-status-title">{view.title}</h4>
            <p className="library-status-subtitle">{view.subtitle}</p>
          </div>
          <div className="library-status-actions" ref={menuRef}>
            {view.state === 'no-server' ? (
              <button className="library-status-btn" onClick={() => goVideo('video-settings')}>
                <span>Connect</span>
              </button>
            ) : (
              <button
                className={scanning ? 'library-status-btn scanning' : 'library-status-btn'}
                disabled={scanning}
                title="Pick up what changed on your server"
                onClick={() => startScan('full')}
              >
                <svg
                  width="14"
                  height="14"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.5"
                >
                  <polyline points="23 4 23 10 17 10" />
                  <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
                </svg>
                <span>{scanning ? 'Scanning' : 'Scan'}</span>
              </button>
            )}
            <button
              type="button"
              className="library-status-more"
              aria-label="More library actions"
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen((o) => !o)}
            >
              {MORE_ICON}
            </button>
            <div className="library-status-menu" role="menu" hidden={!menuOpen}>
              {view.state !== 'no-server' ? (
                <button
                  role="menuitem"
                  className="library-status-menu-item"
                  disabled={scanning}
                  onClick={pick(() => startScan('deep'))}
                >
                  Deep scan
                  <span className="library-status-menu-hint">re-read every title</span>
                </button>
              ) : null}
              <button
                role="menuitem"
                className="library-status-menu-item"
                onClick={pick(() => goVideo('video-library'))}
              >
                Browse library
              </button>
              <button
                role="menuitem"
                className="library-status-menu-item"
                onClick={pick(() => window.VideoPoster?.openSearch?.())}
              >
                Poster manager
              </button>
              {isAdmin ? (
                <button
                  role="menuitem"
                  className="library-status-menu-item"
                  onClick={pick(() => goVideo('video-tools'))}
                >
                  Repair
                </button>
              ) : null}
            </div>
          </div>
        </div>
        {scanning ? (
          <div className="library-status-progress">
            <div className="library-status-phase">
              {current?.phase
                ? current.phase.charAt(0).toUpperCase() + current.phase.slice(1)
                : 'Scanning'}
            </div>
            <div className="library-status-bar">
              <div className="library-status-bar-fill" style={{ width: `${pct ?? 100}%` }}></div>
            </div>
            <div className="library-status-progress-detail">
              {`${current?.movies ?? 0} movies, ${current?.shows ?? 0} shows${pct != null ? ` · ${pct}%` : ''}`}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
