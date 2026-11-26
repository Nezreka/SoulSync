/**
 * The release notes, in one place (plan 4.7). Two readers:
 *
 *   - What's new in the help sheet (whats-new.tsx): `items`, newest first,
 *     each with Show me when it has a page or an anchor.
 *   - The version modal behind the sidebar's version button (downloads.js
 *     showVersionInfo): the highlight sections built by modalSections().
 *
 * The vanilla side reads them from window.SoulSyncReleaseNotes, which
 * installReleaseNotes() publishes (and announces with ss:release-notes-ready).
 * mountHelpHost() calls it as the bundle starts, so the version button can be
 * clicked before anyone opens the help sheet.
 *
 * At release time, edit only this file:
 *   1. Replace RELEASES[0] with the new version: its month, headline,
 *      summary and features (the modal), and its items (What's new, one per
 *      shipped thing, from pr.md).
 *   2. Fold the previous release into `earlier` (one line in What's new,
 *      one "Earlier in X.Y.Z" section in the modal). Keep the current
 *      release only, never a growing backlog.
 * A release whose version is above the running build stays hidden until the
 * build catches up, so notes can land before the version bump.
 *
 * No em or en dashes in the copy.
 */

export interface ReleaseItem {
  title: string;
  desc: string;
  /** a page id (help/page.ts), e.g. 'library', 'video-discover': Show me opens it */
  page?: string;
  /** a data-tour anchor (tours/anchors.ts): Show me spotlights it */
  anchor?: string;
}

export interface ModalSection {
  title: string;
  description: string;
  features: string[];
  usage_note?: string;
}

export interface Release {
  version: string;
  /** "November 2026" */
  month: string;
  /** one line, sentence case: the What's new header and the modal's first title */
  headline: string;
  /** the modal's paragraph under the headline */
  summary: string;
  /** the modal's bullets */
  features: string[];
  usage_note?: string;
  /** What's new, one per shipped thing */
  items: ReleaseItem[];
  /** the releases before this one, in a line each place */
  earlier?: {
    /** What's new's last row */
    desc: string;
    /** the modal's last section, "Earlier in 3.6.6" */
    version: string;
    summary: string;
  };
}

