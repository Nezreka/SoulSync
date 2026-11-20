/**
 * Downloads > Behaviour: how downloads run once a source is picked. The
 * music settings (stream source, concurrency, retries, album publishing) and
 * the video side's one rule, each behind the kind switch. Same keys, ids and
 * saves as the old Downloads tab's sections (tabs/downloads.ts).
 */

import { useEffect, useSyncExternalStore } from 'react';

import type { SettingsStore } from '../../settings-store';

import { Card, NumberField, Row, Select, Switch } from '../../ui';
import { CONCURRENCY, STREAM_SOURCES, resolveDownloads, toInt } from '../downloads';
import { WishlistRules } from '../library-organization';
import { YoutubeFollowCount } from '../library-video-organization';
import { MonitorSpecialsRow } from '../library-video-prefs';
import { useDraft } from '../parts';
import { useVideoDownloads, videoDownloads } from '../video-downloads';

export function useValues(store: SettingsStore) {
  const state = useSyncExternalStore(store.subscribe, store.getState);
  return { state, values: resolveDownloads(state.settings) };
}

/** a number field that shows what is typed and resets on a fresh load */
export function useNumber(store: SettingsStore, value: string, save: (raw: string) => void) {
  const draft = useDraft(value, save);
  const loads = useSyncExternalStore(store.subscribe, () => store.getState().loads);
  useEffect(() => draft.reset(), [loads]); // eslint-disable-line react-hooks/exhaustive-deps
  return draft;
}

/** true while this key waits to be saved: the row shows its dot */
export function useChanged(store: SettingsStore, section: string, ...keys: string[]) {
  return useSyncExternalStore(store.subscribe, () => {
    const pending = store.getState().pending[section];
    return !!pending && keys.some((key) => key in pending);
  });
}

