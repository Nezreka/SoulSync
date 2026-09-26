import { useEffect, useState } from 'react';

import type { EnhancedAlbum, EnhancedTrack } from '../-artist-detail.enhanced';

import { RedownloadModal } from './redownload-modal';

/**
 * The tracks on one album the quality jobs say could be better, each one step
 * from the redownload modal in upgrade mode (only copies that reach the
 * profile's cutoff count as found).
 */
export function upgradableTracks(album: EnhancedAlbum): EnhancedTrack[] {
  return (album.tracks ?? []).filter((t) => t.quality_upgrade);
}

export function AlbumUpgradeModal({
  album,
  artistName,
  onReload,
  onClose,
}: {
  album: EnhancedAlbum;
  artistName: string;
  onReload: () => void;
  onClose: () => void;
}) {
  const [upgrading, setUpgrading] = useState<EnhancedTrack | null>(null);
  const tracks = upgradableTracks(album);

  useEffect(() => {
    if (upgrading) return undefined;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose, upgrading]);

  if (upgrading) {
    return (
      <RedownloadModal
        track={upgrading}
        album={album}
        artistName={artistName}
        onReload={onReload}
        onClose={() => setUpgrading(null)}
        upgrade
      />
    );
  }

  return (
    <div
      className="redownload-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="album-upgrade-title"
      onClick={(e) => {
        e.stopPropagation();
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="redownload-modal album-upgrade-modal">
        <div className="redownload-header">
          <div>
            <h3 id="album-upgrade-title">Could be better</h3>
            <p className="redownload-header-sub">
              {tracks.length} track{tracks.length === 1 ? '' : 's'} on{' '}
              {typeof album.title === 'string' ? album.title : 'this album'}{' '}
              {tracks.length === 1 ? 'is' : 'are'} below your quality profile. Pick one to find a
              copy that reaches it.
            </p>
          </div>
          <button className="redownload-close" type="button" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="redownload-body">
          {tracks.length === 0 ? (
            <div className="rdl-src-col-empty">Nothing on this album needs an upgrade.</div>
          ) : (
            <ul className="album-upgrade-list">
              {tracks.map((track) => {
                const hit = track.quality_upgrade as { current?: string };
                return (
                  <li className="album-upgrade-row" key={String(track.id)}>
                    <div className="album-upgrade-text">
                      <span className="album-upgrade-title">
                        {typeof track.title === 'string' && track.title ? track.title : 'Unknown'}
                      </span>
                      {hit.current ? (
                        <span className="album-upgrade-current">now {hit.current}</span>
                      ) : null}
                    </div>
                    <button
                      type="button"
                      className="redownload-btn primary album-upgrade-find"
                      onClick={() => setUpgrading(track)}
                    >
                      Find a better copy
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
