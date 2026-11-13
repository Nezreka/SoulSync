/**
 * the files of one episode, as cards, inside the page's own episode list.
 * video-detail.js opens an episode and mounts this under its row; the cards
 * and every action on them are the same ones a movie's Files section has.
 */

import { useCallback, useEffect, useState } from 'react';

import type { LibraryFile, ShowLibrary } from './types';

import { pad2 } from './format';
import { ReassignDialog } from './library-dialogs';
import { invalidate, onInvalidate, seasonLibrary } from './library-store';
import { bridge, type FileActions, FileCards, useFileActions } from './library-tab';
import s from './video-library.module.css';

export function EpisodeFiles({
  showId,
  season,
  episodeId,
}: {
  showId: number;
  season: number;
  episodeId: number;
}) {
  const [show, setShow] = useState<ShowLibrary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [moving, setMoving] = useState<LibraryFile | null>(null);

  const load = useCallback(() => {
    let live = true;
    seasonLibrary(showId, season)
      .then((data) => {
        if (!live) return;
        setShow(data);
        setError(null);
      })
      .catch((e: Error) => live && setError(e.message || 'Couldn’t read this episode’s files'));
    return () => {
      live = false;
    };
  }, [showId, season]);

  useEffect(() => load(), [load]);
  useEffect(
    () =>
      onInvalidate((kind, id) => {
        if (kind === 'show' && id === showId) load();
      }),
    [showId, load],
  );

  const reload = useCallback(() => invalidate('show', showId), [showId]);
  const { onRename, onDelete } = useFileActions(reload, 'show');

  if (error) return <p className={s.empty}>{error}</p>;
  if (!show) return <p className={s.loading}>Checking the files…</p>;
  const block = show.seasons.find((x) => x.season_number === season);
  const ep = block?.episodes.find((e) => e.id === episodeId);
  if (!ep || !ep.files.length) return null;

  const label = `S${pad2(season)}E${pad2(ep.episode_number)}`;
  const b = bridge();
  const actions: FileActions = {
    canManage: show.can_manage,
    recycle: show.recycle,
    onRename,
    onDelete: (f) => onDelete(f, show.recycle, ep.files.length <= 1),
    onSubtitles: b.subtitleSearch
      ? () =>
          b.subtitleSearch?.('episode', ep.id, ep.title || `Episode ${ep.episode_number}`, label)
      : undefined,
    onUpgrade: b.manualSearch
      ? () => b.manualSearch?.('episode', season, ep.episode_number)
      : undefined,
    onReassign: (f) => setMoving(f),
  };
  return (
    <div className={s.epCards}>
      <FileCards files={ep.files} actions={actions} label={`${label} file`} />
      {moving ? (
        <ReassignDialog
          show={show}
          file={moving}
          from={{ season, episode: ep.episode_number }}
          onClose={() => setMoving(null)}
          onDone={() => {
            setMoving(null);
            reload();
            b.reloadPage?.();
          }}
        />
      ) : null}
    </div>
  );
}
