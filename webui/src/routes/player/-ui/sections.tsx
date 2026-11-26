/**
 * the player's sections and who sees them. a section follows the profile's
 * media access (the same permission rows the manager uses), so a music-only
 * profile never sees a Movies entry, and a page it can't open is never a
 * link. the server refuses anyway; this keeps the nav honest.
 */

import type { ReactNode } from 'react';

import { useHasAccess, useMyPermissions } from '@/platform/shell/my-permissions';

import {
  BookIcon,
  GuideIcon,
  HomeIcon,
  MovieIcon,
  MusicIcon,
  PeopleIcon,
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
  isAdmin: boolean;
}

export function useSections(): SectionAccess {
  const music = useHasAccess('music');
  const movies = useHasAccess('movies');
  const tv = useHasAccess('tv');
  const podcasts = useHasAccess('podcasts');
  const audiobooks = useHasAccess('books');
  const { isAdmin } = useMyPermissions();
  return { music, movies, tv, video: movies || tv, podcasts, audiobooks, isAdmin };
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
  /** one of the phone's few tabs; the rest sit under More */
  tab?: boolean;
  visible: (access: SectionAccess) => boolean;
}

export const NAV: NavEntry[] = [
  {
    to: '/player',
    label: 'Home',
    group: 'main',
    icon: <HomeIcon />,
    exact: true,
    tab: true,
    visible: () => true,
  },
  {
    to: '/player/guide',
    label: 'Guide',
    group: 'main',
    icon: <GuideIcon />,
    tab: true,
    visible: (a) => a.video || a.music,
  },
  {
    to: '/player/calendar',
    label: 'Calendar',
    group: 'main',
    icon: (
      <svg
        width="18"
        height="18"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <rect x="3" y="4" width="18" height="18" rx="2" ry="2" />
        <line x1="16" y1="2" x2="16" y2="6" />
        <line x1="8" y1="2" x2="8" y2="6" />
        <line x1="3" y1="10" x2="21" y2="10" />
      </svg>
    ),
    visible: (a) => a.video,
  },
  {
    to: '/player/watchlist',
    label: 'Watchlist',
    group: 'main',
    icon: <WatchlistIcon />,
    visible: (a) => a.video,
  },
  {
    to: '/player/watching',
    label: 'Watching now',
    short: 'Now',
    group: 'main',
    icon: <PeopleIcon />,
    visible: () => true,
  },

  {
    to: '/player/movies',
    label: 'Movies',
    group: 'watch',
    icon: <MovieIcon />,
    tab: true,
    visible: (a) => a.movies,
  },
  {
    to: '/player/tv',
    label: 'TV',
    group: 'watch',
    icon: <TvIcon />,
    tab: true,
    visible: (a) => a.tv,
  },
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
    tab: true,
    visible: (a) => a.music,
  },
  {
    to: '/player/music/playlists',
    label: 'Playlists',
    group: 'listen',
    also: ['/player/music/playlist', '/player/music/discover'],
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
