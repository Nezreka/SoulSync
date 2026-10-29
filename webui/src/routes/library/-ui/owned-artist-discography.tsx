import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import { acquireVerb, profileAsksFirst } from '@/platform/shell/download-rights';

import { artistDetailQueryOptions } from '../../artist-detail/-artist-detail.api';
import { DiscographyModal } from '../../artist-detail/-ui/discography-modal';

// dev's download / request discography modal on the owned view: type filters,
// select all/none, future releases, download + watch, retry failed, process
// wishlist now. a profile that can't download gets request discography, the
// way dev's hero did. the release list is the provider's full discography.

const PROVIDER_ORDER = ['spotify', 'deezer', 'itunes', 'musicbrainz', 'discogs'] as const;

export function preferredProviderIdentity(
  providerIds: Partial<Record<string, string>> | undefined,
): { source: string; id: string } | null {
  for (const source of PROVIDER_ORDER) {
    const id = providerIds?.[source];
    if (id) return { source, id: String(id) };
  }
  return null;
}

export function OwnedArtistDiscographyButton({
  artistId,
  artistName,
  imageUrl,
  providerIds,
}: {
  artistId: number;
  artistName: string;
  imageUrl: string;
  providerIds: Partial<Record<string, string>> | undefined;
}) {
  const [open, setOpen] = useState(false);
  const identity = preferredProviderIdentity(providerIds);
  const detail = useQuery({
    ...artistDetailQueryOptions(identity?.source ?? '', identity?.id ?? '', artistName),
    enabled: open && identity != null,
  });
  if (!identity) return null;
  const asksFirst = profileAsksFirst();
  const payload = detail.data?.success === false ? null : detail.data;
  const failure = detail.isError
    ? (detail.error as Error)?.message
    : detail.data?.success === false
      ? detail.data.error
      : null;
  return (
    <>
      <button
        type="button"
        className="discog-download-btn discog-btn-compact"
        data-request-action={asksFirst ? '' : undefined}
        disabled={open && detail.isFetching}
        onClick={() => setOpen(true)}
      >
        <span className="discog-btn-icon">⬇</span>
        <span className="discog-btn-text">
          {open && detail.isFetching ? 'Loading discography…' : `${acquireVerb()} Discography`}
        </span>
      </button>
      {open && failure !== null ? (
        <span role="alert">{failure || 'Could not load the discography'}</span>
      ) : null}
      {open && payload?.discography ? (
        <DiscographyModal
          libraryArtistId={artistId}
          artistName={artistName}
          artistImage={imageUrl}
          discography={payload.discography}
          onClose={() => setOpen(false)}
          watchlistIdentity={{ id: identity.id, name: artistName, source: identity.source }}
        />
      ) : null}
    </>
  );
}
