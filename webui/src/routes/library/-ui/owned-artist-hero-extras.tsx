import { useQuery } from '@tanstack/react-query';

import { apiClient, readJson } from '@/app/api-client';

import type { ArtistInfo } from '../../artist-detail/-artist-detail.types';

import { LIBRARY_V2_QUERY_KEY } from '../-library-v2.api';
import { buildGenreChips } from '../../artist-detail/-artist-detail.hero-stats';
import { ArtistDbRecord } from '../../artist-detail/-ui/artist-db-record';

// what dev's hero showed and the owned view lost: the db record viewer,
// last.fm tag chips next to the genres, and the format tags.

interface RecordPayload {
  formats?: string[];
  record?: { enrichment?: { lastfm?: { tags?: unknown } } | null } & Record<string, unknown>;
}

export function lastfmTagsOf(payload: RecordPayload | undefined): unknown {
  return payload?.record?.enrichment?.lastfm?.tags ?? payload?.record?.lastfm_tags ?? null;
}

export function OwnedArtistHeroExtras({
  artistId,
  artistName,
  genres,
}: {
  artistId: number;
  artistName: string;
  genres: string[];
}) {
  const recordQuery = useQuery({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'artist-record', artistId],
    queryFn: () => readJson<RecordPayload>(apiClient.get(`artist/${artistId}/record`)),
    staleTime: 5 * 60_000,
  });
  const lastfmChips = buildGenreChips({
    genres,
    lastfm_tags: lastfmTagsOf(recordQuery.data),
  } as ArtistInfo).filter((chip) => chip.fromLastfm);
  const formats = recordQuery.data?.formats ?? [];
  return (
    <>
      <ArtistDbRecord
        artist={{ id: artistId, name: artistName } as ArtistInfo}
        isSourceArtist={false}
      />
      {lastfmChips.length ? (
        <div className="artist-genres-container" aria-label="Last.fm tags">
          {lastfmChips.map((chip) => (
            <span key={chip.label} className="genre-tag" style={{ opacity: 0.6 }}>
              {chip.label}
            </span>
          ))}
        </div>
      ) : null}
      {formats.length ? (
        <div className="artist-formats" aria-label="Formats">
          {formats.map((format) => (
            <span className="artist-format-tag" key={format}>
              {format}
            </span>
          ))}
        </div>
      ) : null}
    </>
  );
}
