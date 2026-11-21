/**
 * playlists: everything you listen to as a list. up top, the ones you go
 * back to (liked songs first) and flow. then yours, what soulsync made for
 * you (daily mixes, moods, fresh tape, hidden gems…), the ones mirrored from
 * spotify, deezer and the rest, and listenbrainz. filter by kind, find one by
 * name, sort yours and the mirrored ones. make a new one right here.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { createFileRoute, useNavigate } from '@tanstack/react-router';
import { useMemo, useState } from 'react';

import type { DiscoverCard as Card, DiscoverSection, PlaylistSummary } from '../-player.api';

import { songCount, sourceName } from '../-lib/playlists';
import { discoverPlaylistsQuery, playlistEdits, playlistsQuery } from '../-player.api';
import { PlusIcon, SearchIcon } from '../-ui/icons';
import { CardPlay, Empty, PageTitle, PlayerLink, Rail, styles as s } from '../-ui/kit';
import { DiscoverCard, discoverHref } from '../-ui/music/discover-card';
import m from '../-ui/music/music.module.css';
import { PlaylistCard } from '../-ui/music/playlist-card';
import { PlaylistCover } from '../-ui/music/playlist-cover';
import { NotPartOfProfile } from '../-ui/not-part';
import { useSections } from '../-ui/sections';
import { Sheet } from '../-ui/sheet';
import sh from '../-ui/sheet.module.css';

type Kind = 'all' | 'yours' | DiscoverSection | 'mirrored';
type Sort = 'recent' | 'name' | 'size';

export const Route = createFileRoute('/player/music/playlists')({
  component: PlaylistsPage,
  validateSearch: (search: Record<string, unknown>): { kind?: Kind } => {
    const kind = search.kind;
    return typeof kind === 'string' &&
      ['yours', 'made', 'fresh', 'picks', 'mirrored', 'listenbrainz'].includes(kind)
      ? { kind: kind as Kind }
      : {};
  },
});

const LABELS: Record<Exclude<Kind, 'all'>, string> = {
  yours: 'Yours',
  made: 'Made for you',
  fresh: 'New for you',
  picks: 'Picks',
  mirrored: 'Mirrored',
  listenbrainz: 'ListenBrainz',
};

function sorted(list: readonly PlaylistSummary[], sort: Sort) {
  const out = [...list];
  // liked songs always leads yours
  const liked = (p: PlaylistSummary) => (p.liked ? 0 : 1);
  if (sort === 'name') out.sort((a, b) => liked(a) - liked(b) || a.name.localeCompare(b.name));
  else if (sort === 'size')
    out.sort((a, b) => liked(a) - liked(b) || b.track_count - a.track_count);
  else
    out.sort(
      (a, b) => liked(a) - liked(b) || (b.updated_at ?? '').localeCompare(a.updated_at ?? ''),
    );
  return out;
}

/** one of the ones you go back to: a small tile, play on hover */
function Shortcut({ playlist }: { playlist: PlaylistSummary }) {
  return (
    <div className={m.shortcut} data-liked={playlist.liked ? 'true' : undefined}>
      <PlayerLink
        href={`/player/music/playlist/${playlist.id}`}
        className={m.shortcutLink}
        data-nav
      >
        <span className={m.shortcutArt}>
          <PlaylistCover playlist={playlist} size="thumb" />
        </span>
        <span className={m.shortcutText}>
          <span className={m.shortcutName}>{playlist.name}</span>
          <span className={m.shortcutSub}>{songCount(playlist.track_count)}</span>
        </span>
      </PlayerLink>
      {playlist.track_count ? (
        <span className={m.shortcutPlay}>
          <CardPlay target={{ kind: 'playlist', id: playlist.id }} title={playlist.name} />
        </span>
      ) : null}
    </div>
  );
}

/** flow, up top: your songs, nonstop */
function FlowBanner({ card }: { card: Card }) {
  return (
    <div className={m.flow}>
      <div className={m.flowArt} aria-hidden="true">
        {card.cover_tiles.slice(0, 4).map((t) => (
          <img key={t} src={t} alt="" loading="lazy" />
        ))}
      </div>
      <PlayerLink href={discoverHref(card.key)} className={m.flowText} data-nav>
        <span className={m.flowKicker}>Made for you</span>
        <span className={m.flowName}>{card.name}</span>
        <span className={m.flowBlurb}>{card.blurb}</span>
      </PlayerLink>
      <span className={m.flowPlay}>
        <CardPlay target={{ kind: 'discover', key: card.key }} title={card.name} />
      </span>
    </div>
  );
}

