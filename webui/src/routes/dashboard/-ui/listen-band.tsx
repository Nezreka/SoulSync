/**
 * The Listen band (dash-card data-card="listen") — the dashboard's payoff
 * section. Everything above it is about OWNING music; this row is about
 * playing it.
 *
 * - Library Radio hero: the endless own-collection shuffle that lived buried
 *   in the media player gets a front door (startLibraryRadio seam,
 *   media-player.js:3129). Designed as a living station deck: spinning
 *   vinyl, dancing EQ, glowing transport button.
 * - Discover Mixes grid: Flow and the daily quick mixes brought directly to the
 *   dashboard. Clicking a mix card opens its playlist preview modal, while
 *   tapping the play button plays it immediately.
 */

import { QueryClient, QueryClientContext, QueryClientProvider } from '@tanstack/react-query';
import { useCallback, useContext, useMemo, useRef, useState } from 'react';

import type { MixAction } from '../../discover/-discover.mixes';

import { fetchFlow } from '../../discover/-discover.api';
import { quickTiles } from '../../discover/-discover.greeting';
import { normalizeTrack } from '../../discover/-discover.helpers';
import { beginPlayIntent, playMixNow, playTrackNow } from '../../discover/-discover.playable';
import { defaultLazySource, useMixModal } from '../../discover/-discover.use-mix-modal';
import { useDiscoverMixes } from '../../discover/-discover.use-mixes';
import { QuickTilesGrid } from '../../discover/-ui/greeting-grid';
import { MixModal } from '../../discover/-ui/mix-modal';

let fallbackClient: QueryClient | null = null;
function getFallbackClient() {
  if (!fallbackClient) {
    fallbackClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
  }
  return fallbackClient;
}

function ListenBandInner() {
  const mixes = useDiscoverMixes();
  const tiles = useMemo(
    () => quickTiles(mixes.mixes, mixes.moodMixes, new Date().getHours()),
    [mixes.mixes, mixes.moodMixes],
  );

  const modal = useMixModal(mixes.registry, defaultLazySource);

  const [flowBusy, setFlowBusy] = useState(false);
  const pendingPlay = useRef<string | null>(null);
  const [playingKey, setPlayingKey] = useState<string | null>(null);
  const [playingTrackIndex, setPlayingTrackIndex] = useState<number | null>(null);

  /** flow: a fresh queue from the server every press, played straight away. */
  const handlePlayFlow = useCallback(() => {
    if (flowBusy) return;
    const intent = beginPlayIntent();
    setFlowBusy(true);
    void fetchFlow()
      .then((res) => {
        const tracks = res.tracks ?? [];
        if (!intent.isCurrent()) return;
        if (!tracks.length) {
          window.showToast?.('Flow needs some listening history first', 'info');
          return;
        }
        return playMixNow(tracks, 'Flow', intent);
      })
      .catch(() => window.showToast?.("Couldn't start Flow. Try again.", 'error'))
      .finally(() => setFlowBusy(false));
  }, [flowBusy]);

  /** Play a mix straight from its card */
  const handlePlayMix = useCallback(
    (key: string) => {
      if (pendingPlay.current === key) return;
      const mix = mixes.registry[key];
      if (!mix) return;
      const intent = beginPlayIntent();
      pendingPlay.current = key;
      setPlayingKey(key);
      void (async () => {
        try {
          let tracks = mix.tracks;
          if (!tracks?.length && mix.fetchTracks) {
            tracks = await mix.fetchTracks();
          }
          if (!intent.isCurrent()) return;
          if (!tracks?.length) {
            window.showToast?.('Could not load tracks for that mix', 'error');
            return;
          }
          await playMixNow(tracks, mix.title, intent);
        } catch {
          window.showToast?.('Failed to play mix', 'error');
        } finally {
          if (pendingPlay.current === key) {
            pendingPlay.current = null;
            setPlayingKey(null);
          }
        }
      })();
    },
    [mixes.registry],
  );

  /** Play one row of the open mix */
  const playTrackFromModal = useCallback(
    (index: number) => {
      const rows = modal.tracks ?? [];
      const track = rows[index];
      if (!track) {
        window.showToast?.('That track is no longer in this mix', 'error');
        return;
      }
      const intent = beginPlayIntent();
      setPlayingTrackIndex(index);
      void playTrackNow(track, normalizeTrack(track as never).name, intent).finally(() => {
        setPlayingTrackIndex(null);
      });
    },
    [modal.tracks],
  );

  /** Run modal header action, such as Play all */
  const runMixAction = useCallback(
    (action: MixAction) => {
      const mix = modal.mix;
      if (!mix) return;
      if (action.onclick.startsWith('play')) {
        handlePlayMix(mix.key);
      }
    },
    [modal.mix, handlePlayMix],
  );

  /** Hand selected tracks off to the YouTube missing-tracks downloader */
  const handleDownloadSelected = useCallback(() => {
    const result = modal.downloadSelection();
    if (result.kind === 'ok') {
      void window.openDownloadMissingModalForYouTube?.(
        result.virtualId,
        result.name,
        result.tracks,
      );
    } else {
      window.showToast?.(result.toast, result.level);
    }
  }, [modal]);

  return (
    <>
      <article className="dash-card dash-card--rail" data-card="listen">
        <div className="listen-band">
          <button
            type="button"
            className="listen-hero"
            onClick={() => void window.startLibraryRadio?.()}
          >
            <span className="listen-vinyl" aria-hidden="true" />
            <span className="listen-hero-text">
              <span className="listen-kicker">
                <span className="listen-eq" aria-hidden="true">
                  <i />
                  <i />
                  <i />
                  <i />
                  <i />
                </span>
                On air — your collection
              </span>
              <strong>Library Radio</strong>
              <span>Endless shuffle through your own collection — one click, infinite queue.</span>
            </span>
            <span className="listen-hero-play" aria-hidden="true">
              ▶
            </span>
          </button>
          <div className="listen-mixes-wrap">
            <QuickTilesGrid
              tiles={tiles}
              onOpenMix={modal.open}
              onPlayMix={handlePlayMix}
              onPlayFlow={handlePlayFlow}
              flowBusy={flowBusy}
              playingKey={playingKey}
              className="listen-mixes-grid"
              loading={mixes.mixes.length === 0}
            />
          </div>
        </div>
      </article>

      {modal.mix && (
        <MixModal
          mix={modal.mix}
          tracks={modal.tracks}
          loading={modal.loading}
          error={modal.error}
          selected={modal.selected}
          onClose={modal.close}
          onAction={runMixAction}
          onSelectAll={modal.selectAll}
          onClearSelection={modal.clearSelection}
          onToggleTrack={modal.toggleTrack}
          onPlayTrack={playTrackFromModal}
          playingIndex={playingTrackIndex}
          playing={playingKey === modal.mix.key}
          onDownloadSelected={handleDownloadSelected}
        />
      )}
    </>
  );
}

export function ListenBand() {
  const client = useContext(QueryClientContext);
  if (!client) {
    return (
      <QueryClientProvider client={getFallbackClient()}>
        <ListenBandInner />
      </QueryClientProvider>
    );
  }
  return <ListenBandInner />;
}
