/**
 * every settings section: where it sits in the list, what's inside, and
 * where its settings are saved (the same words the old badges used).
 */

import type { ReactNode } from 'react';

import {
  BellIcon,
  CamcorderIcon,
  DownloadIcon,
  GearIcon,
  GridIcon,
  GuideIcon,
  InfoIcon,
  ListIcon,
  MusicIcon,
  PlayIcon,
  SparkIcon,
  TvIcon,
} from '../icons';

export type SectionId =
  | 'playback'
  | 'picture'
  | 'subtitles'
  | 'music'
  | 'look'
  | 'sidebar'
  | 'notifications'
  | 'setup'
  | 'downloads'
  | 'converting'
  | 'guide'
  | 'issues';

export type SectionGroup = 'you' | 'server';

export interface SectionInfo {
  id: SectionId;
  group: SectionGroup;
  icon: ReactNode;
  title: string;
  /** what's inside, in the list */
  inside: string;
  /** one line under the title */
  description: string;
  /** where it is saved */
  scope: string;
}

export const SECTION_IDS: readonly SectionId[] = [
  'playback',
  'picture',
  'subtitles',
  'music',
  'look',
  'sidebar',
  'notifications',
  'setup',
  'downloads',
  'converting',
  'guide',
  'issues',
];

export const isSectionId = (v: unknown): v is SectionId =>
  typeof v === 'string' && (SECTION_IDS as readonly string[]).includes(v);

export const DEFAULT_SECTION: SectionId = 'playback';

const YOU: SectionInfo[] = [
  {
    id: 'playback',
    group: 'you',
    icon: <PlayIcon />,
    title: 'Playback',
    inside: 'Autoplay, still watching, sharing',
    description:
      'What happens when an episode ends, and whether others can see what you are playing.',
    scope: 'All screens',
  },
  {
    id: 'picture',
    group: 'you',
    icon: <TvIcon />,
    title: 'Picture',
    inside: 'Quality, shape, brightness, trailers',
    description: 'How video looks on this browser, TV or phone. Each screen keeps its own.',
    scope: 'This screen',
  },
  {
    id: 'subtitles',
    group: 'you',
    icon: <ListIcon />,
    title: 'Subtitles',
    inside: 'Size, dark box',
    description:
      'How subtitles look on this screen. A TV across the room may want them bigger than a laptop.',
    scope: 'This screen',
  },
  {
    id: 'music',
    group: 'you',
    icon: <MusicIcon />,
    title: 'Music',
    inside: 'Crossfade, gapless, visuals',
    description: 'How songs run into each other, what loads ahead, and the Now Playing backdrop.',
    scope: 'This screen',
  },
  {
    id: 'look',
    group: 'you',
    icon: <SparkIcon />,
    title: 'Look & feel',
    inside: 'Accent, poster cards, home rows',
    description: "Your highlight color, what movie and TV cards show, and your home page's rows.",
    scope: 'All screens',
  },
  {
    id: 'sidebar',
    group: 'you',
    icon: <GridIcon />,
    title: 'Sidebar',
    inside: "Your order and what's hidden",
    description:
      "Move the places in the sidebar and leave out the ones you don't use. A hidden place still opens from a link or search.",
    scope: 'This profile',
  },
  {
    id: 'notifications',
    group: 'you',
    icon: <BellIcon />,
    title: 'Notifications',
    inside: 'New episodes, films, quiet hours',
    description:
      'What the bell and your phone tell you about: new episodes of your shows, good films, requests and invites. Quiet hours and a daily digest keep it calm.',
    scope: 'This profile',
  },
];

const SERVER: SectionInfo[] = [
  {
    id: 'setup',
    group: 'server',
    icon: <GearIcon />,
    title: 'Setup & libraries',
    inside: 'Libraries, people, sidebar',
    description:
      'What everyone on this server sees: its name, its libraries, who sees which, and the sidebar.',
    scope: 'Admin',
  },
  {
    id: 'downloads',
    group: 'server',
    icon: <DownloadIcon />,
    title: 'Downloads',
    inside: 'Smaller copies, room',
    description:
      'Phones keep downloads, and the web player saves files to a computer. A smaller copy is made once by the server and kept for the next person who asks.',
    scope: 'Admin',
  },
  {
    id: 'converting',
    group: 'server',
    icon: <CamcorderIcon />,
    title: 'Converting',
    inside: 'GPU, how many at once',
    description:
      "Video a screen can't play is converted as it streams. Pick the GPU, how many run at once, and test it.",
    scope: 'Admin',
  },
  {
    id: 'guide',
    group: 'server',
    icon: <GuideIcon />,
    title: 'Live guide',
    inside: 'Retro commercial breaks',
    description:
      'Broadcast commercial breaks built into the Live Guide clock, so every viewer sees the same spots and comes back to the next show in sync.',
    scope: 'Server-wide',
  },
  {
    id: 'issues',
    group: 'server',
    icon: <InfoIcon />,
    title: 'Reported issues',
    inside: 'Broken files, bad metadata',
    description:
      'Broken files, sync issues or bad metadata flagged from movies, episodes, YouTube, music, podcasts and audiobooks.',
    scope: 'Admin queue',
  },
];

/** a member's own reports sit with their things; the queue is the admin's */
const MY_REPORTS: SectionInfo = {
  ...SERVER.find((x) => x.id === 'issues')!,
  group: 'you',
  inside: 'What you flagged',
  description:
    'Files and details you flagged as broken. An admin looks at each one and marks it fixed.',
  scope: 'My reports',
};

export function sectionsFor(isAdmin: boolean): SectionInfo[] {
  return isAdmin ? [...YOU, ...SERVER] : [...YOU, MY_REPORTS];
}

/** the section to show: the asked one when this person may see it */
export function pickSection(asked: SectionId | undefined, list: SectionInfo[]): SectionInfo {
  return list.find((x) => x.id === asked) ?? list.find((x) => x.id === DEFAULT_SECTION) ?? list[0];
}