export function MusicBehaviour({ store }: { store: SettingsStore }) {
  const { values } = useValues(store);
  const timeout = useNumber(store, values.searchTimeout, (raw) =>
    store.set('download_source', 'source_search_timeout', toInt(raw, 0)),
  );
  const retries = useNumber(store, values.retriesPerQuery, (raw) =>
    store.set('post_processing', 'retries_per_query', Math.max(1, parseInt(raw, 10) || 5)),
  );
  const minCount = useNumber(store, values.versionMinCount, (raw) =>
    store.set('post_processing', 'version_mismatch_min_count', Math.max(1, parseInt(raw, 10) || 2)),
  );
  const pp = (key: string) => (value: unknown) => store.set('post_processing', key, value);
  const ds = (...keys: string[]) => useChanged(store, 'download_source', ...keys); // eslint-disable-line react-hooks/rules-of-hooks
  const ppc = (...keys: string[]) => useChanged(store, 'post_processing', ...keys); // eslint-disable-line react-hooks/rules-of-hooks
  const ad = (...keys: string[]) => useChanged(store, 'album_downloads', ...keys); // eslint-disable-line react-hooks/rules-of-hooks

  return (
    <>
      <Card
        title="Download behaviour"
        hint="Preview source, how many at once, and how long a search may take"
      >
        <Row
          field
          label="Stream and preview source"
          htmlFor="stream-source"
          changed={ds('stream_source')}
          help={
            'Where audio plays from when you preview a track. YouTube is instant and needs no setup. "Active Download Source" uses your first download source (Tidal, Qobuz and so on); if that is Soulseek, YouTube is used. "Deezer 30s Preview" plays Deezer\'s 30 second clip straight away, no Deezer account needed, and falls back to YouTube for songs Deezer has no clip for. With queue auto-download off, missing queue songs play their clip instead of being skipped.'
          }
        >
          <Select
            id="stream-source"
            value={values.streamSource}
            options={STREAM_SOURCES.map((o) => ({ ...o }))}
            onChange={(event) => store.set('download_source', 'stream_source', event.target.value)}
          />
        </Row>
        <Row
          field
          label="Concurrent downloads"
          htmlFor="max-concurrent-downloads"
          changed={ds('max_concurrent')}
          help="How many downloads run at once in a batch. Soulseek album downloads always use 1 worker because of per-user upload limits. Higher values speed up large playlists and wishlists. Takes effect after a restart, since the workers are created at startup."
        >
          <Select
            id="max-concurrent-downloads"
            value={values.maxConcurrent}
            options={CONCURRENCY.map((o) => ({ ...o }))}
            onChange={(event) =>
              store.set('download_source', 'max_concurrent', parseInt(event.target.value, 10) || 3)
            }
          />
        </Row>
        <Row
          field
          label="Source search timeout"
          htmlFor="source-search-timeout"
          changed={ds('source_search_timeout')}
          help={
            <>
              How long to wait on a streaming source&apos;s search (HiFi, Qobuz, Deezer, and the
              stream preview search).
            </>
          }
          more={
            <>
              <strong>0 or blank uses each source&apos;s own default</strong>: HiFi and Qobuz 15
              seconds, Deezer 10. Raise it if a slow connection makes searches give up too early;
              lower fails over to the next source faster. Kept between 5 and 120. Soulseek is not
              affected: it has its own search timeout in its settings.
            </>
          }
        >
          <NumberField
            id="source-search-timeout"
            min="0"
            max="120"
            step="1"
            placeholder="0"
            unit="seconds"
            value={timeout.value}
            onChange={(event) => timeout.onChange(event.target.value)}
          />
        </Row>
      </Card>

      <Card title="Retry logic" hint="What happens when a download is rejected">
        <Row
          label="Retry next-best candidate on mismatch"
          htmlFor="retry-next-candidate"
          changed={ppc('retry_next_candidate_on_mismatch')}
          help="When a downloaded file is rejected, try the next-best candidate instead of failing the track."
          more={
            <>
              A file is rejected on an AcoustID mismatch, a wrong version, or a duration or
              integrity failure, whatever post-processing is set to. <strong>Off</strong> means
              quarantine it and fail the track at once.
            </>
          }
        >
          <Switch
            id="retry-next-candidate"
            checked={values.retryNext}
            onChange={(event) => pp('retry_next_candidate_on_mismatch')(event.target.checked)}
          />
        </Row>
        <Row
          label="Exhaustive retry (separate budget per source)"
          htmlFor="retry-exhaustive"
          changed={ppc('retry_exhaustive')}
          help="Give every source (Soulseek, then HiFi, Tidal and so on) its own retry budget."
          more={
            <>
              Each source spends <em>queries × retries per query</em> attempts before the track
              moves on. Across two sources that can mean many downloads, so use it for tracks that
              are hard to match (CJK artist names, for one). Needs the option above.
            </>
          }
        >
          <Switch
            id="retry-exhaustive"
            checked={values.retryExhaustive}
            onChange={(event) => pp('retry_exhaustive')(event.target.checked)}
          />
        </Row>
        <Row
          sub
          field
          id="retries-per-query-row"
          hidden={!values.retryExhaustive}
          label="Retries per query (per source)"
          htmlFor="retries-per-query"
          changed={ppc('retries_per_query')}
          help="How many candidates to try per search query, per source."
          more={
            <>
              The budget per source is <em>number of search queries × this value</em>. Only used
              when Exhaustive retry is on.
            </>
          }
        >
          <NumberField
            id="retries-per-query"
            min="1"
            max="20"
            step="1"
            value={retries.value}
            onChange={(event) => retries.onChange(event.target.value)}
          />
        </Row>
        <Row
          label="Accept best version mismatch as last resort"
          htmlFor="accept-version-mismatch-fallback"
          changed={ppc('accept_version_mismatch_fallback')}
          help="When every candidate failed the same version mismatch, take the best one instead of leaving the track missing."
          more={
            <>
              Applies once retries are used up and <em>every</em> candidate failed the{' '}
              <strong>same</strong> version mismatch (only an instrumental exists, say): the first
              one tried is accepted. Only AcoustID is bypassed. Integrity, duration and bit-depth
              checks still run, so cut-off or plainly wrong files are never let through.{' '}
              <strong>Off</strong> leaves such tracks failed.
            </>
          }
        >
          <Switch
            id="accept-version-mismatch-fallback"
            checked={values.versionFallback}
            onChange={(event) => pp('accept_version_mismatch_fallback')(event.target.checked)}
          />
        </Row>
        <Row
          sub
          field
          id="version-mismatch-min-count-row"
          hidden={!values.versionFallback}
          label="Minimum matching mismatches before accepting"
          htmlFor="version-mismatch-min-count"
          changed={ppc('version_mismatch_min_count')}
          help="How many quarantined candidates must have failed the same version mismatch first."
          more="Higher means more proof that no correct version exists. Only used when the option above is on."
        >
          <NumberField
            id="version-mismatch-min-count"
            min="1"
            max="20"
            step="1"
            value={minCount.value}
            onChange={(event) => minCount.onChange(event.target.value)}
          />
        </Row>
      </Card>

      <Card title="Album publishing" hint="When album downloads appear in your library">
        <Row
          label="Publish albums only when complete"
          htmlFor="album-atomic-publish"
          changed={ad('atomic_publish')}
          help="Off by default: tracks appear in your library as they finish. On: an album's tracks are held back and moved in together once the whole album is done."
          more={
            <>
              With this on, Plex, Jellyfin or Navidrome never shows a half-loaded album. If a batch
              stalls or fails, its tracks stay in staging (never your library) and the failed tracks
              stay retryable on your Wishlist. The staging area is SoulSync&apos;s own working
              folder, <code>storage/album_bundle_staging/&lt;batch id&gt;</code>. It is{' '}
              <strong>not</strong> the Staging path set under Library, which is a different folder
              for a different job. It is emptied when the batch finishes. If a crash leaves one
              behind, the next start moves anything still holding audio into the Downloads recycle
              bin (labelled &ldquo;Stalled album download&rdquo;) and deletes nothing, so a stalled
              album can be recovered. Only full-album and Wishlist album downloads are affected;
              single tracks and album top-ups are not. Tracks stay on your Wishlist until the album
              is published, so an interrupted album can always be retried.
            </>
          }
        >
          <Switch
            id="album-atomic-publish"
            checked={values.atomicPublish}
            onChange={(event) =>
              store.set('album_downloads', 'atomic_publish', event.target.checked)
            }
          />
        </Row>
        <Row
          label="Recover interrupted albums on startup"
          htmlFor="album-atomic-recover"
          changed={ad('atomic_recover_orphans')}
          help="On by default, and only matters when album publishing above is on."
          more={
            <>
              If SoulSync restarts or crashes part-way through an album, the tracks it had finished
              are left in the private staging folder, where no media server can see them. On the
              next start SoulSync moves those finished tracks into your library, and the tracks it
              never got to are still on your Wishlist, so the album fills itself in. Turn this{' '}
              <strong>off</strong> to keep strict quarantine: the interrupted album stays hidden
              until you deal with it by hand. Files are never deleted either way.
            </>
          }
        >
          <Switch
            id="album-atomic-recover"
            checked={values.atomicRecover}
            onChange={(event) =>
              store.set('album_downloads', 'atomic_recover_orphans', event.target.checked)
            }
          />
        </Row>
      </Card>

      <WishlistRules store={store} />
    </>
  );
}

/** the video side's rules: what happens when a download fails, the specials,
 *  and the YouTube follow count (each saves at once, or when its field is left) */
export function VideoBehaviour() {
  const { config } = useVideoDownloads();
  // the config loads with whichever page shows it first (once a page)
  useEffect(() => void videoDownloads.load(), []);
  return (
    <>
      <Card title="When a download fails" hint="Movies and TV. Saves at once.">
        <Row
          id="video-redownload-failed-group"
          label="When a video download fails, grab the next best release"
          htmlFor="video-redownload-failed"
          help="A failed or stalled release is blocklisted and the next one your quality profile ranks is grabbed right away."
          more="This covers torrents and usenet. It stops after 5 failed releases of the same title in 2 days, and you get the failed notification when nothing else could be taken."
        >
          <Switch
            id="video-redownload-failed"
            checked={config ? config.redownload_failed !== false : true}
            onChange={(event) =>
              void videoDownloads.change({ redownload_failed: event.target.checked })
            }
          />
        </Row>
      </Card>
      <Card title="Monitoring" hint="What is hunted. Saves at once.">
        <MonitorSpecialsRow />
      </Card>
      <YoutubeFollowCount />
    </>
  );
}
