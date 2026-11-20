import { useQuery } from '@tanstack/react-query';

import { apiClient, readJson } from '@/app/api-client';

import type { ArtistAbout, ArtistInfo } from '../../artist-detail/-artist-detail.types';

import { LIBRARY_V2_QUERY_KEY } from '../-library-v2.api';
import { artistTags, buildGenreChips } from '../../artist-detail/-artist-detail.hero-stats';
import { AboutSourceNote } from '../../artist-detail/-ui/about-source-note';
import { ArtistDbRecord } from '../../artist-detail/-ui/artist-db-record';

// what dev's hero showed and the owned view lost: the db record viewer,
// last.fm tag chips next to the genres, and the format tags. without last.fm
// tags the chips are musicbrainz's, with a quiet note saying so.

interface RecordPayload {
  formats?: string[];
  record?: { enrichment?: { lastfm?: { tags?: unknown } } | null } & Record<string, unknown>;
}

export function lastfmTagsOf(payload: RecordPayload | undefined): unknown {
  return payload?.record?.enrichment?.lastfm?.tags ?? payload?.record?.lastfm_tags ?? null;
}

const TAG_LABELS = { lastfm: 'Last.fm tags', musicbrainz: 'MusicBrainz tags' } as const;

export function OwnedArtistHeroExtras({
  artistId,
  artistName,
  genres,
  about,
}: {
  artistId: number;
  artistName: string;
  genres: string[];
  /** the artist payload's about block: the fallback tags when last.fm has none */
  about?: ArtistAbout | null;
}) {
  const recordQuery = useQuery({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'artist-record', artistId],
    queryFn: () => readJson<RecordPayload>(apiClient.get(`artist/${artistId}/record`)),
    staleTime: 5 * 60_000,
  });
  const info = {
    genres,
    lastfm_tags: lastfmTagsOf(recordQuery.data),
    about,
  } as ArtistInfo;
  const tagChips = buildGenreChips(info).filter((chip) => chip.source !== 'genre');
  const tagsSource = artistTags(info).source;
  const formats = recordQuery.data?.formats ?? [];
  return (
    <>
      <ArtistDbRecord
        artist={{ id: artistId, name: artistName } as ArtistInfo}
        isSourceArtist={false}
      />
      {tagChips.length && tagsSource ? (
        <div className="artist-genres-container" aria-label={TAG_LABELS[tagsSource]}>
          {tagChips.map((chip) => (
            <span key={chip.label} className="genre-tag" style={{ opacity: 0.6 }}>
              {chip.label}
            </span>
          ))}
        </div>
      ) : null}
      {tagChips.length ? <AboutSourceNote tagsSource={tagsSource} /> : null}
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
