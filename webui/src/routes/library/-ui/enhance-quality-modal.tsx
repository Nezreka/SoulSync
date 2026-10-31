import { useEffect, useMemo, useRef, useState } from 'react';

import { DialogFrame } from '@/components/dialog/dialog';

import type { LibraryV2EnhanceTrack } from '../-library-v2.types';

import styles from './enhance-quality-modal.module.css';
import { LibraryCover } from './library-cover';
import { WishlistIcon } from './list-icons';

/** Enhance Quality (#1062 review, point 2): pick which files below their
 *  quality profile get upgraded, on the Wishlist or not. The picks go on the
 *  Wishlist and are searched right away; the profile alone decides what counts
 *  as better, so a search can come back empty and the Wishlist keeps looking.
 *
 *  one card per release with its cover, the jump it would make once per
 *  release (per track only where the tracks differ). */

interface Release {
  albumId: number;
  title: string;
  year: number | null;
  type: string | null;
  cover: string;
  remote: string | null;
  tracks: LibraryV2EnhanceTrack[];
  /** current and target when every track shares them */
  jump: { current: string; target: string } | null;
}

type Tier = 'lossy' | 'lossless' | 'hires' | 'unknown';

// what a quality label is, for its colour
export function qualityTier(label: string | null | undefined): Tier {
  if (!label) return 'unknown';
  const text = label.toLowerCase();
  if (/\b(24|32)[- ]?bit\b|hi-?res|dsd/.test(text)) return 'hires';
  if (/flac|alac|wav|aiff|ape|wavpack|\bwv\b|lossless/.test(text)) return 'lossless';
  return 'lossy';
}

function targetOf(track: LibraryV2EnhanceTrack): string {
  return track.target ?? track.profile;
}

function groupReleases(tracks: LibraryV2EnhanceTrack[]): Release[] {
  const byId = new Map<number, Release>();
  for (const track of tracks) {
    let release = byId.get(track.album_id);
    if (!release) {
      release = {
        albumId: track.album_id,
        title: track.album_title,
        year: track.album_year ?? null,
        type: track.album_type ?? null,
        cover: track.album_image_url ?? `/api/library/v2/artwork/album/${track.album_id}`,
        remote: track.album_remote_image_url ?? null,
        tracks: [],
        jump: null,
      };
      byId.set(track.album_id, release);
    }
    release.tracks.push(track);
  }
  for (const release of byId.values()) {
    const first = release.tracks[0];
    const current = first.current ?? 'Unknown';
    const target = targetOf(first);
    const same = release.tracks.every(
      (t) => (t.current ?? 'Unknown') === current && targetOf(t) === target,
    );
    release.jump = same ? { current, target } : null;
  }
  return [...byId.values()];
}

// the label most of the tracks carry
function mostCommon(values: string[]): string | null {
  const counts = new Map<string, number>();
  for (const value of values) counts.set(value, (counts.get(value) ?? 0) + 1);
  let best: string | null = null;
  let bestCount = 0;
  for (const [value, count] of counts) {
    if (count > bestCount) {
      best = value;
      bestCount = count;
    }
  }
  return best;
}

function releaseMeta(release: Release): string {
  const raw = (release.type ?? '').toLowerCase();
  const type = raw === 'ep' ? 'EP' : raw ? raw.charAt(0).toUpperCase() + raw.slice(1) : null;
  const count = `${release.tracks.length} ${release.tracks.length === 1 ? 'track' : 'tracks'}`;
  return [release.year, type, count].filter(Boolean).join(' · ');
}

function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

function QualityJump({ current, target }: { current: string; target: string }) {
  return (
    <span className={styles.jump}>
      <span className={styles.chip} data-tier={qualityTier(current)} title="The file you have">
        {current}
      </span>
      <svg className={styles.arrow} viewBox="0 0 16 16" aria-hidden="true">
        <path
          d="M2 8h11M9 4l4 4-4 4"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
      <span
        className={`${styles.chip} ${styles.chipTarget}`}
        data-tier={qualityTier(target)}
        title="What the quality profile asks for"
      >
        {target}
      </span>
    </span>
  );
}

function Check({
  checked,
  mixed = false,
  label,
  onChange,
}: {
  checked: boolean;
  mixed?: boolean;
  label: string;
  onChange: () => void;
}) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = mixed;
  }, [mixed]);
  return (
    <input
      ref={ref}
      type="checkbox"
      className={styles.check}
      aria-label={label}
      checked={checked}
      onChange={onChange}
      onClick={(event) => event.stopPropagation()}
    />
  );
}

function CoverFallback({ className }: { className: string }) {
  return (
    <span className={`${className} ${styles.coverEmpty}`} aria-hidden="true">
      ♪
    </span>
  );
}

