/**
 * The three tours (plan 4.9): Getting around, Your first download and Your
 * first video request. Every step points at a data-tour anchor
 * (anchors.ts), never a styling class.
 *
 * A step whose anchor isn't on screen is skipped (once a page has had time
 * to load), so a member, a phone or a page with nothing on it yet never
 * gets a card floating over nothing. `optional` steps only wait briefly:
 * they are there when the user has done the thing (searched, opened a
 * window), and quietly absent when not.
 */

import type { AnchorId } from './anchors';

export interface TourContext {
  isAdmin: boolean;
  /** downloads straight away; false means their asks go to the admin */
  canDownload: boolean;
  mac: boolean;
  phone: boolean;
}

export type Copy = string | ((ctx: TourContext) => string);

export interface TourStep {
  anchor: AnchorId;
  /** the page it lives on (help/page.ts ids); the tour goes there first. none = wherever you are */
  page?: string;
  title: Copy;
  body: Copy;
  /** there only when the user has done something: a short wait, then skipped */
  optional?: boolean;
  /** when this anchor shows up (the user clicked through), move on by itself */
  advanceOn?: AnchorId;
  /** something on screen has to go first (an open window): the card says so and waits */
  waitUntilGone?: { selector: string; say: string };
}

export interface Tour {
  id: string;
  title: string;
  /** "6 steps, about a minute": the row under Read more */
  detail: string;
  /** pages whose help offers this tour */
  pages: string[];
  steps: TourStep[];
}

const modKey = (ctx: TourContext) => (ctx.mac ? '⌘K' : 'Ctrl K');

export const TOURS: Tour[] = [
  {
    id: 'getting-around',
    title: 'Getting around',
    detail: 'The sidebar, both sides and help, in a minute',
    pages: ['dashboard', 'video-dashboard', 'help', 'video-help'],
    steps: [
      {
        anchor: 'shell.menu',
        optional: true,
        title: 'Every page is in here',
        body: 'Tap the menu to see every page, grouped by what it does. Tap one to go there.',
      },
      {
        anchor: 'shell.nav',
        title: 'Every page has a place',
        body: 'Collect finds new things, Manage is what you already have, and System keeps it all running. Click a group’s name to fold it away.',
      },
      {
        anchor: 'shell.side-switch',
        title: 'Music and video',
        body: 'SoulSync runs both. Switch sides here: each side has its own pages and remembers where you were.',
      },
      {
        anchor: 'shell.profile',
        optional: true,
        title: 'Who is listening',
        body: 'Your profile. Click it to switch profiles or change your own account.',
      },
      {
        anchor: 'shell.activity',
        optional: true,
        title: 'Server Activity',
        body: 'What is playing on Plex and Jellyfin right now, and why a stream converts.',
      },
      {
        anchor: 'shell.notifications',
        title: 'Notifications',
        body: 'Jobs that are running, and anything SoulSync wants you to know when they finish.',
      },
      {
        anchor: 'shell.version',
        optional: true,
        title: 'What changed',
        body: 'The version you run. Click it for the release notes.',
      },
      {
        anchor: 'shell.help',
        title: 'Help, from anywhere',
        body: (ctx) =>
          ctx.phone
            ? 'Help knows the page you are on and what SoulSync is doing. It has this tour and the others too.'
            : `Press ? or F1 on any page. Help knows the page you are on, and ${modKey(ctx)} jumps straight to search.`,
      },
    ],
  },
  {
    id: 'first-download',
    title: 'Your first download',
    detail: 'Search, pick, download, in about a minute',
    pages: ['search', 'active-downloads'],
    steps: [
      {
        anchor: 'search.field',
        page: 'search',
        title: 'Search for music',
        body: 'Type an artist, album or track, or paste a link to one. Results show as you type.',
      },
      {
        anchor: 'search.modes',
        page: 'search',
        title: 'What you are looking for',
        body: 'Catalog finds releases and downloads them tagged and filed. Files searches your download sources directly, so you can pick the exact file.',
      },
      {
        anchor: 'search.source',
        page: 'search',
        title: 'Where results come from',
        body: 'Your main source answers first. Pick another one here, or All sources to ask every one at once.',
      },
      {
        anchor: 'search.results',
        page: 'search',
        optional: true,
        advanceOn: 'download.window',
        title: 'Pick one',
        body: 'Click an album or a track to open its download window. The tour waits for you, or press Next.',
      },
      {
        anchor: 'download.window',
        optional: true,
        title: 'The download window',
        body: 'Every track, and whether you already have it. Nothing downloads until you start it.',
      },
      {
        anchor: 'download.start',
        optional: true,
        title: 'Start it',
        body: 'Begin Analysis checks what you already own, then downloads the rest. You can close the window, it keeps going.',
      },
      {
        anchor: 'downloads.header',
        page: 'active-downloads',
        waitUntilGone: {
          selector: '.download-missing-modal',
          say: 'Close the download window and the tour carries on.',
        },
        title: 'Follow it in Downloads',
        body: 'Everything downloading, queued and finished, for music and video. Finished downloads land in your library, tagged and filed.',
      },
    ],
  },
  {
    id: 'first-video-request',
    title: 'Your first video request',
    detail: 'Find a movie or show and ask for it',
    pages: ['video-search', 'video-discover', 'video-requests'],
    steps: [
      {
        anchor: 'video.search-field',
        page: 'video-search',
        title: 'Find a movie or show',
        body: 'Type a title or a name. A title you have opens its library page, anything else opens a preview.',
      },
      {
        anchor: 'video.search-modes',
        page: 'video-search',
        title: 'Titles, Releases, Fresh',
        body: 'Titles finds movies, shows and people. Releases searches your indexers for an exact release. Fresh is what is new right now.',
      },
      {
        anchor: 'video.search-results',
        page: 'video-search',
        optional: true,
        advanceOn: 'video.title-actions',
        title: 'Open a title',
        body: 'Click a poster to open its page. The tour waits for you, or press Next.',
      },
      {
        anchor: 'video.title-actions',
        optional: true,
        title: (ctx) => (ctx.canDownload ? 'Get it' : 'Ask for it'),
        body: (ctx) =>
          ctx.canDownload
            ? 'Get grabs it now from your sources. Wishlist keeps looking until a release turns up.'
            : 'Request sends it to your admin. You hear when it is approved, and the page shows how far along it is.',
      },
      {
        anchor: 'video.nav-discover',
        title: 'Or browse',
        body: 'Discover has what is trending, by genre and by list. Every poster opens the same page.',
      },
      {
        anchor: 'video.requests',
        page: 'video-requests',
        title: 'Requests',
        body: (ctx) =>
          ctx.isAdmin
            ? 'What your household asked for. Approve once and everyone who asked hears about it.'
            : 'Everything you asked for, and where it stands.',
      },
    ],
  },
];

export function tourById(id: string): Tour | undefined {
  return TOURS.find((t) => t.id === id);
}

export function copy(value: Copy, ctx: TourContext): string {
  return typeof value === 'function' ? value(ctx) : value;
}
