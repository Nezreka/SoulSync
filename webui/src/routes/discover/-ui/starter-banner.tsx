import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';

import {
  buildLine,
  canSavePicks,
  dismissStarter,
  fetchPickerArtists,
  fetchStarter,
  MIN_PICKS,
  pickKey,
  pickProgressLabel,
  savePicks,
  seedFill,
  starterOffers,
  STARTER_KEY,
  togglePick,
  type StarterOffer,
  type StarterPick,
  type StarterStatus,
} from '../-discover.starter';

/**
 * The top of Discover for a profile with little or no taste yet.
 *
 * Shows only while the server says the profile is new and nobody dismissed
 * it, plus while a picked-artists build is still running so the reader sees
 * their first picks arrive. Never shows once the profile has enough history.
 */

const POLL_MS = 2500;

/** The shelves the picked artists fill, refreshed as each one is built. */
const FED_SHELVES = [
  ['discover', 'hero'],
  ['discover', 'similar-artists'],
  ['discover', 'listening-recs'],
] as const;

function say(message: string, kind: 'success' | 'error' | 'info') {
  window.showToast?.(message, kind);
}

export function StarterBanner() {
  const queryClient = useQueryClient();
  const [pickerOpen, setPickerOpen] = useState(false);
  const [gone, setGone] = useState(false);
  const lastDone = useRef(0);

  const status = useQuery({
    queryKey: STARTER_KEY,
    queryFn: fetchStarter,
    staleTime: 60_000,
    retry: false,
    refetchInterval: (query) =>
      (query.state.data as StarterStatus | null | undefined)?.build?.state === 'running'
        ? POLL_MS
        : false,
  });
  const data = status.data;
  const building = data?.build?.state === 'running';

  // the faces on the banner: the main chart, the same answer the picker opens on
  const faces = useQuery({
    queryKey: ['discover', 'starter-artists', 0, ''],
    queryFn: () => fetchPickerArtists({ genre: 0 }),
    enabled: Boolean(data?.show_banner),
    staleTime: 30 * 60_000,
    retry: false,
  });

  // each artist the build finishes is new similar artists: refresh what they feed
  useEffect(() => {
    const build = data?.build;
    if (!build) return;
    if (build.done > lastDone.current || (build.state === 'done' && lastDone.current >= 0)) {
      for (const key of FED_SHELVES) void queryClient.invalidateQueries({ queryKey: [...key] });
    }
    if (build.state === 'done' && lastDone.current > 0 && lastDone.current < build.total) {
      say('Your first picks are in. Scroll down to see them.', 'success');
    }
    lastDone.current = build.state === 'running' ? build.done : -1;
  }, [data?.build, queryClient]);

  if (gone || !data || (!data.show_banner && !building)) return null;

  const offers = starterOffers(data);
  const line = buildLine(data.build);
  const fill = seedFill(data);
  const artwork = (faces.data?.artists ?? []).filter((a) => a.image_url).slice(0, 5);

  const act = (offer: StarterOffer) => {
    if (offer.id === 'pick') {
      setPickerOpen(true);
      return;
    }
    if (offer.id === 'spotify' && offer.href) {
      void window.SoulSyncWebRouter?.navigateToHref(offer.href);
      return;
    }
    window.openMyAccountsModal?.();
  };

  const dismiss = async () => {
    setGone(true);
    if (!(await dismissStarter())) {
      setGone(false);
      say("Couldn't hide that. Try again.", 'error');
      return;
    }
    queryClient.setQueryData(STARTER_KEY, { ...data, dismissed: true, show_banner: false });
  };

  return (
    <>
      <section className="dsc-starter" aria-label="Make Discover yours">
        <div className="dsc-starter-glow" aria-hidden="true" />
        <div className="dsc-starter-main">
          <span className="dsc-pulse-eyebrow">Make Discover yours</span>
          <h2 className="dsc-starter-title">
            {building ? 'Your first picks are on the way' : 'Tell SoulSync what you love'}
          </h2>
          <p className="dsc-starter-lede">
            {line ??
              "Until it knows your taste, Discover shows what's trending. Each of these makes it yours."}
          </p>
          {building ? (
            <div
              className="dsc-starter-progress"
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={data.build.total || 1}
              aria-valuenow={data.build.done}
              aria-label="Building your first picks"
            >
              <span
                style={{
                  ['--starter-fill' as string]: `${
                    data.build.total ? (data.build.done / data.build.total) * 100 : 8
                  }%`,
                }}
              />
            </div>
          ) : (
            <div className="dsc-starter-offers">
              {offers.map((offer) => (
                <button
                  key={offer.id}
                  type="button"
                  className={`dsc-starter-offer${offer.id === 'pick' ? ' is-primary' : ''}`}
                  onClick={() => act(offer)}
                >
                  <span className="dsc-starter-offer-title">{offer.title}</span>
                  <span className="dsc-starter-offer-detail">{offer.detail}</span>
                </button>
              ))}
            </div>
          )}
        </div>
        <div className="dsc-starter-side" aria-hidden="true">
          {artwork.length ? (
            <div className="dsc-starter-faces">
              {artwork.map((a, i) => (
                <img
                  key={pickKey(a)}
                  src={a.image_url ?? ''}
                  alt=""
                  loading="lazy"
                  style={{ ['--face-i' as string]: i }}
                />
              ))}
            </div>
          ) : null}
          <div className="dsc-starter-meter">
            <span className="dsc-starter-meter-bar">
              <span style={{ ['--starter-fill' as string]: `${fill * 100}%` }} />
            </span>
            <span className="dsc-starter-meter-label">
              {Math.min(data.seeds, data.threshold)} of {data.threshold} artists known
            </span>
          </div>
        </div>
        {!building ? (
          <button
            type="button"
            className="dsc-starter-dismiss"
            aria-label="Hide this"
            title="Not now"
            onClick={() => void dismiss()}
          >
            ×
          </button>
        ) : null}
      </section>
      {pickerOpen ? (
        <ArtistPicker
          alreadyPicked={data.picked}
          onClose={() => setPickerOpen(false)}
          onSaved={(build) => {
            setPickerOpen(false);
            queryClient.setQueryData(STARTER_KEY, { ...data, build });
            void queryClient.invalidateQueries({ queryKey: [...STARTER_KEY] });
            for (const key of FED_SHELVES)
              void queryClient.invalidateQueries({ queryKey: [...key] });
          }}
        />
      ) : null}
    </>
  );
}

