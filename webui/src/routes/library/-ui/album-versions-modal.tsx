import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import type {
  LibraryV2AlbumMergeCandidate,
  LibraryV2AlbumVersion,
  LibraryV2AlbumVersionGroup,
  LibraryV2AlbumVersionPreview,
} from '../-library-v2.api';

import {
  applyLibraryV2AlbumVersion,
  fetchLibraryV2AlbumVersions,
  LIBRARY_V2_QUERY_KEY,
  previewLibraryV2AlbumVersion,
} from '../-library-v2.api';
import styles from './library-v2-page.module.css';
import { LibraryToolDialog } from './tool-dialog';

/**
 * #2145: picard's "Other versions" on the album page. Lists the album's
 * editions (musicbrainz release group, the primary source's same-title
 * releases) with track counts, and switches the album to the one picked
 * through the manual album match, after a preview and a confirm.
 */

/** "17 tracks · 2008 · US · CD · Arista Nashville · deluxe" */
export function versionFacts(v: LibraryV2AlbumVersion): string {
  return [
    v.track_count ? `${v.track_count} tracks` : 'track count unknown',
    v.date || (v.year ? String(v.year) : null),
    v.country,
    v.format,
    v.label,
    v.disambiguation,
    v.status && v.status.toLowerCase() !== 'official' ? v.status : null,
  ]
    .filter(Boolean)
    .join(' · ');
}

/** What switching changes, in one line. */
export function describeVersionPreview(p: LibraryV2AlbumVersionPreview): string {
  const before = p.track_count_before
    ? `${p.track_count_before} tracks now`
    : 'tracklist unknown now';
  const counts = `${before}, ${p.track_count_after} on this version.`;
  if (!p.owned_count) return `${counts} You don't have files for this album yet.`;
  if (!p.unmapped_count) return `${counts} All ${p.owned_count} of your files have a place on it.`;
  return `${counts} ${p.mapped_count} of your ${p.owned_count} files have a place on it, ${p.unmapped_count} don't.`;
}

function VersionGroup({
  group,
  busy,
  onPick,
}: {
  group: LibraryV2AlbumVersionGroup;
  busy: boolean;
  onPick: (v: LibraryV2AlbumVersion) => void;
}) {
  return (
    <section className={styles.matchResults} aria-label={`${group.label} versions`}>
      <div className={styles.currentMatchEyebrow}>
        {group.label}
        {group.versions.length ? ` · ${group.versions.length}` : ''}
      </div>
      {group.error ? <div className={styles.searchError}>{group.error}</div> : null}
      {!group.error && !group.versions.length ? (
        <div className={styles.inlineLoading}>{group.note || 'No other versions found here.'}</div>
      ) : null}
      {group.versions.map((v) => (
        <div
          key={`${v.source}:${v.id}`}
          className={`${styles.matchResultRow} ${v.current ? styles.matchResultCurrent : ''}`}
        >
          <div className={styles.matchResultInfo}>
            <span className={styles.matchResultHeading}>
              <span className={styles.matchResultName}>{v.title || 'Untitled'}</span>
              {v.current ? <span className={styles.matchCurrentPill}>Current</span> : null}
            </span>
            <span className={styles.matchResultExtra}>{versionFacts(v)}</span>
          </div>
          {/* #2178: the current one previews too. applying it puts its title,
              type and date on the album, and merges an album split in two */}
          <button
            type="button"
            className={styles.btnGhost}
            data-requires-write=""
            disabled={busy}
            onClick={() => onPick(v)}
          >
            Preview
          </button>
        </div>
      ))}
    </section>
  );
}

/** #2178: what a merge with one album does, in one line. */
export function describeMerge(albumTitle: string, c: LibraryV2AlbumMergeCandidate): string {
  const files = `${c.owned_count} ${c.owned_count === 1 ? 'file' : 'files'}`;
  if (c.locked)
    return `${c.title} is this version too (${files}). It's locked, unlock it to merge.`;
  return c.holds
    ? `${c.title} already is this version (${files}). Merging moves this album's tracks into it.`
    : `${c.title} is this version too (${files}). Merging moves its tracks into ${albumTitle}.`;
}