export const RELEASES: Release[] = [
  {
    version: '3.7.0',
    month: 'November 2026',
    headline: 'Player libraries, rows you arrange and a backups page',
    summary:
      'The player has a first-run setup, libraries you name and build from your folders, and pages whose rows each profile arranges. Video can run with no media server, or with Plex and Jellyfin as one library. Backups have their own page and cover every database, and downloads got a hard pass on matching and on never losing a file.',
    features: [
      'Player setup: an admin names the server, sorts folders into libraries and sets the sidebar. Run it again from the player’s settings.',
      'Libraries: a named group of folders (4K, Kids, Anime) with its own page in the sidebar, which can be hidden from a profile.',
      'Rows you arrange: Movies, TV, Music and YouTube each have a layout per profile, with extra rows to switch on and a sidebar in your own order.',
      'The SoulSync Player app for Android phones, tablets and TV, with downloads you can take offline: pair it from Settings, Devices. Scan the QR there with your phone’s camera to pair. Get it at github.com/Nezreka/soulsync-mobile-app/releases/latest.',
      'Player downloads: pick the quality, download a whole season or album, smart downloads, and every device’s downloads on the Downloads page.',
      'Chapters and Skip intro for video, from the file’s own chapter marks, and a title’s logo on its page.',
      'Watch parties with a waiting room, countdown and reactions, and Chromecast that plays anything.',
      'Player streams show in Server Activity beside Plex and Jellyfin, recorded in history, and an admin can stop one with a message.',
      'Identify a song by listening, from the search bar (thanks Jygijiji, #2307).',
      'The SoulSync Companion extension 0.3.0: badges check a whole page in one request, short and accented artist names match, and SoundCloud uploads can be added exactly as they are.',
      'SoulSync Standalone: video with no media server, scanning your movie and TV folders itself. A download is in the library the moment it lands.',
      'Plex and Jellyfin as one library (off by default), extras on a title’s page, and YouTube channels as libraries you manage.',
      'One Backups page: every database is backed up and restorable, a restore checks the backup first, and a damaged database can be repaired. Keep 5 keeping only one is fixed.',
      'A playlist’s missing songs are one request (#2302), members can ask for a feature (#2304), and a member no longer sees the server’s connected playlists (#2301).',
      'Search public playlists by name (#2305), sync several playlists at once with or without downloading (#2309), and songs only YouTube has download from their own video (#2306).',
      'Downloads: four ways a file could be lost are closed, tribute acts and wrong artists are refused, a track tries eight peers, and deep verify can catch fake lossless (off by default).',
      'A file is known by its name and size when its path changed (#2288, #2290), and two artists who share a name are two artists.',
      'Exact release matching and edition track counts (thanks EffakT, #2294 to #2298), Lidarr and slskd fixes and less memory (thanks savemosca, #2251 to #2263), Jellyfin fixes (thanks PapaBearDoes, #2243, #2244).',
      'Tidal playlist ids (thanks jonjomckay, #2269), CJK paths (thanks adityaanikam, #2266), deleted quarantine left out of scans (thanks TaunGadd, #2286), and fixes from #2248 to #2295.',
    ],
    items: [
      {
        title: 'Player setup',
        desc: 'The first time an admin opens the player it asks a few things: the server’s name, which folders make which library, the sidebar’s order and what is hidden for everyone. Run it again from the player’s settings.',
        anchor: 'shell.open-player',
      },
      {
        title: 'Libraries',
        desc: 'A library is a named group of folders. You start with Movies and TV Shows and can move a drive into a new one (4K, Kids, Anime), rename it and give it its own page. A library hidden from a profile is gone from that profile’s search, rows, guide and links.',
      },
      {
        title: 'Rows you arrange',
        desc: 'Movies, TV, Music and YouTube each have a Customize button: reorder, hide and restyle the rows, for this screen or all of them. Each page has extra rows that are off until you want them, and the sidebar takes your own order too.',
      },
      {
        title: 'Chapters and Skip intro',
        desc: 'A movie or episode’s own chapter marks show in the chapter menu. When the file names a chapter intro, recap or credits, a skip button shows while it plays. Nothing is guessed.',
      },
      {
        title: 'Watch parties and casting',
        desc: 'Start a watch party from any title: a waiting room with seats, a synced countdown, reactions on screen and a break button. Chromecast plays anything, converting what it can’t decode.',
      },
      {
        title: 'Video without a media server',
        desc: 'Pick SoulSync Standalone as the video source and SoulSync scans your movie and TV folders itself, reads editions and watches for changes. A download is in the library the moment it lands.',
        page: 'settings',
      },
      {
        title: 'Plex and Jellyfin as one library',
        desc: 'With both connected, the same file is linked across them and watched state goes both ways. Off by default, on the video Connections card.',
        page: 'settings',
      },
      {
        title: 'Extras and channel libraries',
        desc: 'Trailers, featurettes and deleted scenes beside a title are listed on its page and play in place. A YouTube channel’s page shows its files, what needs attention, delete with undo and clean now.',
      },
      {
        title: 'Backups',
        desc: 'Settings > System > Backups lists every backup and what it holds. The player and audiobook databases are backed up with the music one, the key file can be downloaded, and a restore checks the backup before it replaces anything. Keep 5 keeping only one is fixed.',
        page: 'settings',
      },
      {
        title: 'A playlist is one request',
        desc: 'The songs a playlist sync asks for arrive as one request under the playlist’s name. Approve or decline it whole, or open its tracks and answer song by song (#2302).',
      },
      {
        title: 'Ask for a feature',
        desc: 'A member’s account lists what else the server has, each with an Ask button. An admin answers at the top of Requests and approving switches it on (#2304, #2303).',
      },
      {
        title: 'Connected accounts stay yours',
        desc: 'A member who hasn’t connected their own Spotify, Tidal, Deezer, Qobuz or YouTube Music no longer sees the server’s playlists. Settings > System > Security has a switch to share them (#2301).',
        page: 'settings',
      },
      {
        title: 'Search public playlists',
        desc: 'In Add Playlist, type a name instead of pasting a link and pick from public playlists on Deezer and Spotify (#2305). Playlists also take the source’s artwork or your own cover (#2279 to #2281).',
        page: 'sync',
      },
      {
        title: 'Downloads that keep your files',
        desc: 'Four ways a download could destroy or strand a file are closed, a failed slskd check no longer cancels everything, tribute acts and wrong artists are refused, and a track tries eight peers before giving up.',
      },
      {
        title: 'The SoulSync Player app',
        desc: 'Android phones, tablets and TV get the player as an app: the same pages, libraries and rows as the web player, a TV home built for a remote, and Chromecast. Pair it from Settings, Devices; 0.1.0 is on the soulsync-mobile-app GitHub releases.',
        page: 'settings',
      },
      {
        title: 'Server Activity for the player',
        desc: 'Streams from SoulSync’s own player show beside Plex and Jellyfin, are recorded in history and stats, and can be stopped with a message, or sent a message without stopping.',
      },
      {
        title: 'Repair a damaged database',
        desc: 'Backups never copy a damaged database, and now every admin is told when that happens. The Backups page shows the database’s health and a Repair button that keeps the damaged files, rebuilds, checks and swaps in, or offers the newest healthy backup.',
        page: 'settings',
      },
      {
        title: 'Sync several playlists at once',
        desc: 'Select playlists on the Sync page and choose Sync (on your server with the songs you have) or Sync & download. They run one after another with a status for each (#2309). A playlist’s own window has Sync only too.',
        page: 'sync',
      },
      {
        title: 'Identify a song',
        desc: 'The search bar can listen to a song playing near you, or take a clip, and find it through Shazam (thanks Jygijiji, #2307).',
        page: 'search',
      },
      {
        title: 'Fake lossless check',
        desc: 'Deep verify can catch a FLAC made from an MP3. Off by default, in Settings > Downloads > Quality.',
        page: 'settings',
      },
      {
        title: 'Files known by name and size',
        desc: 'A file whose path isn’t the one on record is still recognised (#2288, #2290), a library moved between mounts is settled (#2289), and two artists who share a name are two artists.',
        page: 'library',
      },
      {
        title: 'Fixes from your reports and PRs',
        desc: 'Release matching and edition counts (thanks EffakT), Lidarr, slskd and memory (thanks savemosca), Jellyfin (thanks PapaBearDoes), Tidal ids (thanks jonjomckay), CJK paths (thanks adityaanikam), quarantine scans (thanks TaunGadd), and #2248 to #2295.',
      },
    ],
    earlier: {
      desc: '3.6.9 brought the player, settings rebuilt and music apps. 3.6.8 brought Last.fm without Pro and Shelf Life. 3.6.2 brought the new Library, built by @nick2000713.',
      version: '3.6.9',
      summary:
        'SoulSync got its own player for everything you own, settings were rebuilt on nine tabs with one search, and any Subsonic app could play from SoulSync as a profile.',
    },
  },
];