/** the new-playlist dialog: a name (and a description if you like) */
export function NewPlaylist({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const make = async () => {
    if (!name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const { id } = await playlistEdits.create(name.trim());
      await queryClient.invalidateQueries({ queryKey: ['player', 'playlists'] });
      onClose();
      void navigate({
        to: '/player/music/playlist/$playlistId',
        params: { playlistId: String(id) },
      });
    } catch {
      setError("Couldn't make that playlist. Try another name.");
      setBusy(false);
    }
  };
  return (
    <Sheet
      title="New playlist"
      subtitle="Add songs from any album, the queue or what's playing."
      onClose={onClose}
      footer={
        <>
          <button type="button" className={sh.btn} onClick={onClose} data-nav>
            Cancel
          </button>
          <button
            type="button"
            className={sh.btnPrimary}
            disabled={!name.trim() || busy}
            onClick={() => void make()}
            data-nav
          >
            {busy ? 'Making…' : 'Make it'}
          </button>
        </>
      }
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void make();
        }}
      >
        <label className={sh.field}>
          Name
          <input
            className={sh.input}
            value={name}
            maxLength={120}
            placeholder="Late night drive"
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        {error ? <p className={sh.error}>{error}</p> : null}
      </form>
    </Sheet>
  );
}

function PlaylistsPage() {
  const sections = useSections();
  const navigate = useNavigate();
  const { kind = 'all' } = Route.useSearch();
  const lists = useQuery({ ...playlistsQuery(), enabled: sections.music });
  const discover = useQuery({ ...discoverPlaylistsQuery(), enabled: sections.music });
  const [making, setMaking] = useState(false);
  const [find, setFind] = useState('');
  const [sort, setSort] = useState<Sort>('recent');
  const setKind = (next: Kind) =>
    void navigate({
      to: '/player/music/playlists',
      search: next === 'all' ? {} : { kind: next },
      replace: true,
    });

  const needle = find.trim().toLowerCase();
  const hit = (name: string, also = '') =>
    !needle || name.toLowerCase().includes(needle) || also.toLowerCase().includes(needle);

  const yours = useMemo(() => sorted(lists.data?.yours ?? [], sort), [lists.data, sort]);
  const mirrored = useMemo(() => sorted(lists.data?.mirrored ?? [], sort), [lists.data, sort]);
  const groups = discover.data ?? [];
  const flow = groups.flatMap((g) => g.playlists).find((c) => c.key === 'flow');
  // the ones you go back to: liked songs, then the last ones touched
  const shortcuts = useMemo(() => {
    const all = [...(lists.data?.yours ?? []), ...(lists.data?.mirrored ?? [])];
    const liked = all.filter((p) => p.liked);
    const recent = all
      .filter((p) => !p.liked)
      .sort((a, b) => (b.updated_at ?? '').localeCompare(a.updated_at ?? ''));
    return [...liked, ...recent].slice(0, 6);
  }, [lists.data]);

  if (!sections.music) return <NotPartOfProfile what="Music" single />;

  const yoursShown = yours.filter((p) => hit(p.name, p.description ?? ''));
  const mirroredShown = mirrored.filter((p) => hit(p.name, sourceName(p.source) ?? ''));
  // flow has the banner up top on the overview; everywhere else it's a card
  const discoverShown = (id: DiscoverSection) =>
    (groups.find((g) => g.id === id)?.playlists ?? []).filter(
      (c) => !(c.key === 'flow' && kind === 'all' && !needle) && hit(c.name, c.blurb),
    );
  const counts: Record<Exclude<Kind, 'all'>, number> = {
    yours: yours.length,
    made: groups.find((g) => g.id === 'made')?.playlists.length ?? 0,
    fresh: groups.find((g) => g.id === 'fresh')?.playlists.length ?? 0,
    picks: groups.find((g) => g.id === 'picks')?.playlists.length ?? 0,
    mirrored: mirrored.length,
    listenbrainz: groups.find((g) => g.id === 'listenbrainz')?.playlists.length ?? 0,
  };
  const kinds = (Object.keys(LABELS) as Exclude<Kind, 'all'>[]).filter(
    (x) => x === 'yours' || counts[x] > 0,
  );
  const sortable = kind === 'yours' || kind === 'mirrored';
  const label = (id: DiscoverSection) => groups.find((g) => g.id === id)?.label ?? LABELS[id];

  const discoverGrid = (id: DiscoverSection) => {
    const cards = discoverShown(id);
    return cards.length ? (
      <div className={s.grid} data-shape="square">
        {cards.map((c) => (
          <DiscoverCard key={c.key} card={c} />
        ))}
      </div>
    ) : (
      <Empty title="Nothing here">
        {needle ? 'Nothing by that name.' : 'SoulSync makes these as it learns what you like.'}
      </Empty>
    );
  };
  const yoursGrid = (list: PlaylistSummary[]) =>
    list.length ? (
      <div className={s.grid} data-shape="square">
        {list.map((p) => (
          <PlaylistCard key={p.id} playlist={p} />
        ))}
      </div>
    ) : needle ? (
      <Empty title="Nothing by that name">Try part of its name.</Empty>
    ) : (
      <Empty title="No playlists yet">
        Make one here, or add a song to a playlist from any album or the queue.
      </Empty>
    );

  return (
    <>
      <div className={s.toolbar}>
        <PageTitle>Playlists</PageTitle>
        <div className={m.plTools}>
          <label className={m.plFind}>
            <SearchIcon />
            <input
              type="search"
              value={find}
              placeholder="Find a playlist"
              aria-label="Find a playlist by name"
              onChange={(e) => setFind(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Escape') setFind('');
              }}
            />
          </label>
          <button type="button" className={s.btnPrimary} onClick={() => setMaking(true)} data-nav>
            <PlusIcon />
            New playlist
          </button>
        </div>
      </div>

      {lists.isLoading ? (
        <div className={s.skeleton} style={{ height: '40vh' }} />
      ) : lists.isError ? (
        <Empty title="Couldn't load your playlists">Try again in a moment.</Empty>
      ) : (
        <>
          {kind === 'all' && !needle && (shortcuts.length || flow) ? (
            <div className={m.top}>
              {shortcuts.length ? (
                <div className={m.shortcuts} aria-label="Jump back in">
                  {shortcuts.map((p) => (
                    <Shortcut key={p.id} playlist={p} />
                  ))}
                </div>
              ) : null}
              {flow ? <FlowBanner card={flow} /> : null}
            </div>
          ) : null}

          <div className={s.chips} role="tablist" aria-label="Kinds of playlist">
            {(['all', ...kinds] as Kind[]).map((x) => (
              <button
                key={x}
                type="button"
                role="tab"
                className={s.filterChip}
                aria-selected={kind === x}
                onClick={() => setKind(x)}
                data-nav
              >
                {x === 'all' ? 'All' : LABELS[x]}
              </button>
            ))}
            {sortable ? (
              <label className={m.plSort}>
                <span>Sort</span>
                <select value={sort} onChange={(e) => setSort(e.target.value as Sort)}>
                  <option value="recent">Recent</option>
                  <option value="name">A to Z</option>
                  <option value="size">Most songs</option>
                </select>
              </label>
            ) : null}
          </div>

          {kind === 'all' ? (
            <>
              <section className={s.rail} aria-label="Your playlists">
                <div className={s.railHead}>
                  <h2 className={s.sectionTitle}>Your playlists</h2>
                  {yoursShown.length > 12 ? (
                    <button type="button" className={s.railMore} onClick={() => setKind('yours')}>
                      All {yoursShown.length}
                    </button>
                  ) : null}
                </div>
                {yoursGrid(yoursShown.slice(0, 12))}
              </section>
              {(['made', 'fresh', 'picks'] as DiscoverSection[]).map((id) => {
                const cards = discoverShown(id);
                return cards.length ? (
                  <Rail key={id} title={label(id)}>
                    {cards.map((c) => (
                      <DiscoverCard key={c.key} card={c} />
                    ))}
                  </Rail>
                ) : null;
              })}
              {mirroredShown.length ? (
                <Rail title="Mirrored">
                  {mirroredShown.map((p) => (
                    <PlaylistCard key={p.id} playlist={p} />
                  ))}
                </Rail>
              ) : null}
              {discoverShown('listenbrainz').length ? (
                <Rail title={label('listenbrainz')}>
                  {discoverShown('listenbrainz').map((c) => (
                    <DiscoverCard key={c.key} card={c} />
                  ))}
                </Rail>
              ) : null}
              {discover.isError ? (
                <p className={m.plNote}>
                  SoulSync's own playlists couldn't load. Try again in a moment.
                </p>
              ) : null}
            </>
          ) : kind === 'yours' ? (
            yoursGrid(yoursShown)
          ) : kind === 'mirrored' ? (
            mirroredShown.length ? (
              <div className={s.grid} data-shape="square">
                {mirroredShown.map((p) => (
                  <PlaylistCard key={p.id} playlist={p} />
                ))}
              </div>
            ) : (
              <Empty title="Nothing by that name">Try part of its name.</Empty>
            )
          ) : (
            discoverGrid(kind)
          )}
        </>
      )}
      {making ? <NewPlaylist onClose={() => setMaking(false)} /> : null}
    </>
  );
}
