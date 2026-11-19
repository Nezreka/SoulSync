/**
 * Downloads > Filtering: what is skipped or preferred when music downloads.
 * Explicit content, a size cap, a preferred version, and the genre whitelist
 * (it filters enrichment tags; it moves with the Library tab's tags). Same
 * keys, ids and saves as the old Downloads tab's Filtering section.
 */

import { useState } from 'react';

import type { SettingsStore } from '../../settings-store';

import { Btn, Card, ChipList, NumberField, Row, Select, Switch, TextField } from '../../ui';
import { PREFERRED_VERSIONS, addGenre, toFloat } from '../downloads';
import { useChanged, useNumber, useValues } from './behaviour-view';

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

function Genres({
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
    <div id="genre-whitelist-container" hidden={!enabled}>
      <Row
        stack
        sub
        label="Genres that are kept"
        htmlFor="genre-whitelist-search"
        help="Type to find one. Press Enter to add what you typed."
      >
        <TextField
          id="genre-whitelist-search"
          wide
          icon="search"
          placeholder="Search or add genre..."
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
        <Btn small onClick={() => void resetGenres(store)}>
          Reset to defaults
        </Btn>
        <span id="genre-whitelist-count">
          {enabled && genres.length ? `${genres.length} genres` : ''}
        </span>
      </Row>
      <Row stack sub label="">
        {enabled ? (
          <ChipList
            id="genre-whitelist-chips"
            itemAttr="data-genre"
            items={genres.length ? shown : []}
            empty={
              genres.length
                ? 'Nothing matches that.'
                : 'No genres yet. "Reset to defaults" loads the default list.'
            }
            removeLabel={(genre) => `Remove ${genre}`}
            onRemove={(genre) => save(genres.filter((x) => x !== genre))}
          />
        ) : (
          <div id="genre-whitelist-chips" />
        )}
      </Row>
    </div>
  );
}

export function FilteringView({ store }: { store: SettingsStore }) {
  const { values } = useValues(store);
  const maxMb = useNumber(store, values.maxMbPerMinute, (raw) =>
    store.set('download_source', 'max_mb_per_minute', Math.max(0, toFloat(raw, 0))),
  );
  const blocked = !values.allowExplicit;
  const cf = (key: string) => useChanged(store, 'content_filter', key); // eslint-disable-line react-hooks/rules-of-hooks
  const maxChanged = useChanged(store, 'download_source', 'max_mb_per_minute');
  const versionChanged = useChanged(store, 'soulseek', 'preferred_version');
  const genreChanged = useChanged(store, 'genre_whitelist', 'enabled', 'genres');
  return (
    <>
      <Card title="Explicit content" hint="What is done with tracks marked explicit">
        <Row
          label="Allow explicit content in downloads"
          htmlFor="allow-explicit"
          changed={cf('allow_explicit')}
          help="When off, tracks marked as explicit on Spotify are skipped during matched downloads. Manual Soulseek searches are not affected."
        >
          <Switch
            id="allow-explicit"
            checked={values.allowExplicit}
            onChange={(event) =>
              store.set('content_filter', 'allow_explicit', event.target.checked)
            }
          />
        </Row>
        {/* #923: only meaningful while explicit content is allowed */}
        <Row
          sub
          off={blocked}
          label="Prefer explicit versions when matching"
          htmlFor="prefer-explicit"
          changed={cf('prefer_explicit')}
          help="Files marked Explicit or Uncensored rank higher, and Clean, Censored or Radio Edit files rank lower."
          more="Nothing is ever skipped: if only a clean version exists, it still downloads. Needs explicit content to be allowed."
        >
          <Switch
            id="prefer-explicit"
            checked={values.preferExplicit}
            disabled={blocked}
            onChange={(event) =>
              store.set('content_filter', 'prefer_explicit', event.target.checked)
            }
          />
        </Row>
        {/* #2081: the hard version of the preference above */}
        <Row
          sub
          off={blocked}
          label="Never download clean versions"
          htmlFor="exclude-clean"
          changed={cf('exclude_clean')}
          help="Files marked Clean, Censored, Edited or Radio Edit are never picked for a track that is not that cut itself."
          more="If only a clean version exists, the track waits on your Wishlist until a real one turns up. Needs explicit content to be allowed."
        >
          <Switch
            id="exclude-clean"
            checked={values.excludeClean}
            disabled={blocked}
            onChange={(event) => store.set('content_filter', 'exclude_clean', event.target.checked)}
          />
        </Row>
      </Card>

      <Card title="Size and version" hint="Which file wins when there is a choice">
        <Row
          field
          label="Maximum music download size"
          htmlFor="music-max-mb-per-minute"
          changed={maxChanged}
          help="0 turns the limit off. At 10 MB/min, a 3:30 track may be up to 35 MB."
          more="1 MB is 1,000,000 bytes. Oversized candidates are skipped before download. It uses the advertised duration, or the requested track length when there is none. Album releases use the album length. Sources without size or duration information cannot be checked. This does not resize existing files or limit post-processing output."
        >
          <NumberField
            id="music-max-mb-per-minute"
            min="0"
            step="0.1"
            unit="MB/min"
            value={maxMb.value}
            onChange={(event) => maxMb.onChange(event.target.value)}
          />
        </Row>
        <Row
          field
          label="Prefer a version when one exists"
          htmlFor="preferred-version"
          changed={versionChanged}
          help="Spotify often only carries one cut of a track."
          more={
            <>
              With a version picked here, a matching file wins over the plain one when both turn up
              in the same Soulseek search. Nothing is ever skipped: if that version isn&apos;t out
              there, the normal one downloads exactly as it does now. Off by default.
              <br />
              <br />
              Live, remix, acoustic and instrumental files are normally thrown out on sight when the
              source didn&apos;t ask for one. Picking any of those four here lifts that for the
              version you picked, so a live take of a similarly titled song can be accepted when the
              real track is nowhere to be found.
            </>
          }
        >
          <Select
            id="preferred-version"
            value={values.preferredVersion}
            options={PREFERRED_VERSIONS.map((o) => ({
              value: o.value,
              label: o.label.replace(' — ', ': '),
            }))}
            onChange={(event) => store.set('soulseek', 'preferred_version', event.target.value)}
          />
        </Row>
      </Card>

      <Card title="Genres" hint="Which genre tags are kept when music is tagged">
        <Row
          label="Enable strict genre filtering"
          htmlFor="genre-whitelist-enabled"
          changed={genreChanged}
          help="When on, only genres on the list below are kept during enrichment."
          more="Junk tags (artist names, radio shows, playlist names) are dropped without a word. When off, all genres pass through unchanged."
        >
          <Switch
            id="genre-whitelist-enabled"
            checked={values.genreWhitelist}
            onChange={(event) => {
              const on = event.target.checked;
              store.set('genre_whitelist', 'enabled', on);
              // the first time it is switched on with nothing in it, load the defaults
              if (on && values.genres.length === 0) void resetGenres(store);
            }}
          />
        </Row>
        <Genres store={store} enabled={values.genreWhitelist} genres={values.genres} />
      </Card>
    </>
  );
}