// ── versions ────────────────────────────────────────────────────────────

/** "3.6.7" vs "3.6.10" vs "3.6.7+abc": negative when a is older. Missing parts are 0. */
export function compareVersions(
  a: string | null | undefined,
  b: string | null | undefined,
): number {
  const parse = (s: string | null | undefined) =>
    String(s || '0')
      .replace(/^v/i, '')
      .split('+')[0]
      .split('.')
      .map((n) => parseInt(n, 10) || 0);
  const pa = parse(a);
  const pb = parse(b);
  for (let i = 0; i < Math.max(pa.length, pb.length); i += 1) {
    const diff = (pa[i] || 0) - (pb[i] || 0);
    if (diff !== 0) return diff;
  }
  return 0;
}

/** newest first, without any release still above the running build */
export function releasedNotes(build?: string | null): Release[] {
  const sorted = [...RELEASES].sort((a, b) => compareVersions(b.version, a.version));
  if (!build) return sorted;
  return sorted.filter((r) => compareVersions(r.version, build) <= 0);
}

/** the release the running build should talk about; null only with an empty file */
export function currentRelease(build?: string | null): Release | null {
  return releasedNotes(build)[0] ?? RELEASES[0] ?? null;
}

/** the version the sidebar's button shows, "v3.6.7" → "3.6.7" */
export function buildVersionFromPage(): string | null {
  if (typeof document === 'undefined') return null;
  const text = document.querySelector('.version-button')?.textContent?.trim() ?? '';
  return /^v?\d/.test(text) ? text.replace(/^v/i, '') : null;
}

// ── the version modal's sections ────────────────────────────────────────

/** "Settings in React, ..." → "3.6.7: settings in React, ..." (the modal's style) */
export function modalTitle(release: Release): string {
  const h = release.headline;
  return `${release.version}: ${h.charAt(0).toLowerCase()}${h.slice(1)}`;
}

export function modalSections(build?: string | null): ModalSection[] {
  const release = currentRelease(build);
  if (!release) return [];
  const sections: ModalSection[] = [
    {
      title: modalTitle(release),
      description: release.summary,
      features: [...release.features],
      ...(release.usage_note ? { usage_note: release.usage_note } : {}),
    },
  ];
  if (release.earlier) {
    sections.push({
      title: `Earlier in ${release.earlier.version}`,
      description: release.earlier.summary,
      features: [],
    });
  }
  return sections;
}

// ── the window bridge ───────────────────────────────────────────────────

export const RELEASE_NOTES_READY = 'ss:release-notes-ready';

export interface ReleaseNotesBridge {
  releases: Release[];
  /** the newest release at or below the build (the button's version when omitted) */
  latestVersion: (build?: string | null) => string | null;
  /** the version modal's sections */
  modalSections: (build?: string | null) => ModalSection[];
}

declare global {
  interface Window {
    SoulSyncReleaseNotes?: ReleaseNotesBridge;
  }
}

export function installReleaseNotes(): void {
  if (typeof window === 'undefined') return;
  const build = (b?: string | null) => b ?? buildVersionFromPage();
  window.SoulSyncReleaseNotes = {
    releases: RELEASES,
    latestVersion: (b) => currentRelease(build(b))?.version ?? null,
    modalSections: (b) => modalSections(build(b)),
  };
  window.dispatchEvent(new CustomEvent(RELEASE_NOTES_READY));
}
