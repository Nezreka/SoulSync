import { useQuery } from '@tanstack/react-query';
import { Link, useNavigate } from '@tanstack/react-router';
import clsx from 'clsx';
import { type DragEvent, useEffect, useRef, useState } from 'react';

import { Button, Select, TextInput } from '@/components/form/form';
import { Notice } from '@/components/primitives';
import { browserSafeImageUrl } from '@/platform/artwork-thumb';

import type {
  ImportAlbumMatch,
  ImportAlbumMatchPayload,
  ImportAlbumResult,
  ImportInboxFile,
  ImportInboxItem,
  ImportStagingFile,
  ImportTrackResult,
} from '../-import.types';

import {
  importInboxQueryOptions,
  importSearchSourcesQueryOptions,
  matchImportAlbum,
  searchImportAlbums,
  searchImportTracks,
} from '../-import.api';
import {
  formatDuration,
  getDisplayedMatchFile,
  getEffectiveAlbumMatches,
  getImportSourceBadgeText,
  getImportSourceLabel,
  getTrackDisplayInfo,
  getUnmatchedStagingFiles,
  IMPORT_PLACEHOLDER_IMAGE,
} from '../-import.helpers';
import { describeFile, describeItemFiles, formatBitrate } from '../-import.inbox';
import styles from './import-page.module.css';
import { fallbackImage, getErrorMessage, useImportQueueActions } from './import-shared';

/**
 * The matcher for one inbox item. Picard's shape: the release on the left
 * (what it is, the other candidates, a search for when none fit), the
 * track table on the right (each release track beside the file matched to
 * it, with the file's own length and quality so a mismatch is visible
 * before the import). Singles get the same page with track candidates on
 * the left.
 */
export function Matcher({ itemKey }: { itemKey: string }) {
  const inbox = useQuery(importInboxQueryOptions());
  const item = inbox.data?.items?.find((row) => row.key === itemKey);

  if (inbox.isLoading) {
    return <div className={styles.centered}>Loading…</div>;
  }
  if (!item || !item.in_staging) {
    return (
      <div className={styles.empty}>
        <div className={styles.emptyTitle}>This item is no longer in the import folder</div>
        <div>It was imported, moved, or the folder was re-read.</div>
        <div style={{ marginTop: 12 }}>
          <Link to="/import" className={styles.matcherBack}>
            ← Back to the inbox
          </Link>
        </div>
      </div>
    );
  }
  return item.kind === 'single' ? <SingleMatcher item={item} /> : <AlbumMatcher item={item} />;
}

/** the inbox's file shape, as the album match helpers expect it */
function toStagingFile(file: ImportInboxFile): ImportStagingFile {
  return {
    filename: file.filename,
    rel_path: file.rel_path,
    full_path: file.full_path,
    title: file.title,
    artist: file.artist,
    album: file.album,
    track_number: file.track_number,
    disc_number: file.disc_number,
    extension: file.extension,
    size: file.size,
    duration_ms: file.duration_ms,
    bitrate: file.bitrate,
  };
}

function albumMetaLine(album: {
  total_tracks?: number | null;
  release_date?: string | null;
  format?: string | null;
  country?: string | null;
  label?: string | null;
  disambiguation?: string | null;
}): string {
  return [
    album.release_date?.substring(0, 4),
    album.total_tracks ? `${album.total_tracks} tracks` : '',
    album.format,
    album.country,
    album.label,
    album.disambiguation,
  ]
    .filter(Boolean)
    .join(' · ');
}

// ── album mode ──────────────────────────────────────────────────────

