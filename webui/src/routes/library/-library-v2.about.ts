import type { ArtistAboutSource } from '../artist-detail/-artist-detail.types';
import type { ArtistHeroStats } from './-library-v2.api';
import type { LibraryV2ArtistDetail } from './-library-v2.types';

export interface CatalogueAbout {
  bio: string | null;
  bioSource: ArtistAboutSource | null;
  bioUrl: string | null;
  listeners: number | null;
  playcount: number | null;
  numbersSource: ArtistAboutSource | null;
  followers: number | null;
  fans: number | null;
}

/**
 * the hero's bio and numbers for a catalogue artist, each from the first
 * source that has it: the media server's summary or live last.fm first, then
 * what enrichment stored (last.fm, then the free sources). numbers never mix
 * sources, and a free source's bio carries its note.
 */
export function catalogueAbout(
  artist: Pick<LibraryV2ArtistDetail, 'summary' | 'about'>,
  live: ArtistHeroStats | undefined,
): CatalogueAbout {
  const about = artist.about ?? null;
  const liveBio = artist.summary || live?.bio || null;
  const storedBio = liveBio ? null : (about?.bio ?? null);
  const bioSource =
    storedBio && about?.bio_source !== 'lastfm' ? (about?.bio_source ?? null) : null;
  const liveNumbers = Boolean(live?.listeners || live?.playcount);
  const stored = !liveNumbers && Boolean(about?.popularity_source);
  return {
    bio: liveBio || storedBio,
    bioSource,
    bioUrl: bioSource ? (about?.bio_url ?? null) : null,
    listeners: stored ? (about?.listeners ?? null) : (live?.listeners ?? null),
    playcount: stored ? (about?.plays ?? null) : (live?.playcount ?? null),
    numbersSource: stored ? (about?.popularity_source ?? null) : null,
    followers: live?.followers ?? null,
    fans: live?.followers ? null : (about?.fans ?? null),
  };
}
