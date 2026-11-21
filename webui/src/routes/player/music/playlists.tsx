/**
 * playlists: yours (Liked Songs first, then what you edited last) and the
 * ones you mirrored from spotify, deezer and the rest. make a new one right
 * here.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { createFileRoute, useNavigate } from '@tanstack/react-router';
import { useState } from 'react';

import { playlistEdits, playlistsQuery } from '../-player.api';
import { PlusIcon } from '../-ui/icons';
import { Empty, PageTitle, styles as s } from '../-ui/kit';
import { PlaylistCard } from '../-ui/music/playlist-card';
import { NotPartOfProfile } from '../-ui/not-part';
import { useSections } from '../-ui/sections';
import { Sheet } from '../-ui/sheet';
import sh from '../-ui/sheet.module.css';

export const Route = createFileRoute('/player/music/playlists')({
  component: PlaylistsPage,
});

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
  const lists = useQuery({ ...playlistsQuery(), enabled: sections.music });
  const [making, setMaking] = useState(false);
  if (!sections.music) return <NotPartOfProfile what="Music" single />;
  const yours = lists.data?.yours ?? [];
  const mirrored = lists.data?.mirrored ?? [];

  return (
    <>
      <div className={s.toolbar}>
        <PageTitle>Playlists</PageTitle>
        <button type="button" className={s.btnPrimary} onClick={() => setMaking(true)} data-nav>
          <PlusIcon />
          New playlist
        </button>
      </div>

      {lists.isLoading ? (
        <div className={s.skeleton} style={{ height: '40vh' }} />
      ) : lists.isError ? (
        <Empty title="Couldn't load your playlists">Try again in a moment.</Empty>
      ) : (
        <>
          <section className={s.rail} aria-label="Your playlists">
            <div className={s.railHead}>
              <h2 className={s.sectionTitle}>Your playlists</h2>
            </div>
            {yours.length ? (
              <div className={s.grid} data-shape="square">
                {yours.map((p) => (
                  <PlaylistCard key={p.id} playlist={p} />
                ))}
              </div>
            ) : (
              <Empty title="No playlists yet">
                Make one here, or add a song to a playlist from any album or the queue.
              </Empty>
            )}
          </section>
          {mirrored.length ? (
            <section className={s.rail} aria-label="Mirrored playlists">
              <div className={s.railHead}>
                <h2 className={s.sectionTitle}>Mirrored</h2>
                <span className={s.railMore}>
                  Kept in step with where they came from. Make one yours to change it.
                </span>
              </div>
              <div className={s.grid} data-shape="square">
                {mirrored.map((p) => (
                  <PlaylistCard key={p.id} playlist={p} />
                ))}
              </div>
            </section>
          ) : null}
        </>
      )}
      {making ? <NewPlaylist onClose={() => setMaking(false)} /> : null}
    </>
  );
}
