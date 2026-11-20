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
  PodcastIcon,
  TvIcon,
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

export interface NavEntry {
  to: string;
  label: string;
  /** a shorter label for the phone tab bar */
  short?: string;
  icon: ReactNode;
  exact?: boolean;
  visible: (access: SectionAccess) => boolean;
}

export const NAV: NavEntry[] = [
  { to: '/player', label: 'Home', icon: <HomeIcon />, exact: true, visible: () => true },
  { to: '/player/music', label: 'Music', icon: <MusicIcon />, visible: (a) => a.music },
  { to: '/player/movies', label: 'Movies', icon: <MovieIcon />, visible: (a) => a.movies },
  { to: '/player/tv', label: 'TV', icon: <TvIcon />, visible: (a) => a.tv },
  { to: '/player/youtube', label: 'YouTube', icon: <YoutubeIcon />, visible: (a) => a.video },
  { to: '/player/podcasts', label: 'Podcasts', icon: <PodcastIcon />, visible: (a) => a.podcasts },
  {
    to: '/player/audiobooks',
    label: 'Audiobooks',
    short: 'Books',
    icon: <BookIcon />,
    visible: (a) => a.audiobooks,
  },
  {
    to: '/player/guide',
    label: 'Guide',
    icon: <GuideIcon />,
    visible: (a) => a.video || a.music,
  },
];
