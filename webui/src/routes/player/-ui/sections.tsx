/**
 * the player's sections and who sees them. a section follows the profile's
 * media access (the same permission rows the manager uses), so a music-only
 * profile never sees a Movies entry, and a page it can't open is never a
 * link. the server refuses anyway; this keeps the nav honest.
 */

import type { ReactNode } from 'react';

import { useHasAccess } from '@/platform/shell/my-permissions';

import {
  BookIcon,
  GuideIcon,
  HomeIcon,
  MovieIcon,
  MusicIcon,
  PlaylistIcon,
  PodcastIcon,
  TvIcon,
  WatchlistIcon,
  YoutubeIcon,
} from './icons';

export interface SectionAccess {
  music: boolean;
  movies: boolean;
  tv: boolean;
  /** youtube and the video catalog ride on either video kind */
  video: boolean;
  podcasts: boolean;
  audiobooks: boolean;
}

export function useSections(): SectionAccess {
  const music = useHasAccess('music');
  const movies = useHasAccess('movies');
  const tv = useHasAccess('tv');
  const podcasts = useHasAccess('podcasts');
  const audiobooks = useHasAccess('books');
  return { music, movies, tv, video: movies || tv, podcasts, audiobooks };
}

export type NavGroup = 'main' | 'watch' | 'listen';

export const NAV_GROUPS: { id: NavGroup; label?: string }[] = [
  { id: 'main' },
  { id: 'watch', label: 'Watch' },
  { id: 'listen', label: 'Listen' },
];

export interface NavEntry {
  to: string;
  label: string;
  group: NavGroup;
  /** a shorter label for the phone tab bar */
  short?: string;
  icon: ReactNode;
  exact?: boolean;
  /** other paths that belong to it (a single playlist's page is under Playlists) */
  also?: string[];
  visible: (access: SectionAccess) => boolean;
}

export const NAV: NavEntry[] = [
  {
    to: '/player',
    label: 'Home',
    group: 'main',
    icon: <HomeIcon />,
    exact: true,
    visible: () => true,
  },
  {
    to: '/player/guide',
    label: 'Guide',
    group: 'main',
    icon: <GuideIcon />,
    visible: (a) => a.video || a.music,
  },
  {
    to: '/player/watchlist',
    label: 'Watchlist',
    group: 'main',
    icon: <WatchlistIcon />,
    visible: (a) => a.video,
  },
  {
    to: '/player/movies',
    label: 'Movies',
    group: 'watch',
    icon: <MovieIcon />,
    visible: (a) => a.movies,
  },
  { to: '/player/tv', label: 'TV', group: 'watch', icon: <TvIcon />, visible: (a) => a.tv },
  {
    to: '/player/youtube',
    label: 'YouTube',
    group: 'watch',
    icon: <YoutubeIcon />,
    visible: (a) => a.video,
  },
  {
    to: '/player/music',
    label: 'Music',
    group: 'listen',
    icon: <MusicIcon />,
    visible: (a) => a.music,
  },
  {
    to: '/player/music/playlists',
    label: 'Playlists',
    group: 'listen',
    also: ['/player/music/playlist'],
    icon: <PlaylistIcon />,
    visible: (a) => a.music,
  },

  {
    to: '/player/podcasts',
    label: 'Podcasts',
    group: 'listen',
    icon: <PodcastIcon />,
    visible: (a) => a.podcasts,
  },
  {
    to: '/player/audiobooks',
    label: 'Audiobooks',
    group: 'listen',
    short: 'Books',
    icon: <BookIcon />,
    visible: (a) => a.audiobooks,
  },
];
