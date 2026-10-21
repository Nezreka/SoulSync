import type {
  ParsedWishlistTrack,
  WishlistAlbumGroup,
  WishlistArtistGroup,
} from '../-wishlist.types';

import {
  describeTrouble,
  failingTitle,
  groupTracks,
  orbImage,
  shortStamp,
  trackCountLabel,
} from '../-wishlist.helpers';
import { openWishlistInspector } from '../../../features/downloads/inspector-modal';
import { NebulaSheet } from './nebula-parts';

interface Props {
  group: WishlistArtistGroup;
  artistImages: Map<string, string>;
  onClose: () => void;
  onGrabArtist: () => void;
  onGrabAlbum: (album: WishlistAlbumGroup) => void;
  /** clear the backoff on these track ids and try them now. */
  onRetry: (ids: string[]) => void;
  onRemoveAlbum: (albumName: string) => void;
  onRemoveTrack: (trackId: string) => void;
  onRemoveArtist: () => void;
  /** a bulk action is in flight, the queue buttons stand down. */
  actionBusy?: boolean;
}

/** one sentence for the stuck tracks, so the panel says why before it lists. */
function troubleCallout(stuck: ParsedWishlistTrack[]): { headline: string; detail: string } {
  if (stuck.length === 1) {
    const [track] = stuck;
    const { headline, detail } = describeTrouble(track);
    const last = shortStamp(track.lastTried);
    return {
      headline,
      detail: `${detail} ${track.retry} tries${last ? `, last ${last}` : ''}.`,
    };
  }
  const counts = { rejected: 0, notfound: 0, failed: 0 };
  for (const track of stuck) {
    const kind = describeTrouble(track).kind;
    if (kind !== 'hunting') counts[kind] += 1;
  }
  const parts = [
    counts.rejected ? `${counts.rejected} came in bad and were rejected` : '',
    counts.notfound ? `${counts.notfound} had no match on your sources` : '',
    counts.failed ? `${counts.failed} kept failing to download` : '',
  ].filter(Boolean);
  return {
    headline: `${stuck.length} tracks stuck`,
    detail: `${parts.join(', ')}.`.replace(/^./, (c) => c.toUpperCase()),
  };
}

/**
 * the artist panel. opens beside the nebula instead of inside it, so the
 * field holds still. says what's wrong first, then lists what's wanted.
 */
