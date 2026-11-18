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
    version: '3.6.7',
    month: 'November 2026',
    headline: 'People and permissions, Settings in React and Seerr',
    summary:
      'Settings, People is one place for who can do what, the Settings page is rebuilt in React, SoulSync can answer as a Seerr server for Litefin and other Seerr apps, Server Activity is rebuilt with its own history and stats, albums can switch to another edition, and popular tracks get a flame. Plus band names with an ampersand stay one artist, numbered releases stop crossing, and a long list of reported fixes.',
    features: [
      'Settings, People: templates, personal changes, a global policy, moderators, separate movie and TV access, request quality and a request limit per kind (thanks SeadogsBooty, #2166 to #2168).',
      'Members see only their own things: their notifications, the download actions they may use, and the help guides for what they can open (thanks SeadogsBooty, #2170, #2173, #2174).',
      'Settings rebuilt in React: it looks and works the same, loads its tabs once and saves only what you changed.',
      'A Seerr compatible API at /seerr, so Litefin and other Seerr apps can discover and request through the video side (thanks Thibs, #2151).',
      'Server Activity rebuilt for Plex and Jellyfin: now playing and why it converts, stop with a message, and SoulSync’s own play history and stats.',
      'Other versions on the album menu: pick another edition with a preview first (thanks wolf39us, #2145).',
      'A flame on an album’s most played tracks, and “N to rename” on the artist page (thanks wolf39us, #2144, #2141).',
      'Collection completeness on the Library cards and table, with sorts and filters (thanks SeadogsBooty, #2106 to #2109).',
      'Band names like Above & Beyond and K/DA stay one artist, compilations stay under their album artist, and numbered releases never cross (#2131, #2133 to #2136, #2150).',
      'Release types go by length, so Wish You Were Here is an album again.',
      'artist.nfo in the artist folder with poster.jpg and background.jpg (#2138), private Jellyfin playlists (#2148), and Apple Music mirrors that refresh (#2147).',
      'Contributions from nstrelow (#2140), PapaBearDoes (#2152) and ginevra-70 (#2115): AcoustID scans past an unreadable file, a video table that keeps its columns in view, and Music Assistant scans that follow their task.',
    ],
    items: [
      {
        title: 'People and permissions',
        desc: 'Settings, People holds everyone, a global policy, templates and invites. Each person follows a template with their own changes on top, getting things is set per media type, movies and TV are separate, quality and a request limit are set per kind, and moderators answer requests. Nothing changes on upgrade (thanks SeadogsBooty, #2166 to #2168).',
        page: 'settings',
      },
      {
        title: 'Members see their own',
        desc: 'A member’s notifications show their own notes, downloads and automations, search and Enhance Quality only show for profiles that may download, and Help & Docs shows only the guides for what they can use (thanks SeadogsBooty, #2170, #2173, #2174).',
      },
      {
        title: 'Cover art findings explain themselves',
        desc: 'A Missing Cover Art finding shows the library, the audio files, the album folder and the cover found side by side, says what Apply Art writes, and Change cover lets you pick your own (thanks Vidariondr, #2169).',
        page: 'tools',
      },
      {
        title: 'Keep Best and playlists',
        desc: 'Duplicates in the same playlists can go, playlists you name never protect a copy, and an option moves a removed copy’s playlist entries to the kept one (thanks Storm, #2172).',
        page: 'tools',
      },
      {
        title: 'Settings, rebuilt',
        desc: 'The Settings page is React now, every tab on the music and video side. It looks and works the same, loads its tabs once and saves only what you changed.',
        page: 'settings',
      },
      {
        title: 'SoulSync as a Seerr server',
        desc: 'Turn on the Seerr Compatible API under Settings, System, SoulSync API and point Litefin’s Seerr plugin, or another Seerr app, at /seerr with an admin key. Discover, search and requests go through the video side (thanks Thibs, #2151).',
        page: 'settings',
      },
      {
        title: 'Server Activity, rebuilt',
        desc: 'The activity button opens one drawer for Plex and Jellyfin: what is playing and how, the reason a stream converts in plain words, stop with a message, and SoulSync’s own play history and stats. Admin only, set up under Settings, Connections, Server Activity.',
        anchor: 'shell.activity',
      },
      {
        title: 'Help, rebuilt',
        desc: 'The ? opens help beside the page: answers for what is happening there, Ctrl K to search help, settings and actions from anywhere, Health with a fix for each problem, a bug report with secrets removed, log downloads and three short tours.',
        anchor: 'shell.help',
      },
      {
        title: 'Other versions',
        desc: 'An album’s ⋯ menu lists the release’s other editions with track count, year, country and label, plus a search for that artist. It shows what changes before it switches (thanks wolf39us, #2145).',
        page: 'library',
      },
      {
        title: 'Popular tracks',
        desc: 'A flame marks up to three of an album’s most played tracks on Last.fm that clearly stand out, like Plexamp, from the play counts SoulSync already has (thanks wolf39us, #2144).',
        page: 'library',
      },
      {
        title: 'What a rename would move',
        desc: 'The artist page shows “N to rename” on albums whose files don’t match your naming template, and a click opens Preview rename (thanks wolf39us, #2141). Locking an album there keeps you on the artist page (#2143).',
        page: 'library',
      },
      {
        title: 'Collection completeness',
        desc: 'Cards and table rows show owned over expected tracks with sorts, a featured filter, card size and multi-select (thanks SeadogsBooty, #2106 to #2109). For an artist you don’t watch it counts every track of the releases you own something of.',
        page: 'library',
      },
      {
        title: 'Band names stay whole',
        desc: 'Above & Beyond, Kyau & Albert and K/DA stay one artist in tags, credits, folders and Split Artists, and a Split Artists finding can be corrected by hand (thanks SeadogsBooty, wolf39us and Vidariondr, #2131, #2136, #2150).',
      },
      {
        title: 'Releases stay together',
        desc: 'A compilation stays under its album artist on reorganize and download (#2133, #2135), and numbered releases like Group Therapy 696 and 699 never cross (thanks SeadogsBooty, #2134).',
      },
      {
        title: 'Release types by length',
        desc: 'An EP or single now has to run under 30 minutes, so Wish You Were Here is an album again, and a provider’s own type wins (thanks wolf39us).',
      },
      {
        title: 'Artist art for your server',
        desc: 'artist.nfo lands in the artist folder, and the same switch saves poster.jpg and background.jpg where the folder has none yet (thanks Ericsson, #2138). Guest artists no longer fill the Library list (#2142).',
        page: 'settings',
      },
      {
        title: 'Review and AcoustID',
        desc: 'Asked for and Got are the right way round in review, and one file with no usable audio no longer stops every AcoustID scan (thanks nstrelow, #2140).',
      },
      {
        title: 'Playlists and servers',
        desc: 'Jellyfin playlists are private to their account (#2148), Apple Music mirrors refresh again and copy mode playlists stay owned after a remount (thanks NeskireDK, #2147, #2137), and Music Assistant scans follow their own task (thanks ginevra-70, #2115).',
      },
      {
        title: 'Video',
        desc: 'An import folder like the music side’s (#2129), manual search with an editable query on a movie’s menu (#2121, #2122), release languages on download rows (#2120), and long titles that no longer push the library table’s columns out of view (thanks PapaBearDoes, #2152).',
      },
      {
        title: 'Where a title stands',
        desc: 'Every video poster shows In Library, Partial, Requested, Approved, Searching, Downloading or Failed, and the detail page shows progress, each season and who asked. A request for something already on its way is refused (thanks SeadogsBooty, #2160).',
      },
      {
        title: 'Discover',
        desc: 'Every row fills, hides when empty or offers Try again, the Top 10 numbers sit beside the posters, and the page has one even layout (thanks SeadogsBooty, #2158, #2159).',
        page: 'video-discover',
      },
      {
        title: 'Profiles',
        desc: 'Members land on a page they can open and never see the admin menu, admins can set, lock and reset what each profile shows, and a lock hides Customize and shows only what the admin set. Members see only their own profile with login on, and set their own sidebar weather under Profile & account (thanks SeadogsBooty, #2154 to #2157, #2162 to #2164).',
      },
      {
        title: 'Security',
        desc: 'Only the admin can make the first API key, and members can only do what their profile allows: no downloads with downloads off, no shared library edits, tools, other people’s downloads or install accounts. Worth updating soon.',
      },
      {
        title: 'Artist folder names',
        desc: 'A download takes the name your library has for an artist, so Machine Gun Kelly from Deezer lands in mgk. Artist Settings can set your own spelling (thanks nstrelow, #2165).',
        page: 'library',
      },
      {
        title: 'Library fixes',
        desc: 'Leftover rows fold on every scan (#2094), reorganize takes the cover along (#2010), a pair that can’t be merged says why (#2146), and the library upgrade no longer stops at an old track with no id.',
        page: 'library',
      },
    ],
    earlier: {
      desc: '3.6.6 turned the video Library into a manager with bulk operations, history with undo and a padlock. 3.6.5 rebuilt both dashboards, Tools, Automations and Stats. 3.6.2 brought the new Library, built by @nick2000713.',
      version: '3.6.6',
      summary:
        'The video Library became a manager with tables, bulk operations, history with undo and a padlock, and movie and show pages manage their own seasons, episodes and files.',
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