function AlbumMatcher({ item }: { item: ImportInboxItem }) {
  const navigate = useNavigate();
  const { addQueueJob } = useImportQueueActions();
  const sources = useQuery(importSearchSourcesQueryOptions());
  const stagingFiles = item.files.map(toStagingFile);

  const [candidates, setCandidates] = useState<ImportAlbumResult[]>([]);
  const [lookupSource, setLookupSource] = useState<string | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [query, setQuery] = useState(() => [item.artist, item.name].filter(Boolean).join(' '));
  const [sourceOverride, setSourceOverride] = useState('');

  const [selected, setSelected] = useState<ImportAlbumResult | null>(null);
  const [match, setMatch] = useState<ImportAlbumMatchPayload | null>(null);
  const [matching, setMatching] = useState(false);
  const [matchError, setMatchError] = useState<string | null>(null);
  const [overrides, setOverrides] = useState<Record<number, number>>({});
  const [dragOver, setDragOver] = useState<number | null>(null);
  const [heldChip, setHeldChip] = useState<number | null>(null);

  const search = async (text: string, override: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    setSearching(true);
    setSearchError(null);
    try {
      const payload = await searchImportAlbums(trimmed, override || undefined);
      setCandidates(payload.albums ?? []);
      setLookupSource(payload.primary_source ?? null);
      return payload.albums ?? [];
    } catch (error) {
      setSearchError(getErrorMessage(error));
      return [];
    } finally {
      setSearching(false);
    }
  };

  const pick = async (album: ImportAlbumResult) => {
    setSelected(album);
    setMatch(null);
    setMatchError(null);
    setOverrides({});
    setHeldChip(null);
    setMatching(true);
    try {
      const payload = await matchImportAlbum({
        albumId: album.id,
        source: album.source,
        albumName: album.name,
        albumArtist: album.artist,
        filePaths: item.files.map((file) => file.full_path),
      });
      setMatch(payload);
    } catch (error) {
      setMatchError(getErrorMessage(error));
    } finally {
      setMatching(false);
    }
  };

  // First open: search from what we know, and if the worker already had a
  // release in mind, open with that one so the user starts from its answer.
  const booted = useRef(false);
  useEffect(() => {
    if (booted.current) return;
    booted.current = true;
    void (async () => {
      const found = (await search(query, '')) ?? [];
      const known = item.album_id
        ? found.find((album) => String(album.id) === String(item.album_id))
        : undefined;
      const first = known ?? (item.status === 'needs_review' ? found[0] : undefined);
      if (first) void pick(first);
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const matches = match?.matches ?? [];
  const effective = getEffectiveAlbumMatches(matches, stagingFiles, overrides);
  const unmatched = getUnmatchedStagingFiles(matches, stagingFiles, overrides);
  const leftOver = stagingFiles.length - effective.length;

  const assign = (trackIndex: number, fileIndex: number) => {
    setOverrides((current) => {
      const next = { ...current };
      for (const [key, value] of Object.entries(next)) {
        if (value === fileIndex) delete next[Number(key)];
      }
      next[trackIndex] = fileIndex;
      return next;
    });
    setHeldChip(null);
  };
  const unassign = (trackIndex: number) => {
    setOverrides((current) => {
      const next = { ...current };
      delete next[trackIndex];
      if (matches[trackIndex]?.staging_file) next[trackIndex] = -1;
      return next;
    });
  };

  const runImport = () => {
    const album = match?.album;
    if (!album || effective.length === 0) return;
    addQueueJob({
      type: 'album',
      label: album.name,
      sublabel: `${album.artist} · ${effective.length} tracks`,
      imageUrl: album.image_url,
      items: effective,
      albumData: album,
      historyId: item.history_id,
    });
    void navigate({ to: '/import', search: { filter: 'all' } });
  };

  return (
    <>
      <MatcherHeader item={item} />
      <div className={styles.matcher} id="import-matcher">
        <aside className={styles.pane} aria-label="Release">
          {match?.album ? (
            <ReleaseHero album={match.album} lookupSource={lookupSource} />
          ) : selected ? (
            <ReleaseHero album={selected} lookupSource={lookupSource} />
          ) : (
            <div className={styles.heroMeta}>Pick a release, or search for one.</div>
          )}

          <div>
            <div className={styles.paneTitle} style={{ marginBottom: 8 }}>
              {selected ? 'Other candidates' : 'Candidates'}
            </div>
            {searching ? (
              <div className={styles.searchHint}>Searching…</div>
            ) : searchError ? (
              <Notice tone="danger" role="alert">
                {searchError}
              </Notice>
            ) : candidates.length === 0 ? (
              <div className={styles.searchHint}>No releases found. Try another search.</div>
            ) : (
              <div className={styles.candidates}>
                {candidates.map((album) => (
                  <button
                    key={`${album.source}-${album.id}`}
                    type="button"
                    className={clsx(styles.candidate, {
                      [styles.active]:
                        selected && selected.id === album.id && selected.source === album.source,
                    })}
                    onClick={() => void pick(album)}
                  >
                    <img
                      className={styles.candidateArt}
                      src={
                        album.image_url
                          ? browserSafeImageUrl(album.image_url)
                          : IMPORT_PLACEHOLDER_IMAGE
                      }
                      alt=""
                      loading="lazy"
                      onError={fallbackImage}
                    />
                    <span className={styles.candidateBody}>
                      <span className={styles.candidateTitle}>{album.name}</span>
                      <span className={styles.candidateMeta}>
                        {[album.artist, albumMetaLine(album)].filter(Boolean).join(' · ')}
                        {getImportSourceBadgeText(album.source, lookupSource)
                          ? ` · ${getImportSourceBadgeText(album.source, lookupSource)}`
                          : ''}
                      </span>
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>

          <div>
            <div className={styles.paneTitle} style={{ marginBottom: 8 }}>
              Search
            </div>
            <div className={styles.searchRow}>
              <TextInput
                id="import-page-album-search-input"
                placeholder="Artist and album"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') void search(query, sourceOverride);
                }}
              />
              <Button
                variant="secondary"
                disabled={searching}
                onClick={() => void search(query, sourceOverride)}
              >
                Search
              </Button>
            </div>
            {(sources.data?.sources?.length ?? 0) > 1 ? (
              <div style={{ marginTop: 8 }}>
                <Select
                  id="import-page-album-search-source"
                  size="sm"
                  aria-label="Search source"
                  value={sourceOverride}
                  onChange={(event) => setSourceOverride(event.target.value)}
                >
                  <option value="">Any source</option>
                  {sources.data!.sources!.map((source) => (
                    <option key={source.source} value={source.source}>
                      {source.label}
                    </option>
                  ))}
                </Select>
              </div>
            ) : null}
          </div>
        </aside>

        <section className={styles.pane} aria-label="Tracks">
          {matching ? (
            <div className={styles.centered}>Matching files to the tracklist…</div>
          ) : matchError ? (
            <Notice tone="danger" role="alert">
              {matchError}
            </Notice>
          ) : !match?.album ? (
            <div className={styles.centered}>
              <div className={styles.emptyTitle}>{describeItemFiles(item)}</div>
              Pick a release on the left to line the files up against its tracklist.
            </div>
          ) : (
            <>
              <div className={styles.tracks} id="import-page-match-list">
                <div className={styles.trackHead}>
                  <span>#</span>
                  <span>Track</span>
                  <span>Len</span>
                  <span>File</span>
                  <span>Match</span>
                  <span />
                </div>
                {matches.map((row, index) => (
                  <TrackRow
                    key={index}
                    index={index}
                    match={row}
                    stagingFiles={stagingFiles}
                    overrides={overrides}
                    dragOver={dragOver === index}
                    canReceive={heldChip !== null}
                    onDragOver={(over) => setDragOver(over ? index : null)}
                    onDrop={(fileIndex) => assign(index, fileIndex)}
                    onTap={() => {
                      if (heldChip !== null) assign(index, heldChip);
                    }}
                    onUnassign={() => unassign(index)}
                  />
                ))}
              </div>

              <div className={styles.pool} id="import-page-unmatched-pool">
                <div className={styles.poolTitle}>
                  <span>
                    {unmatched.length === 0
                      ? 'Every file has a track'
                      : `${unmatched.length} ${unmatched.length === 1 ? 'file' : 'files'} without a track`}
                  </span>
                  {unmatched.length > 0 ? (
                    <span className={styles.poolHint}>
                      drag onto a track, or tap a file then a track
                    </span>
                  ) : null}
                </div>
                {unmatched.length > 0 ? (
                  <div className={styles.chips}>
                    {unmatched.map(({ file, index }) => (
                      <button
                        key={file.full_path}
                        type="button"
                        className={clsx(styles.chip, { [styles.selected]: heldChip === index })}
                        draggable
                        title={file.full_path}
                        onClick={() => setHeldChip((held) => (held === index ? null : index))}
                        onDragStart={(event: DragEvent<HTMLButtonElement>) => {
                          event.dataTransfer.setData('text/plain', String(index));
                          event.dataTransfer.effectAllowed = 'move';
                        }}
                      >
                        <span>{file.filename}</span>
                        <small>{describeFile(item.files[index])}</small>
                      </button>
                    ))}
                  </div>
                ) : null}
              </div>

              <div className={styles.matcherFooter}>
                <span className={styles.footerNote}>
                  <strong>{effective.length}</strong> of {matches.length} tracks matched
                  {leftOver > 0 ? (
                    <>
                      {' '}
                      · {leftOver} {leftOver === 1 ? 'file stays' : 'files stay'} in the import
                      folder
                    </>
                  ) : null}
                </span>
                <Button
                  variant="primary"
                  id="import-page-album-process-btn"
                  disabled={effective.length === 0}
                  onClick={runImport}
                >
                  Import {effective.length} {effective.length === 1 ? 'track' : 'tracks'}
                </Button>
              </div>
            </>
          )}
        </section>
      </div>
    </>
  );
}

function MatcherHeader({ item }: { item: ImportInboxItem }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
      <Link to="/import" className={styles.matcherBack}>
        ← Inbox
      </Link>
      <span className={styles.heroMeta}>
        <code>{item.rel_path || item.folder_name}</code> · {describeItemFiles(item)}
      </span>
    </div>
  );
}

function ReleaseHero({
  album,
  lookupSource,
}: {
  album: ImportAlbumResult | NonNullable<ImportAlbumMatchPayload['album']>;
  lookupSource: string | null;
}) {
  const badge =
    getImportSourceBadgeText(album.source, lookupSource) || getImportSourceLabel(album.source);
  return (
    <div className={styles.hero} id="import-page-album-hero">
      <img
        className={styles.heroArt}
        src={album.image_url ? browserSafeImageUrl(album.image_url) : IMPORT_PLACEHOLDER_IMAGE}
        alt=""
        onError={fallbackImage}
      />
      <div className={styles.heroInfo}>
        <div className={styles.heroTitle}>{album.name}</div>
        <div className={styles.heroArtist}>{album.artist}</div>
        <div className={styles.heroMeta}>{albumMetaLine(album)}</div>
        {badge ? (
          <div>
            <span className={styles.sourceBadge}>{badge}</span>
          </div>
        ) : null}
      </div>
    </div>
  );
}

/** a file whose length is this far off the track's is flagged, not blocked */
const LENGTH_TOLERANCE_MS = 8_000;

function TrackRow({
  index,
  match,
  stagingFiles,
  overrides,
  dragOver,
  canReceive,
  onDragOver,
  onDrop,
  onTap,
  onUnassign,
}: {
  index: number;
  match: ImportAlbumMatch;
  stagingFiles: ImportStagingFile[];
  overrides: Record<number, number>;
  dragOver: boolean;
  canReceive: boolean;
  onDragOver: (over: boolean) => void;
  onDrop: (fileIndex: number) => void;
  onTap: () => void;
  onUnassign: () => void;
}) {
  const info = getTrackDisplayInfo(match, index);
  const { file, confidence } = getDisplayedMatchFile(match, index, stagingFiles, overrides);
  const percent = Math.round(confidence * 100);
  const trackLen = info.track.duration_ms ?? 0;
  const fileLen = file?.duration_ms ?? 0;
  const lengthOff = Boolean(
    file && trackLen && fileLen && Math.abs(trackLen - fileLen) > LENGTH_TOLERANCE_MS,
  );
  const tone = percent >= 90 ? 'high' : percent >= 70 ? 'medium' : 'low';

  return (
    <div
      className={clsx(styles.track, {
        [styles.matched]: Boolean(file),
        [styles.mismatch]: lengthOff,
        [styles.dropTarget]: dragOver,
        [styles.canReceive]: canReceive,
      })}
      onClick={onTap}
      onDragOver={(event) => {
        event.preventDefault();
        event.dataTransfer.dropEffect = 'move';
        onDragOver(true);
      }}
      onDragLeave={() => onDragOver(false)}
      onDrop={(event) => {
        event.preventDefault();
        onDragOver(false);
        const fileIndex = Number(event.dataTransfer.getData('text/plain'));
        if (Number.isFinite(fileIndex)) onDrop(fileIndex);
      }}
    >
      <span className={styles.trackNum}>{info.displayTrackNumber}</span>
      <span className={styles.trackName} title={info.name}>
        {info.name}
      </span>
      <span className={styles.trackLen}>{formatDuration(trackLen)}</span>
      <span className={styles.trackFile}>
        {file ? (
          <>
            <span className={styles.trackFileName} title={file.full_path}>
              {file.filename}
            </span>
            <span className={styles.trackFileFacts}>
              {[
                file.extension?.replace('.', '').toUpperCase(),
                fileLen ? (
                  <span key="len" className={lengthOff ? styles.warn : undefined}>
                    {formatDuration(fileLen)}
                    {lengthOff ? ' (length differs)' : ''}
                  </span>
                ) : null,
                formatBitrate(file.bitrate),
              ]
                .filter(Boolean)
                .map((part, i) => (
                  <span key={i}>
                    {i > 0 ? ' · ' : ''}
                    {part}
                  </span>
                ))}
            </span>
          </>
        ) : (
          <span className={styles.trackSlot}>{canReceive ? 'tap to place here' : 'no file'}</span>
        )}
      </span>
      <span className={styles.trackConf}>
        {file ? (
          <>
            <span className={styles.confidenceBar}>
              <span
                className={styles.confidenceFill}
                data-tone={tone}
                style={{ width: `${percent}%`, display: 'block' }}
              />
            </span>
            {percent}%
          </>
        ) : null}
      </span>
      <span>
        {file ? (
          <button
            type="button"
            className={styles.unassign}
            title="Take this file off the track"
            aria-label={`Unassign ${file.filename}`}
            onClick={(event) => {
              event.stopPropagation();
              onUnassign();
            }}
          >
            ×
          </button>
        ) : null}
      </span>
    </div>
  );
}

// ── single mode ─────────────────────────────────────────────────────

function SingleMatcher({ item }: { item: ImportInboxItem }) {
  const navigate = useNavigate();
  const { addQueueJob } = useImportQueueActions();
  const file = item.files[0];
  const [query, setQuery] = useState(
    () =>
      [file?.artist, file?.title].filter(Boolean).join(' - ') ||
      (file?.filename || '').replace(/\.[^.]+$/, ''),
  );
  const [results, setResults] = useState<ImportTrackResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [chosen, setChosen] = useState<ImportTrackResult | null>(null);

  const search = async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    setSearching(true);
    setError(null);
    try {
      const payload = await searchImportTracks(trimmed);
      setResults(payload.tracks ?? []);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setSearching(false);
    }
  };

  const booted = useRef(false);
  useEffect(() => {
    if (booted.current) return;
    booted.current = true;
    void search(query);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const runImport = () => {
    if (!file) return;
    const staging = toStagingFile(file);
    addQueueJob({
      type: 'singles',
      label: chosen ? `${chosen.name} · ${chosen.artist}` : file.title || file.filename,
      sublabel: file.filename,
      imageUrl: chosen?.image_url ?? null,
      items: [chosen ? { ...staging, manual_match: chosen } : staging],
      historyId: item.history_id,
    });
    void navigate({ to: '/import', search: { filter: 'all' } });
  };

  return (
    <>
      <MatcherHeader item={item} />
      <div className={styles.matcher} id="import-matcher">
        <aside className={styles.pane} aria-label="Track">
          <div className={styles.paneTitle}>Which track is this?</div>
          <div className={styles.searchRow}>
            <TextInput
              placeholder="Artist - title"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') void search(query);
              }}
            />
            <Button variant="secondary" disabled={searching} onClick={() => void search(query)}>
              Search
            </Button>
          </div>
          {searching ? (
            <div className={styles.searchHint}>Searching…</div>
          ) : error ? (
            <Notice tone="danger" role="alert">
              {error}
            </Notice>
          ) : results.length === 0 ? (
            <div className={styles.searchHint}>No tracks found. Try another search.</div>
          ) : (
            <div className={styles.candidates}>
              {results.map((track, index) => (
                <button
                  key={`${track.source}-${track.id}-${index}`}
                  type="button"
                  className={clsx(styles.trackResult, {
                    [styles.active]: chosen?.id === track.id && chosen?.source === track.source,
                  })}
                  onClick={() => setChosen(track)}
                >
                  <img
                    className={styles.candidateArt}
                    src={
                      track.image_url
                        ? browserSafeImageUrl(track.image_url)
                        : IMPORT_PLACEHOLDER_IMAGE
                    }
                    alt=""
                    loading="lazy"
                    onError={fallbackImage}
                  />
                  <span className={styles.candidateBody}>
                    <span className={styles.candidateTitle}>
                      {track.name} · {track.artist}
                    </span>
                    <span className={styles.candidateMeta}>
                      {[
                        track.album,
                        formatDuration(track.duration_ms),
                        getImportSourceLabel(track.source),
                      ]
                        .filter(Boolean)
                        .join(' · ')}
                    </span>
                  </span>
                  <span className={styles.trackLen}>{chosen?.id === track.id ? '✓' : ''}</span>
                </button>
              ))}
            </div>
          )}
        </aside>

        <section className={styles.pane} aria-label="File">
          <div className={styles.paneTitle}>The file</div>
          {file ? (
            <div className={styles.hero}>
              <div className={styles.heroInfo}>
                <div className={styles.heroTitle}>{file.title || file.filename}</div>
                {file.artist ? <div className={styles.heroArtist}>{file.artist}</div> : null}
                <div className={styles.heroMeta}>
                  <code>{file.rel_path}</code>
                  <br />
                  {describeFile(file)}
                  {file.album ? ` · album tag: ${file.album}` : ''}
                </div>
              </div>
            </div>
          ) : null}
          <div className={styles.matcherFooter}>
            <span className={styles.footerNote}>
              {chosen ? (
                <>
                  Tagged as <strong>{chosen.name}</strong> by {chosen.artist}
                  {chosen.duration_ms &&
                  file?.duration_ms &&
                  Math.abs(chosen.duration_ms - file.duration_ms) > LENGTH_TOLERANCE_MS
                    ? ' · length differs from the file'
                    : ''}
                </>
              ) : (
                'No track picked: imported from its own tags'
              )}
            </span>
            <Button variant="primary" id="import-page-singles-process-btn" onClick={runImport}>
              Import
            </Button>
          </div>
        </section>
      </div>
    </>
  );
}