export function EnhanceQualityModal({
  title,
  tracks,
  onEnhance,
  onClose,
}: {
  title: string;
  tracks: LibraryV2EnhanceTrack[];
  onEnhance: (trackIds: number[]) => void;
  onClose: () => void;
}) {
  const [selected, setSelected] = useState(() => new Set(tracks.map((t) => t.track_id)));
  const releases = useMemo(() => groupReleases(tracks), [tracks]);
  const goal = useMemo(() => mostCommon(tracks.map(targetOf)), [tracks]);
  const from = useMemo(() => mostCommon(tracks.map((t) => t.current ?? 'Unknown')), [tracks]);
  const allPicked = tracks.length > 0 && selected.size === tracks.length;

  function toggle(ids: number[], on: boolean) {
    const next = new Set(selected);
    for (const id of ids) {
      if (on) next.add(id);
      else next.delete(id);
    }
    setSelected(next);
  }

  const stack = releases.slice(0, 3);

  return (
    <DialogFrame
      open
      aria-label={`Enhance Quality — ${title}`}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      viewportClassName={styles.viewport}
      className={styles.sheet}
    >
      <header className={styles.hero}>
        <div className={styles.wash} aria-hidden="true">
          {stack.map((release) => (
            <LibraryCover
              key={release.albumId}
              src={release.cover}
              remote={release.remote}
              className={styles.washArt}
              fallback={null}
            />
          ))}
        </div>
        <div className={styles.stack} data-count={stack.length} aria-hidden="true">
          {stack.map((release) => (
            <LibraryCover
              key={release.albumId}
              src={release.cover}
              remote={release.remote}
              className={styles.stackArt}
              fallback={<CoverFallback className={styles.stackArt} />}
            />
          ))}
        </div>
        <div className={styles.heroText}>
          <span className={styles.eyebrow}>
            <svg viewBox="0 0 16 16" aria-hidden="true">
              <path
                d="M8 1.5l1.6 4.4 4.4 1.6-4.4 1.6L8 13.5 6.4 9.1 2 7.5l4.4-1.6z"
                fill="currentColor"
              />
            </svg>
            Enhance Quality
          </span>
          <h2 className={styles.title}>{title}</h2>
          <p className={styles.summary}>
            {tracks.length
              ? `${plural(tracks.length, 'track')} on ${plural(releases.length, 'release')} can sound better.`
              : 'Everything here already meets its quality profile.'}
          </p>
          {from && goal ? <QualityJump current={from} target={goal} /> : null}
        </div>
        <button type="button" className={styles.close} aria-label="Close" onClick={onClose}>
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <path
              d="M4 4l8 8M12 4l-8 8"
              stroke="currentColor"
              strokeWidth="1.7"
              strokeLinecap="round"
            />
          </svg>
        </button>
      </header>

      <div className={styles.toolbar}>
        <label className={styles.selectAll}>
          <Check
            checked={allPicked}
            mixed={selected.size > 0 && !allPicked}
            label={allPicked ? 'Deselect all' : 'Select all'}
            onChange={() =>
              setSelected(allPicked ? new Set() : new Set(tracks.map((t) => t.track_id)))
            }
          />
          <span>{allPicked ? 'Deselect all' : 'Select all'}</span>
        </label>
        <span className={styles.picked}>
          {selected.size} of {tracks.length} picked
        </span>
      </div>

      <div className={styles.body}>
        {releases.map((release) => {
          const ids = release.tracks.map((t) => t.track_id);
          const picked = ids.filter((id) => selected.has(id)).length;
          return (
            <section key={release.albumId} className={styles.release} data-picked={picked > 0}>
              <div className={styles.releaseHead}>
                <Check
                  checked={picked === ids.length}
                  mixed={picked > 0 && picked < ids.length}
                  label={`Enhance all of ${release.title}`}
                  onChange={() => toggle(ids, picked !== ids.length)}
                />
                <LibraryCover
                  src={release.cover}
                  remote={release.remote}
                  className={styles.cover}
                  fallback={<CoverFallback className={styles.cover} />}
                />
                <div className={styles.releaseInfo}>
                  <span className={styles.releaseTitle}>{release.title}</span>
                  <span className={styles.releaseMeta}>{releaseMeta(release)}</span>
                </div>
                {release.jump ? <QualityJump {...release.jump} /> : null}
              </div>
              <ol className={styles.tracks}>
                {release.tracks.map((t) => {
                  const on = selected.has(t.track_id);
                  return (
                    <li key={t.track_id}>
                      <label className={styles.track} data-on={on}>
                        <Check
                          checked={on}
                          label={`Enhance ${t.title}`}
                          onChange={() => toggle([t.track_id], !on)}
                        />
                        <span className={styles.num}>{t.track_number ?? '·'}</span>
                        <span className={styles.trackTitle}>{t.title}</span>
                        {t.monitored ? (
                          <span className={styles.listed} title="On Wishlist">
                            <WishlistIcon on size={14} />
                            <span className={styles.srOnly}>On Wishlist</span>
                          </span>
                        ) : null}
                        {release.jump ? null : (
                          <QualityJump current={t.current ?? 'Unknown'} target={targetOf(t)} />
                        )}
                      </label>
                    </li>
                  );
                })}
              </ol>
            </section>
          );
        })}
      </div>

      <footer className={styles.footer}>
        <p className={styles.footNote}>
          {selected.size
            ? 'Picked tracks go on your Wishlist and are searched now. If no better copy turns up, the Wishlist keeps looking.'
            : 'Pick the tracks to upgrade.'}
        </p>
        <div className={styles.footActions}>
          <button type="button" className={styles.ghost} onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className={styles.primary}
            disabled={selected.size === 0}
            onClick={() => onEnhance([...selected])}
          >
            <svg viewBox="0 0 16 16" aria-hidden="true">
              <path
                d="M8 1.5l1.6 4.4 4.4 1.6-4.4 1.6L8 13.5 6.4 9.1 2 7.5l4.4-1.6z"
                fill="currentColor"
              />
            </svg>
            {`Enhance (${selected.size})`}
          </button>
        </div>
      </footer>
    </DialogFrame>
  );
}
