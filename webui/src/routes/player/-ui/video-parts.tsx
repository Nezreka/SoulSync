/** pieces the movie and show pages share: cast row, version picker, browse toolbar */

import { Link } from '@tanstack/react-router';
import { useState } from 'react';

import type { CastMember, VideoFile } from '../-player.types';

import { remoteImage } from '../-lib/images';
import { rankVersions, versionLines, VIDEO_SORTS, type VideoSort } from '../-lib/video';
import { Art, HScroll, VerdictBadge, styles as s } from './kit';

export function CastRow({ cast }: { cast?: CastMember[] }) {
  if (!cast?.length) return null;
  return (
    <section className={s.rail} aria-label="Cast">
      <div className={s.railHead}>
        <h2 className={s.sectionTitle}>Cast</h2>
      </div>
      <HScroll className={s.people}>
        {cast.slice(0, 24).map((person, index) => {
          const body = (
            <>
              <div className={s.personPhoto}>
                <Art src={remoteImage(person.photo)} alt={person.name.slice(0, 1)} />
              </div>
              <div className={s.personName}>{person.name}</div>
              {person.character ? <div className={s.personRole}>{person.character}</div> : null}
            </>
          );
          return person.tmdb_id ? (
            <Link
              key={`${person.name}-${index}`}
              to="/player/person/$personId"
              params={{ personId: String(person.tmdb_id) }}
              className={s.person}
              data-nav
            >
              {body}
            </Link>
          ) : (
            <div key={`${person.name}-${index}`} className={s.person} tabIndex={0} data-nav>
              {body}
            </div>
          );
        })}
      </HScroll>
    </section>
  );
}

/**
 * the copies you own of this title. picking one is remembered for when play
 * is pressed (stage b); for now it shows which copy would play and how.
 */
export function VersionPicker({ files, bare }: { files?: VideoFile[]; bare?: boolean }) {
  const ranked = rankVersions(files ?? []);
  const [picked, setPicked] = useState<number | null>(null);
  if (!ranked.length) return null;
  const chosen = picked ?? ranked[0].id;
  if (ranked.length === 1) {
    const only = ranked[0];
    const lines = versionLines(only);
    return (
      <div className={s.picker}>
        {bare ? null : <div className={s.pickerLabel}>Your copy</div>}
        <div className={s.badges}>
          <span className={s.versionName}>{lines.name}</span>
          {only.playable ? (
            <VerdictBadge verdict={only.playable.verdict} reasons={only.playable.reasons} />
          ) : null}
        </div>
        <div className={s.versionSub}>{lines.detail}</div>
      </div>
    );
  }
  return (
    <div className={s.picker} role="group" aria-label="Version">
      {bare ? null : <div className={s.pickerLabel}>{ranked.length} versions</div>}
      {ranked.map((file) => {
        const lines = versionLines(file);
        return (
          <button
            key={file.id}
            type="button"
            className={s.version}
            aria-pressed={file.id === chosen}
            onClick={() => setPicked(file.id)}
            data-nav
          >
            <span>
              <div className={s.versionName}>{lines.name}</div>
              <div className={s.versionSub}>{lines.detail}</div>
            </span>
            {file.playable ? (
              <VerdictBadge verdict={file.playable.verdict} reasons={file.playable.reasons} />
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

export function BrowseToolbar({
  sort,
  onSort,
  genres,
  genre,
  onGenre,
  count,
  noun,
}: {
  sort: VideoSort;
  onSort: (sort: VideoSort) => void;
  genres: string[];
  genre: string;
  onGenre: (genre: string) => void;
  count: number;
  noun: string;
}) {
  return (
    <div className={s.toolbar}>
      <div className={s.tabs} role="tablist" aria-label="Sort">
        {VIDEO_SORTS.map((option) => (
          <button
            key={option.value}
            type="button"
            role="tab"
            className={s.tab}
            aria-selected={option.value === sort}
            onClick={() => onSort(option.value)}
            data-nav
          >
            {option.label}
          </button>
        ))}
      </div>
      <div className={s.fieldRow}>
        {genres.length ? (
          <select
            className={s.fieldInput}
            value={genre}
            onChange={(e) => onGenre(e.target.value)}
            aria-label="Genre"
            data-nav
          >
            <option value="">All genres</option>
            {genres.map((g) => (
              <option key={g} value={g}>
                {g}
              </option>
            ))}
          </select>
        ) : null}
        <span className={s.notYet}>
          {count.toLocaleString()} {noun}
        </span>
      </div>
    </div>
  );
}