export function WishlistArtistSheet({
  group,
  artistImages,
  onClose,
  onGrabArtist,
  onGrabAlbum,
  onRetry,
  onRemoveAlbum,
  onRemoveTrack,
  onRemoveArtist,
  actionBusy = false,
}: Props) {
  const image = orbImage(group, artistImages);
  const stuck = groupTracks(group).filter((track) => track.failing);
  const callout = stuck.length > 0 ? troubleCallout(stuck) : null;

  return (
    <NebulaSheet
      label={`${group.name} on the wishlist`}
      image={image}
      avatarFallback={group.name.substring(0, 2).toUpperCase()}
      name={group.name}
      meta={
        <>
          {trackCountLabel(group.total)} wanted
          {stuck.length > 0 ? (
            <span className="wlp-sheet-stuck"> · {stuck.length} stuck</span>
          ) : null}
        </>
      }
      callout={callout}
      onClose={onClose}
      actions={
        <>
          <button
            type="button"
            className="wlp-btn wlp-btn--primary wlp-sheet-primary"
            disabled={actionBusy}
            title={`Download all ${trackCountLabel(group.total)} by ${group.name} now`}
            aria-label={`Download all tracks by ${group.name} now`}
            onClick={onGrabArtist}
          >
            {group.total === 1 ? 'Grab now' : `Grab all ${group.total} now`}
          </button>
          {stuck.length > 0 ? (
            <button
              type="button"
              className="wlp-btn wlp-sheet-ghost"
              disabled={actionBusy}
              title="Clear the retry backoff so the next pass tries these again"
              onClick={() => onRetry(stuck.map((track) => track.id))}
            >
              Retry now
            </button>
          ) : null}
          <button
            type="button"
            className="wlp-btn wlp-sheet-ghost"
            onClick={() => window._navigateToArtistFromWishlist?.(group.name)}
          >
            Open artist
          </button>
        </>
      }
    >
      {group.albums.map((album) => (
        <section key={album.name} className="wlp-sheet-album" data-album={album.name}>
          <div className="wlp-sheet-album-head">
            {album.image ? (
              <img className="wlp-sheet-cover" src={album.image} alt="" />
            ) : (
              <div className="wlp-sheet-cover wlp-sheet-cover--ph" aria-hidden="true">
                ♪
              </div>
            )}
            <div className="wlp-sheet-album-id">
              <div className="wlp-sheet-album-name" title={album.name}>
                {album.name}
              </div>
              <div className="wlp-sheet-album-count">
                {trackCountLabel(album.tracks.length)} wanted
              </div>
            </div>
            <button
              type="button"
              className="wlp-sheet-chip"
              disabled={actionBusy}
              title={`Download "${album.name}" now`}
              aria-label={`Download album ${album.name} now`}
              onClick={() => onGrabAlbum(album)}
            >
              Grab
            </button>
            <button
              type="button"
              className="wlp-sheet-x"
              title="Remove album from the wishlist"
              aria-label={`Remove album ${album.name}`}
              onClick={() => onRemoveAlbum(album.name)}
            >
              ✕
            </button>
          </div>
          {album.tracks.map((track) => (
            <SheetTrack
              key={track.id || track.track}
              track={track}
              onRemove={() => onRemoveTrack(track.id)}
            />
          ))}
        </section>
      ))}

      {group.singles.length > 0 ? (
        <section className="wlp-sheet-album">
          <h3 className="wlp-sheet-label">Singles</h3>
          {group.singles.map((track) => (
            <SheetTrack
              key={track.id || track.track}
              track={track}
              withCover
              onRemove={() => onRemoveTrack(track.id)}
            />
          ))}
        </section>
      ) : null}

      <button
        type="button"
        className="wlp-sheet-remove"
        title={`Remove ${group.name} from the wishlist`}
        aria-label={`Remove ${group.name} from the wishlist`}
        onClick={onRemoveArtist}
      >
        Remove artist from wishlist
      </button>
    </NebulaSheet>
  );
}

function SheetTrack({
  track,
  withCover = false,
  onRemove,
}: {
  track: ParsedWishlistTrack;
  withCover?: boolean;
  onRemove: () => void;
}) {
  const trouble = describeTrouble(track);
  const pill = track.failing
    ? `${track.retry} tries`
    : track.retry > 0
      ? `Tried ${track.retry}×`
      : 'Waiting';
  const line = track.failing
    ? `${trouble.headline}${shortStamp(track.lastTried) ? ` · last ${shortStamp(track.lastTried)}` : ''}`
    : trouble.detail;
  return (
    <div className={`wlp-sheet-track${track.failing ? ' is-stuck' : ''}`}>
      {withCover ? (
        track.image ? (
          <img className="wlp-sheet-track-cover" src={track.image} alt="" />
        ) : (
          <div className="wlp-sheet-track-cover wlp-sheet-cover--ph" aria-hidden="true">
            ♪
          </div>
        )
      ) : null}
      <div className="wlp-sheet-track-main">
        <div className="wlp-sheet-track-top">
          <span className="wlp-sheet-track-name" title={track.track}>
            {track.track}
          </span>
          <span
            className={`wlp-sheet-pill${track.failing ? ' is-stuck' : ''}`}
            title={track.failing ? failingTitle(track) : undefined}
          >
            {pill}
          </span>
        </div>
        <div className="wlp-sheet-track-why" title={track.failReason || undefined}>
          {line}
        </div>
      </div>
      <button
        type="button"
        className="wlp-sheet-chip"
        title="Interactive Search — pick a source yourself"
        aria-label={`Interactive Search for ${track.track}`}
        onClick={() =>
          openWishlistInspector({
            id: track.id,
            name: track.track,
            artist: track.artist,
            album: track.album,
          })
        }
      >
        Search
      </button>
      <button
        type="button"
        className="wlp-sheet-x"
        title="Remove track"
        aria-label={`Remove ${track.track}`}
        onClick={onRemove}
      >
        ✕
      </button>
    </div>
  );
}
