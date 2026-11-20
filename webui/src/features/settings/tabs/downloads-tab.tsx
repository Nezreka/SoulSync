/**
 * The Downloads tab of Settings, ported from index.html and settings.js
 * (SETTINGS_PORT.md, step 5). Same markup, classes and ids. Three slots:
 *  - part "behaviour": Download Behaviour, inside the vanilla
 *    #dlchain-behaviour wrapper the download chain hides under the video and
 *    audiobook chains
 *  - part "retry": Retry Logic and Album Publishing
 *  - part "filtering": Filtering, with the genre whitelist
 *
 * Source Settings (the download chain) is still vanilla: it moves with the
 * Sources tab, its other half.
 */

import { type ReactNode, useEffect, useState, useSyncExternalStore } from 'react';

import type { SettingsTabProps } from '../host';
import type { SettingsStore } from '../settings-store';

import {
  CONCURRENCY,
  PREFERRED_VERSIONS,
  STREAM_SOURCES,
  addGenre,
  filteringSummary,
  resolveDownloads,
  toFloat,
  toInt,
} from './downloads';
import { Section, helpBits, useDraft, useHelp } from './parts';
import { sourcesModel } from './sources-model';
import { ChainWidget } from './sources-tab';

const TAB = 'downloads';

function useValues(store: SettingsStore) {
  const state = useSyncExternalStore(store.subscribe, store.getState);
  return { state, values: resolveDownloads(state.settings) };
}

/** a number field that shows what is typed and resets on a fresh load */
function useNumber(store: SettingsStore, value: string, save: (raw: string) => void) {
  const draft = useDraft(value, save);
  const loads = useSyncExternalStore(store.subscribe, () => store.getState().loads);
  useEffect(() => draft.reset(), [loads]); // eslint-disable-line react-hooks/exhaustive-deps
  return draft;
}

/** a select or input with its help inside the form-group (setting-help-text) */
function Field({ label, control, help }: { label: string; control: ReactNode; help: ReactNode }) {
  const state = useHelp();
  const bits = helpBits(help, state);
  return (
    <div className="form-group">
      <label>{label}</label>
      {control}
      {bits.icon}
      <div className="setting-help-text setting-help-body" hidden={!state.open}>
        {help}
      </div>
      {bits.line}
    </div>
  );
}

/** a setting-row: the control and its "i" on one line, the help under it */
function Row({
  id,
  rowStyle,
  control,
  help,
}: {
  id?: string;
  rowStyle?: Record<string, string>;
  control: ReactNode;
  help: ReactNode;
}) {
  const state = useHelp();
  const bits = helpBits(help, state);
  return (
    <div className="form-group" id={id} style={rowStyle}>
      <div className="setting-row">
        {control}
        {bits.icon}
      </div>
      <div className="setting-help-body" hidden={!state.open}>
        {help}
      </div>
      {bits.line}
    </div>
  );
}

/** a tick box with its help after the form-group */
function Check({
  id,
  label,
  help,
  checked,
  onChange,
  disabled,
  indent,
  bodyStyle,
}: {
  id: string;
  label: string;
  help: ReactNode;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  indent?: boolean;
  bodyStyle?: Record<string, string>;
}) {
  const state = useHelp();
  const bits = helpBits(help, state);
  const margin = indent ? { marginLeft: '26px' } : undefined;
  return (
    <>
      <div className="form-group" style={margin}>
        <label className="checkbox-label" style={disabled ? { opacity: '0.45' } : undefined}>
          <input
            type="checkbox"
            id={id}
            checked={checked}
            disabled={disabled}
            onChange={(event) => onChange(event.target.checked)}
          />
          {label}
        </label>
        {bits.icon}
        {bits.line}
      </div>
      <div
        className="help-text setting-help-body"
        style={{ ...margin, ...bodyStyle }}
        hidden={!state.open}
      >
        {help}
      </div>
    </>
  );
}