export interface ArtistPickerProps {
  alreadyPicked?: StarterPick[];
  onClose: () => void;
  onSaved: (build: StarterStatus['build']) => void;
}

/**
 * "Pick artists you love": search, or browse what's popular by genre, pick
 * five or more. Saving stores them as this profile's seeds and starts the
 * first build at once.
 */
export function ArtistPicker({ alreadyPicked = [], onClose, onSaved }: ArtistPickerProps) {
  const [genre, setGenre] = useState(0);
  const [typed, setTyped] = useState('');
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<StarterPick[]>([]);
  const [saving, setSaving] = useState(false);
  const searchRef = useRef<HTMLInputElement | null>(null);

  // search as you type, without a request per keystroke
  useEffect(() => {
    const t = setTimeout(() => setQuery(typed.trim()), 300);
    return () => clearTimeout(t);
  }, [typed]);

  useEffect(() => {
    searchRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const artists = useQuery({
    queryKey: ['discover', 'starter-artists', query ? -1 : genre, query],
    queryFn: () => fetchPickerArtists(query ? { query } : { genre }),
    staleTime: 30 * 60_000,
    retry: false,
  });

  const known = useMemo(() => new Set(alreadyPicked.map(pickKey)), [alreadyPicked]);
  const chosen = useMemo(() => new Set(selected.map(pickKey)), [selected]);
  const genres = artists.data?.genres ?? [];
  const total = selected.length + alreadyPicked.length;

  const save = async () => {
    if (!canSavePicks(total) || saving || !selected.length) return;
    setSaving(true);
    const result = await savePicks(selected);
    setSaving(false);
    if (!result.success || !result.build) {
      say(result.error || 'Could not save your picks', 'error');
      return;
    }
    say(`Saved ${selected.length} artists. Building your first picks now.`, 'success');
    onSaved(result.build);
  };

  return (
    <div
      className="modal-overlay dsc-picker-overlay"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        className="dsc-picker"
        role="dialog"
        aria-modal="true"
        aria-label="Pick artists you love"
      >
        <header className="dsc-picker-head">
          <div>
            <span className="dsc-pulse-eyebrow">Make Discover yours</span>
            <h2>Pick artists you love</h2>
            <p>
              Choose {MIN_PICKS} or more. Discover builds your first picks from them right away.
            </p>
          </div>
          <button type="button" className="dsc-picker-close" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </header>

        <div className="dsc-picker-tools">
          <input
            ref={searchRef}
            type="search"
            className="dsc-picker-search"
            placeholder="Search for an artist"
            aria-label="Search for an artist"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
          />
          {!query ? (
            <div className="dsc-picker-genres" role="tablist" aria-label="Popular by genre">
              {genres.map((g) => (
                <button
                  key={g.id}
                  type="button"
                  role="tab"
                  aria-selected={g.id === genre}
                  className={`dsc-picker-genre${g.id === genre ? ' is-on' : ''}`}
                  onClick={() => setGenre(g.id)}
                >
                  {g.name}
                </button>
              ))}
            </div>
          ) : null}
        </div>

        <div className="dsc-picker-grid" aria-busy={artists.isPending}>
          {artists.isPending ? (
            <p className="dsc-picker-note">Loading artists…</p>
          ) : artists.isError ? (
            <p className="dsc-picker-note">Couldn't load artists. Try another genre or search.</p>
          ) : (artists.data?.artists ?? []).length === 0 ? (
            <p className="dsc-picker-note">No artists found{query ? ` for "${query}"` : ''}.</p>
          ) : (
            (artists.data?.artists ?? []).map((artist) => {
              const key = pickKey(artist);
              const had = known.has(key);
              const on = had || chosen.has(key);
              return (
                <button
                  key={key}
                  type="button"
                  className={`dsc-picker-artist${on ? ' is-on' : ''}`}
                  aria-pressed={on}
                  disabled={had}
                  title={had ? 'Already picked' : undefined}
                  onClick={() => setSelected((s) => togglePick(s, artist))}
                >
                  <span className="dsc-picker-art">
                    {artist.image_url ? (
                      <img src={artist.image_url} alt="" loading="lazy" />
                    ) : (
                      <span className="dsc-picker-initial">{artist.name.charAt(0)}</span>
                    )}
                    <span className="dsc-picker-check" aria-hidden="true">
                      ✓
                    </span>
                  </span>
                  <span className="dsc-picker-name">{artist.name}</span>
                </button>
              );
            })
          )}
        </div>

        <footer className="dsc-picker-foot">
          <div className="dsc-picker-chosen" aria-live="polite">
            <span className="dsc-picker-stack" aria-hidden="true">
              {selected.slice(-6).map((p) =>
                p.image_url ? (
                  <img key={pickKey(p)} src={p.image_url} alt="" />
                ) : (
                  <span key={pickKey(p)} className="dsc-picker-initial">
                    {p.name.charAt(0)}
                  </span>
                ),
              )}
            </span>
            <span className="dsc-picker-count">{pickProgressLabel(total)}</span>
          </div>
          <button
            type="button"
            className="dsc-pulse-btn primary"
            disabled={!canSavePicks(total) || !selected.length || saving}
            onClick={() => void save()}
          >
            {saving ? 'Saving…' : 'Build my Discover'}
          </button>
        </footer>
      </div>
    </div>
  );
}