export function AlbumVersionsModal({
  albumId,
  albumTitle,
  artistName,
  onClose,
  onMerged,
}: {
  albumId: number;
  albumTitle: string;
  artistName: string;
  onClose: () => void;
  /** #2178: this album was merged into `keptId` and is gone */
  onMerged?: (keptId: number) => void;
}) {
  const queryClient = useQueryClient();
  const [query, setQuery] = useState('');
  const [searched, setSearched] = useState<string | null>(null);
  const [picked, setPicked] = useState<LibraryV2AlbumVersion | null>(null);

  // its own key, outside the library tree: a library refresh must not
  // re-ask musicbrainz for the editions
  const versions = useQuery({
    queryKey: ['library-v2-album-versions', albumId, searched],
    queryFn: () => fetchLibraryV2AlbumVersions(albumId, searched ?? undefined),
    staleTime: 10 * 60_000,
    retry: false,
  });
  const preview = useQuery({
    queryKey: ['library-v2-album-version-preview', albumId, picked?.source, picked?.id],
    queryFn: () => previewLibraryV2AlbumVersion(albumId, picked as LibraryV2AlbumVersion),
    enabled: picked !== null,
    retry: false,
  });
  const apply = useMutation({
    mutationFn: (merge?: LibraryV2AlbumMergeCandidate) =>
      applyLibraryV2AlbumVersion(albumId, picked as LibraryV2AlbumVersion, merge?.id),
    onSuccess: async (result, merge) => {
      window.showToast?.(
        merge
          ? `${albumTitle} and ${merge.title} are one album now. Rename / Organize puts the files in one folder.`
          : `${albumTitle} now follows that version. Preview Retag writes it into your files.`,
        'success',
      );
      await queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
      await queryClient.invalidateQueries({ queryKey: ['library-v2-album-versions', albumId] });
      if (result.merged_album_id === albumId) onMerged?.(result.album_id);
      onClose();
    },
  });

  const confirmMerge = async (c: LibraryV2AlbumMergeCandidate) => {
    if (!picked || c.locked) return;
    const message = [
      `Make ${albumTitle} and ${c.title} one album, as ${picked.title} (${versionFacts(picked)})?`,
      c.holds
        ? `This album's tracks move into ${c.title}.`
        : `${c.title}'s tracks move into this album.`,
      'A song both have becomes one track with both files. Your files stay where they are.',
    ].join(' ');
    const ok = window.showConfirmDialog
      ? await window.showConfirmDialog({ title: 'Merge albums', message, confirmText: 'Merge' })
      : false;
    if (ok) apply.mutate(c);
  };

  const confirmSwitch = async () => {
    if (!picked || !preview.data) return;
    const held = preview.data.held_by;
    const message = [
      `Point ${albumTitle} at ${picked.title} (${versionFacts(picked)})?`,
      'The tracklist and missing tracks follow the new version. Your files stay as they are.',
      held ? `It is the match of ${held.title || 'another album'} now, which loses it.` : '',
    ]
      .filter(Boolean)
      .join(' ');
    const ok = window.showConfirmDialog
      ? await window.showConfirmDialog({
          title: 'Switch version',
          message,
          confirmText: 'Switch',
          destructive: Boolean(held),
        })
      : false;
    if (ok) apply.mutate(undefined);
  };

  const groups = versions.data?.groups ?? [];
  const current = versions.data?.album;
  const description = picked
    ? `${picked.title} · ${versionFacts(picked)}`
    : `${albumTitle} by ${artistName}${
        current?.track_count ? `, ${current.track_count} tracks now` : ''
      }. Pick the edition your files are.`;

  const mergeWith = preview.data?.merge_with ?? [];
  const footer = picked ? (
    <div className={styles.modalActions}>
      <button
        type="button"
        className={styles.btnGhost}
        disabled={apply.isPending}
        onClick={() => setPicked(null)}
      >
        Back
      </button>
      <button
        type="button"
        className={mergeWith.length ? styles.btnGhost : styles.btnPrimary}
        data-requires-write=""
        disabled={!preview.data || apply.isPending}
        onClick={() => void confirmSwitch()}
      >
        {apply.isPending
          ? 'Switching…'
          : preview.data?.already_current
            ? "Use this version's details"
            : 'Switch to this version'}
      </button>
      {mergeWith.map((c) => (
        <button
          key={c.id}
          type="button"
          className={styles.btnPrimary}
          data-requires-write=""
          disabled={c.locked || apply.isPending}
          onClick={() => void confirmMerge(c)}
        >
          Merge with {c.title}
        </button>
      ))}
    </div>
  ) : null;

  return (
    <LibraryToolDialog
      title="Other versions"
      description={description}
      footer={footer}
      onClose={onClose}
    >
      {picked ? (
        <div className={styles.matchResults}>
          {preview.isLoading ? (
            <div className={styles.inlineLoading}>Reading that version…</div>
          ) : null}
          {preview.isError ? (
            <div className={styles.searchError}>{(preview.error as Error).message}</div>
          ) : null}
          {apply.isError ? (
            <div className={styles.searchError}>{(apply.error as Error).message}</div>
          ) : null}
          {preview.data ? (
            <>
              <div className={styles.matchResultExtra}>{describeVersionPreview(preview.data)}</div>
              {mergeWith.map((c) => (
                <div key={c.id} className={styles.matchResultExtra}>
                  {describeMerge(albumTitle, c)}
                </div>
              ))}
              {preview.data.held_by &&
              !mergeWith.some((c) => c.id === preview.data?.held_by?.id) ? (
                <div className={styles.searchError}>
                  This version is the match of {preview.data.held_by.title || 'another album'} in
                  your library. Switching moves it here.
                </div>
              ) : null}
              {preview.data.pairings.map((p, index) => (
                <div
                  key={`${p.local_title}-${index}`}
                  className={`${styles.matchResultRow} ${p.mapped ? '' : styles.matchResultCurrent}`}
                >
                  <span className={styles.matchResultExtra}>
                    {p.local_track_number ? `${p.local_track_number}. ` : ''}
                    {p.local_title}
                  </span>
                  <span className={styles.matchResultExtra}>
                    {p.mapped
                      ? `→ ${p.target_track_number ? `${p.target_track_number}. ` : ''}${p.target_title}`
                      : 'no place on this version'}
                  </span>
                </div>
              ))}
              <div className={styles.currentMatchEyebrow}>
                Tracklist · {preview.data.track_count_after}
              </div>
              {preview.data.tracks.map((t, index) => (
                <span
                  key={`${t.disc_number}-${t.track_number}-${index}`}
                  className={styles.matchResultExtra}
                >
                  {t.disc_number && t.disc_number > 1 ? `${t.disc_number}-` : ''}
                  {t.track_number ?? index + 1}. {t.title}
                </span>
              ))}
            </>
          ) : null}
        </div>
      ) : (
        <>
          <div className={styles.matchSearchRow}>
            <input
              className={styles.searchInput}
              value={query}
              placeholder={`Search ${artistName}'s releases…`}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && query.trim()) setSearched(query.trim());
              }}
            />
            <button
              type="button"
              className={styles.btnPrimary}
              disabled={!query.trim() || versions.isFetching}
              onClick={() => setSearched(query.trim())}
            >
              Search
            </button>
            {searched !== null ? (
              <button
                type="button"
                className={styles.btnGhost}
                onClick={() => {
                  setSearched(null);
                  setQuery('');
                }}
              >
                All versions
              </button>
            ) : null}
          </div>
          {versions.isLoading ? (
            <div className={styles.inlineLoading}>Looking up this album&apos;s versions…</div>
          ) : null}
          {versions.isError ? (
            <div className={styles.searchError}>{(versions.error as Error).message}</div>
          ) : null}
          {groups.map((group) => (
            <VersionGroup
              key={group.source}
              group={group}
              busy={apply.isPending}
              onPick={(v) => setPicked(v)}
            />
          ))}
        </>
      )}
    </LibraryToolDialog>
  );
}