function Behaviour({ store }: SettingsTabProps) {
  const { values } = useValues(store);
  const timeout = useNumber(store, values.searchTimeout, (raw) =>
    store.set('download_source', 'source_search_timeout', toInt(raw, 0)),
  );
  const kind = useSyncExternalStore(sourcesModel.subscribe, () => sourcesModel.getState().kind);
  const testing = useSyncExternalStore(
    sourcesModel.subscribe,
    () => sourcesModel.getState().testingAll,
  );
  // these are download_source.*, music-wide: the wrapper (header and all) is
  // hidden under the video and audiobook chains, as renderDownloadChain did
  return (
    <div id="dlchain-behaviour" data-stg={TAB} hidden={kind !== 'music'}>
      <Section tab={TAB} title="Download Behaviour" hint="Preview source, concurrency & timeouts">
        <div className="settings-group dlchain-behaviour" data-stg={TAB}>
          <div id="dlchain-extra" className="dlchain-extra">
            <Field
              label="Stream / Preview Source:"
              control={
                <select
                  id="stream-source"
                  className="form-select"
                  value={values.streamSource}
                  onChange={(event) =>
                    store.set('download_source', 'stream_source', event.target.value)
                  }
                >
                  {STREAM_SOURCES.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
              }
              help={
                <>
                  Where to stream audio when previewing tracks. YouTube is instant and requires no
                  setup. "Active Download Source" uses your configured source (Tidal, Qobuz, etc.) —
                  if that's Soulseek, YouTube is used automatically. "Deezer 30s Preview" plays
                  Deezer's 30 second clip of the song straight away, no Deezer account needed. Songs
                  Deezer has no clip for use YouTube. With queue auto-download off, missing queue
                  songs play their clip instead of being skipped.
                </>
              }
            />
            <Field
              label="Concurrent Downloads:"
              control={
                <select
                  id="max-concurrent-downloads"
                  className="form-select"
                  value={values.maxConcurrent}
                  onChange={(event) =>
                    store.set(
                      'download_source',
                      'max_concurrent',
                      parseInt(event.target.value, 10) || 3,
                    )
                  }
                >
                  {CONCURRENCY.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
              }
              help={
                <>
                  Maximum simultaneous downloads per batch. Soulseek album downloads always use 1
                  worker due to per-user upload limits. Higher values speed up large playlists and
                  wishlists. Takes effect after a restart, since the worker threads are created at
                  startup.
                </>
              }
            />
            <Field
              label="Source search timeout (seconds):"
              control={
                <input
                  type="number"
                  id="source-search-timeout"
                  min="0"
                  max="120"
                  step="1"
                  placeholder="0 = source defaults"
                  style={{ maxWidth: '180px' }}
                  value={timeout.value}
                  onChange={(event) => timeout.onChange(event.target.value)}
                />
              }
              help={
                <>
                  How long to wait on a streaming source's API during search (HiFi, Qobuz, Deezer,
                  and the stream preview search).{' '}
                  <strong>0 (or blank) = each source's built-in default</strong> — HiFi/Qobuz 15s,
                  Deezer 10s. Raise it if a slow connection makes searches give up too early; lower
                  means failing over to the next source faster. Clamped to 5&ndash;120. Soulseek is
                  not affected — it has its own Search Timeout setting above.
                </>
              }
            />
            <div className="form-group">
              <button
                className="test-button"
                id="test-all-sources-btn"
                disabled={testing}
                // fills the source tiles' status dots (sources-model.ts)
                onClick={() => void sourcesModel.testAll()}
              >
                {testing ? 'Testing…' : 'Test all sources'}
              </button>
              <span className="setting-help-text" style={{ marginLeft: '10px' }}>
                Checks every selected source &amp; configured connection.
              </span>
            </div>
          </div>
        </div>
      </Section>
    </div>
  );
}

function Retry({ store }: SettingsTabProps) {
  const { values } = useValues(store);
  const set = (key: string) => (value: unknown) => store.set('post_processing', key, value);
  const retries = useNumber(store, values.retriesPerQuery, (raw) =>
    store.set('post_processing', 'retries_per_query', Math.max(1, parseInt(raw, 10) || 5)),
  );
  const minCount = useNumber(store, values.versionMinCount, (raw) =>
    store.set('post_processing', 'version_mismatch_min_count', Math.max(1, parseInt(raw, 10) || 2)),
  );
  const rejectDays = useNumber(store, values.reviewRejectDays, (raw) =>
    store.set('post_processing', 'review_auto_reject_days', Math.max(0, parseInt(raw, 10) || 0)),
  );
  const check = (id: string, label: string, checked: boolean, onChange: (v: boolean) => void) => (
    <label className="checkbox-label">
      <input
        type="checkbox"
        id={id}
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
      />
      {label}
    </label>
  );
  const number = (
    id: string,
    label: string,
    min: string,
    draft: { value: string; onChange: (raw: string) => void },
  ) => (
    <>
      <label htmlFor={id}>{label}</label>
      <input
        type="number"
        id={id}
        min={min}
        max="20"
        step="1"
        style={{ width: '100px' }}
        autoComplete="off"
        data-bwignore=""
        data-1p-ignore=""
        data-lpignore="true"
        value={draft.value}
        onChange={(event) => draft.onChange(event.target.value)}
      />
    </>
  );
  return (
    <>
      <Section tab={TAB} title="Retry Logic" hint="What happens when a download is rejected">
        <div className="settings-group" data-stg={TAB}>
          <Row
            control={check(
              'retry-next-candidate',
              'Retry next-best candidate on mismatch',
              values.retryNext,
              set('retry_next_candidate_on_mismatch'),
            )}
            help={
              <>
                Controls what happens when a downloaded file is rejected (AcoustID mismatch, wrong
                version, or duration/integrity failure) — independent of post-processing. When on,
                SoulSync automatically tries the next-best candidate instead of failing the track.{' '}
                <strong>Off</strong> = quarantine and fail immediately.
              </>
            }
          />
          <Row
            control={check(
              'retry-exhaustive',
              'Exhaustive retry (separate budget per source)',
              values.retryExhaustive,
              set('retry_exhaustive'),
            )}
            help={
              <>
                Give every source (Soulseek, then HiFi/Tidal/…) its own retry budget. Each source
                spends <em>queries × retries-per-query</em> attempts before the track moves on.
                Worst case across two sources can mean many downloads — use for hard-to-match tracks
                (e.g. CJK artist names). Requires the option above.
              </>
            }
          />
          <Row
            id="retries-per-query-row"
            rowStyle={values.retryExhaustive ? {} : { display: 'none' }}
            control={number('retries-per-query', 'Retries per query (per source)', '1', retries)}
            help={
              <>
                In exhaustive mode, how many candidates to try per search query per source. The
                per-source budget is <em>number of search queries × this value</em>. Only used when
                Exhaustive retry is on.
              </>
            }
          />
          <Row
            control={check(
              'accept-version-mismatch-fallback',
              'Accept best version mismatch as last resort',
              values.versionFallback,
              set('accept_version_mismatch_fallback'),
            )}
            help={
              <>
                When retries are fully exhausted and <em>every</em> candidate for a track failed the{' '}
                <strong>same</strong> version mismatch (e.g. only an instrumental exists), accept
                the best (first-tried) one instead of leaving the track missing. Only AcoustID is
                bypassed — integrity/duration/bit-depth checks still run, so truncated or genuinely
                wrong files are never let through. <strong>Off</strong> = leave such tracks failed.
              </>
            }
          />
          <Row
            id="version-mismatch-min-count-row"
            rowStyle={values.versionFallback ? {} : { display: 'none' }}
            control={number(
              'version-mismatch-min-count',
              'Minimum matching mismatches before accepting',
              '1',
              minCount,
            )}
            help={
              <>
                How many quarantined candidates must have failed the <em>same</em> version mismatch
                before the last-resort acceptance kicks in. Higher = more confirmation that no
                correct version exists. Only used when the option above is on.
              </>
            }
          />
          <Row
            control={check(
              'clear-near-misses-on-success',
              'Clear near-misses once the track downloads',
              values.clearNearMisses,
              set('clear_near_misses_on_success'),
            )}
            help={
              <>
                When a rejected file is followed by a candidate that imports fine, the rejected
                files leave Review on their own. They go to the Downloads recycle bin, so you can
                restore one to Review. Automatic quality upgrades never send anything to Review
                either way: if nothing verifies, the file you already have stays.
              </>
            }
          />
          <Row
            control={
              <>
                <label htmlFor="review-auto-reject-days">
                  Auto-reject Review items older than (days)
                </label>
                <input
                  type="number"
                  id="review-auto-reject-days"
                  min="0"
                  max="365"
                  step="1"
                  style={{ width: '100px' }}
                  autoComplete="off"
                  data-bwignore=""
                  data-1p-ignore=""
                  data-lpignore="true"
                  value={rejectDays.value}
                  onChange={(event) => rejectDays.onChange(event.target.value)}
                />
              </>
            }
            help={
              <>
                Quarantined files nobody has looked at after this many days move to the Downloads
                recycle bin, where you can still restore them. <strong>0</strong> keeps them until
                you decide.
              </>
            }
          />
        </div>
      </Section>

      <Section
        tab={TAB}
        title="Album Publishing"
        hint="When album downloads appear in your library"
      >
        <div className="settings-group" data-stg={TAB}>
          <Row
            control={check(
              'album-atomic-publish',
              'Publish albums only when complete',
              values.atomicPublish,
              (v) => store.set('album_downloads', 'atomic_publish', v),
            )}
            help={
              <>
                <strong>Off by default</strong> — with this off, tracks appear in your library as
                they finish downloading (unchanged behavior). When <strong>on</strong>, the tracks
                of an album download are staged privately and only moved into your media library
                once the <strong>whole album</strong> completes, so Plex/Jellyfin/Navidrome never
                shows a half-loaded album while it's still downloading. If a batch stalls or fails,
                its tracks stay in staging (never your library) and the failed tracks remain
                retryable in your wishlist. That private staging area is SoulSync's own working
                folder, <code>storage/album_bundle_staging/&lt;batch id&gt;</code> — it is{' '}
                <strong>not</strong> the Staging path set under Library, which is a different folder
                for a different job. It is emptied when the batch finishes. If a crash leaves one
                behind, the next start moves anything still holding audio into the Downloads recycle
                bin (labelled &ldquo;Stalled album download&rdquo;) rather than deleting it, so a
                stalled album is recoverable instead of gone. Only affects full-album and
                wishlist-album downloads — single tracks and album top-ups (completeness fills) are
                unaffected. Tracks stay on your wishlist until the album is actually published, so
                an interrupted album is always retryable.
              </>
            }
          />
          <Row
            control={check(
              'album-atomic-recover',
              'Recover interrupted albums on startup',
              values.atomicRecover,
              (v) => store.set('album_downloads', 'atomic_recover_orphans', v),
            )}
            help={
              <>
                <strong>On by default</strong>, and only relevant when album publishing above is on.
                If SoulSync restarts or crashes part-way through an album, the tracks it had already
                finished are left in the private staging folder, where no media server can see them.
                On the next start SoulSync moves those finished tracks into your library, and the
                tracks it never got to are still on your wishlist, so the album fills itself in.
                Turn this <strong>off</strong> to keep strict quarantine instead: the interrupted
                album stays hidden until you deal with it by hand. Files are never deleted either
                way.
              </>
            }
          />
        </div>
      </Section>
    </>
  );
}

function GenreWhitelist({
  store,
  enabled,
  genres,
}: {
  store: SettingsStore;
  enabled: boolean;
  genres: string[];
}) {
  const [search, setSearch] = useState('');
  const save = (next: string[]) => store.set('genre_whitelist', 'genres', next);
  const needle = search.toLowerCase();
  const shown = needle ? genres.filter((g) => g.toLowerCase().includes(needle)) : genres;
  return (
    <div id="genre-whitelist-container" style={{ display: enabled ? '' : 'none' }}>
      <div className="form-group" style={{ marginBottom: '8px' }}>
        <input
          type="text"
          id="genre-whitelist-search"
          placeholder="Search or add genre..."
          className="form-input"
          style={{ marginBottom: '8px' }}
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          onKeyDown={(event) => {
            if (event.key !== 'Enter') return;
            event.preventDefault();
            const next = addGenre(genres, search);
            if (next !== genres) save(next);
            setSearch('');
          }}
        />
      </div>
      <div id="genre-whitelist-chips" className="genre-whitelist-chips">
        {!enabled ? null : genres.length === 0 ? (
          <div style={{ color: 'rgba(255,255,255,0.3)', fontSize: '13px', padding: '4px 0' }}>
            No genres configured. Click "Reset to Defaults" to load the default whitelist.
          </div>
        ) : (
          shown.map((g) => (
            <span className="genre-chip" key={g}>
              {g}
              <button
                className="genre-chip-x"
                data-genre={g}
                onClick={(event) => {
                  event.preventDefault();
                  save(genres.filter((x) => x !== g));
                }}
              >
                &times;
              </button>
            </span>
          ))
        )}
      </div>
      <div className="form-actions" style={{ marginTop: '8px', gap: '8px' }}>
        <button className="test-button" onClick={() => void resetGenres(store)}>
          Reset to Defaults
        </button>
        <span
          id="genre-whitelist-count"
          style={{ color: 'rgba(255,255,255,0.4)', fontSize: '12px' }}
        >
          {enabled && genres.length ? `${genres.length} genres` : ''}
        </span>
      </div>
    </div>
  );
}

export async function resetGenres(store: SettingsStore) {
  try {
    const data = (await (await fetch('/api/genre-whitelist/defaults')).json()) as {
      genres?: string[];
    };
    if (data.genres) {
      store.set('genre_whitelist', 'genres', data.genres);
      window.showToast?.(`Loaded ${data.genres.length} default genres`, 'success');
    }
  } catch {
    window.showToast?.('Failed to load defaults', 'error');
  }
}

function Filtering({ store }: SettingsTabProps) {
  const { state, values } = useValues(store);
  const maxMb = useNumber(store, values.maxMbPerMinute, (raw) =>
    store.set('download_source', 'max_mb_per_minute', Math.max(0, toFloat(raw, 0))),
  );
  const versionHelp = useHelp();
  const versionBits = helpBits(
    "Spotify often only carries one cut of a track. With a version picked here, a matching file wins over the plain one when both turn up in the same Soulseek search. Nothing is ever skipped — if that version isn't out there, the normal one downloads exactly as it does now. Off by default. Live, remix, acoustic and instrumental files are normally thrown out on sight when the source didn't ask for one. Picking any of those four here lifts that for the version you picked, so a live take of a similarly titled song can be accepted when the real track is nowhere to be found.",
    versionHelp,
  );
  const blocked = !values.allowExplicit;
  return (
    <Section
      tab={TAB}
      title="Filtering"
      hint="Explicit content & genre whitelist"
      summaryKey="library-filtering"
      summary={state.loaded ? filteringSummary(values) : ''}
    >
      <div className="settings-group" data-stg={TAB}>
        <Check
          id="allow-explicit"
          label="Allow explicit content in downloads"
          checked={values.allowExplicit}
          onChange={(v) => store.set('content_filter', 'allow_explicit', v)}
          help="When disabled, tracks marked as explicit on Spotify will be skipped during matched downloads. Does not affect manual Soulseek searches."
        />
        {/* #923: only meaningful while explicit content is allowed */}
        <Check
          id="prefer-explicit"
          label="Prefer explicit versions when matching"
          checked={values.preferExplicit}
          disabled={blocked}
          indent
          onChange={(v) => store.set('content_filter', 'prefer_explicit', v)}
          help={
            <>
              When picking between Soulseek results, files marked Explicit/Uncensored rank higher
              and Clean/Censored/Radio-Edit files rank lower. Nothing is ever skipped &mdash; if
              only a clean version exists, it still downloads. Requires explicit content to be
              allowed.
            </>
          }
        />
        {/* #2081: the hard version of the preference above */}
        <Check
          id="exclude-clean"
          label="Never download clean versions"
          checked={values.excludeClean}
          disabled={blocked}
          indent
          onChange={(v) => store.set('content_filter', 'exclude_clean', v)}
          help="Files marked Clean, Censored, Edited or Radio Edit are never picked for a track that is not that cut itself. If only a clean version exists, the track waits on your Wishlist until a real one turns up. Requires explicit content to be allowed."
        />
        <div className="form-group">
          <label htmlFor="music-max-mb-per-minute">Maximum music download size (MB/min)</label>
          <input
            type="number"
            id="music-max-mb-per-minute"
            min="0"
            step="0.1"
            value={maxMb.value}
            onChange={(event) => maxMb.onChange(event.target.value)}
          />
          <div className="help-text">
            0 disables the limit. At 10 MB/min, a 3:30 track may be up to 35 MB (1 MB = 1,000,000
            bytes). Oversized candidates are skipped before download. Uses the advertised duration,
            or the requested track length when unavailable. Album releases use the album length.
            Sources without size or duration information cannot be checked. This does not resize
            existing files or limit post-processing output.
          </div>
        </div>
        <div className="form-group">
          <label htmlFor="preferred-version">Prefer a version when one exists</label>
          <select
            id="preferred-version"
            value={values.preferredVersion}
            onChange={(event) => store.set('soulseek', 'preferred_version', event.target.value)}
          >
            {PREFERRED_VERSIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          {versionBits.icon}
          {versionBits.line}
        </div>
        <div className="help-text setting-help-body" hidden={!versionHelp.open}>
          Spotify often only carries one cut of a track. With a version picked here, a matching file
          wins over the plain one when both turn up in the same Soulseek search. Nothing is ever
          skipped &mdash; if that version isn't out there, the normal one downloads exactly as it
          does now. Off by default.
          <br />
          <br />
          Live, remix, acoustic and instrumental files are normally thrown out on sight when the
          source didn't ask for one. Picking any of those four here lifts that for the version you
          picked, so a live take of a similarly titled song can be accepted when the real track is
          nowhere to be found.
        </div>
        <Check
          id="genre-whitelist-enabled"
          label="Enable strict genre filtering"
          checked={values.genreWhitelist}
          bodyStyle={{ marginBottom: '12px' }}
          onChange={(v) => {
            store.set('genre_whitelist', 'enabled', v);
            // the first time it is switched on with nothing in it, load the defaults
            if (v && values.genres.length === 0) void resetGenres(store);
          }}
          help="When enabled, only genres on the whitelist below are kept during enrichment. Junk tags (artist names, radio shows, playlist names) are silently dropped. When disabled, all genres pass through unchanged."
        />
        <GenreWhitelist store={store} enabled={values.genreWhitelist} genres={values.genres} />
      </div>
    </Section>
  );
}

/** Source Settings: the download chain (step 7a) */
function Chain({ store, opened, active }: SettingsTabProps) {
  const loads = useSyncExternalStore(store.subscribe, () => store.getState().loads);
  return <ChainWidget opened={opened} active={active} loads={loads} />;
}

export function DownloadsTab(props: SettingsTabProps) {
  if (props.part === 'chain') return <Chain {...props} />;
  if (props.part === 'behaviour') return <Behaviour {...props} />;
  if (props.part === 'filtering') return <Filtering {...props} />;
  return <Retry {...props} />;
}
