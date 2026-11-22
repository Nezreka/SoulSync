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
    version: '3.6.9',
    month: 'November 2026',
    headline: 'The player, settings rebuilt and music apps',
    summary:
      'SoulSync has its own player: open it from the sidebar and everything you own plays in one full-screen app, with a home, a TV guide of channels made from your library and SoulSync’s own playlists. Settings is rebuilt on nine tabs with pills, side sheets and one search, and any Subsonic or OpenSubsonic music app can play straight from SoulSync, paired from the new Settings > Devices. Plus folder watching (off until you turn it on), a coverage check for your media server’s folders, Spotify history import, your own plays on every artist page, a full Discover for new profiles, more of Your Year, blends that ask first, and fixes for #2213 to #2241. If you use an auth proxy header, add your proxy’s address under Trusted proxy addresses or people get the PIN screen.',
    features: [
      'Trusted proxy addresses: the auth proxy header only counts from your proxy’s address. Until you add it, the header is ignored and people get the PIN screen.',
      'The player: open the player from the sidebar for music, movies, TV, YouTube, audiobooks and podcasts in one full-screen app, with a home each profile arranges, pages for people, studios and genres, and request or get for what you don’t own.',
      'Every file plays: files a browser can’t play are converted as they stream (on the GPU when there is one), with audio tracks, subtitles you can find and shift, next episode, synced lyrics and visuals.',
      'The guide: channels from your library scheduled like TV, premieres in the evening, channel numbers, reminders, a channel builder and your own lineup.',
      'Player playlists: yours, mirrored ones and SoulSync’s own (daily mixes, moods, Fresh Tape, Flow and more). Get what you don’t have puts the missing songs on your Wishlist.',
      'Watching now, comments on titles, marks on moments and a watchlist per profile.',
      'Settings on nine tabs with pills, a side sheet per source and service, one search over every label, and a save status that says what is holding a save.',
      'Music apps over the Subsonic and OpenSubsonic API, each paired to one profile, with mp3 or opus conversion that waits its turn instead of sending the bigger file.',
      'Settings > Devices: pair with a one-time code and QR, your server’s addresses, and a check that the remote address really leads back here.',
      'Folder watching, off by default: turn it on in Settings > Library > Folders. Local disks are watched live, network shares on a timer.',
      'A coverage check shows how much of your media server’s music SoulSync can read, with folder overrides.',
      'Spotify history import, your own plays on every artist page, a full Discover for new profiles, and Your Year with a soundtrack, streaks, a calendar, a playlist and a video.',
      'Blends need both people to share their listening, a private session keeps plays out of history, and the adventurousness dial is per profile.',
      'Now playing for Last.fm and ListenBrainz, Koito, embedded lyrics, and empty tags filled from the library with undo.',
      'Downloads wait out a lost Soulseek login, near-misses clear themselves, upgrade rows say what they replace, and Lidarr gets album requests as one album (thanks savemosca, #2219, #2225, #2226).',
      'Compressed responses cut the first load from about 16 MB to 3, and an open tab offers a reload after an update.',
      'A release stays whole under its first credited artist and a single stays a single (thanks Ericsson, #2239, #2240), manual search for TV shows by series, season or episode (thanks Thibs, #2233), and Move a track here and Copy a track here fixed (thanks Vidariondr, #2241).',
      'Fixes for dead files, import tags, playlist readback, Soulseek folder hits, Various Artists, Greek and Cyrillic names, big wishlists, import folders and foreign titles (#2213 to #2235).',
    ],
    items: [
      {
        title: 'The player',
        desc: 'Open the player in the sidebar, under the audio and video toggle. Music, movies, TV, YouTube, audiobooks and podcasts play in one full-screen app with its own home (each profile arranges its rows), for you pages, collections, and pages for people, studios, networks, genres and labels. Search plays a song or video right from the results, and a title you don’t own opens a page to request or get it.',
        anchor: 'shell.open-player',
      },
      {
        title: 'Every play button plays',
        desc: 'A queue with shuffle and repeat, resume where you left off, speed, a sleep timer and chapters. Files a browser can’t play are converted as they stream, on the GPU when there is one, with a quality menu, audio tracks, subtitles you can find and shift, and the next episode. Music opens full screen with synced lyrics and visuals.',
      },
      {
        title: 'The guide',
        desc: 'Channels made from your library, scheduled like TV: genres, networks, decades, kids and music by genre and vibe. New episodes premiere in the evening and each channel airs what you haven’t seen first. Channel numbers, channel up and down, start over, favorites and reminders. Admins make channels for everyone, anyone builds their own, and your lineup orders and hides them.',
      },
      {
        title: 'Playlists in the player',
        desc: 'Your playlists (liked songs first), the ones you mirrored, and SoulSync’s own: daily mixes, On Repeat, moods, Fresh Tape, The Archives, Hidden Gems, Flow and more. A discovery playlist plays the songs you have, and Get what you don’t have puts the rest on your Wishlist.',
      },
      {
        title: 'Watching together',
        desc: 'Watching now shows what others on the server are playing and lets you join at their spot. Titles have comments, marks keep a moment with a note, and every profile has its own watchlist for movies, shows and YouTube.',
      },
      {
        title: 'Settings, rebuilt',
        desc: 'Nine tabs with pills, each pill opening with one line on what it is for. Downloads is built around the source order, every source and service opens in a side sheet with one Test, a template that would not work holds back only itself, and one search (press /) finds any setting, even behind More.',
        page: 'settings',
      },
      {
        title: 'Music apps',
        desc: 'Settings > Devices > Set up a music app: pick whose music, and the server, username, app password and API key show once with a QR. The app plays that profile’s library, playlists, liked songs and lyrics, and its plays count like the web player’s. Songs convert to mp3 or opus when an app asks for less, and wait their turn rather than going out as the bigger file.',
        page: 'settings',
      },
      {
        title: 'Settings > Devices',
        desc: 'Add a device with a one-time code and QR, see and unpair paired devices, and check your server: its fingerprint, the addresses a device can use, and whether the remote address really leads back here, with a link to try from a phone on mobile data.',
        page: 'settings',
      },
      {
        title: 'Trusted proxy addresses',
        desc: 'If an auth proxy signs people in with a header, that header now only counts from the addresses in Settings > System > Security > Trusted proxy addresses. Until you add your proxy’s address the header is ignored and people get the PIN screen.',
        page: 'settings',
      },
      {
        title: 'Folder watching',
        desc: 'Off by default. Turn on Watch library folders for changes in Settings > Library > Folders and music you add, retag, move or delete by hand shows up without a scan, and the Import folder starts as soon as files land. Local disks are watched live, network shares are checked on a timer, and each folder can be set on its own.',
        page: 'settings',
      },
      {
        title: 'Coverage check',
        desc: 'Settings > Library > Folders lists every music folder your media server reports and says whether SoulSync can read it. Add one as a library folder in one click, or save a folder override when the paths differ.',
        page: 'settings',
      },
      {
        title: 'Spotify history import',
        desc: 'Stats takes the zip or JSON from Spotify’s privacy export. Only music plays leave your browser, plays ListenBrainz or Last.fm already have are linked, not doubled, and you choose stats or taste only.',
        page: 'stats',
      },
      {
        title: 'You and this artist',
        desc: 'An artist page shows your own plays of them: your rank, first and last listen, a year by month and the songs you play most.',
        page: 'library',
      },
      {
        title: 'Discover for new profiles',
        desc: 'A profile with little history gets a full Discover from charts and trends, and a banner to connect ListenBrainz or Last.fm, import Spotify history or pick five artists you love. Liked songs, server stars and Watchlist adds feed your taste too, and the adventurousness dial is per profile.',
        page: 'discover',
      },
      {
        title: 'Your Year, more of it',
        desc: 'A soundtrack of your top songs, your longest streak, biggest day, top genre and a calendar of the year, then save it as a playlist or a video. From 1 December the dashboard says it is ready.',
      },
      {
        title: 'Blends ask first',
        desc: 'Share my listening is a switch per profile, off for everyone, and a blend needs both people to share. A private session keeps six hours of plays out of history, stats, taste and scrobbles.',
      },
      {
        title: 'Now playing, Koito and lyrics',
        desc: 'The web player tells Last.fm and ListenBrainz what is playing, plays scrobble the moment they count, Koito has its own address button and history import, and lyrics come from the file’s own tags before LRCLIB.',
      },
      {
        title: 'Fill empty tags',
        desc: 'When the library learns something a file left blank, SoulSync writes it in: never over a tag the file has, never on hand-tagged or locked files. Tools > Filled Tags shows every write with undo.',
        page: 'tools',
      },
      {
        title: 'Releases stay whole',
        desc: 'A release files under its first credited artist as a whole, so a box set no longer splits across its members, and rows filed that way move back. A credit given as one name files under its first artist, and a single stays a single (thanks Ericsson, #2239, #2240).',
        page: 'library',
      },
      {
        title: 'Move or copy a track here',
        desc: 'The Move a track here dialog lays out properly, and I Have This is now Copy a track here: it copies and keeps the original, while move takes it out of its old album (thanks Vidariondr, #2241).',
        page: 'library',
      },
      {
        title: 'Manual search for TV shows',
        desc: 'A show’s ⋯ menu has Manual search, with a picker for the whole series, a season with its episodes, or one episode (thanks Thibs, #2233).',
      },
      {
        title: 'Downloads that wait and tidy up',
        desc: 'Downloads wait out a lost Soulseek login instead of failing, near-misses leave review once the track imports, and upgrade rows say what they replace.',
      },
      {
        title: 'Lidarr albums',
        desc: 'Album requests go to Lidarr as one album with a Whole album via Lidarr toggle, wishlist albums are checked by their tracks, and Lidarr finds them by MusicBrainz id and year (thanks savemosca, #2219, #2225, #2226).',
      },
      {
        title: 'Faster loads',
        desc: 'Pages and data go out compressed, so the first load is about 3 MB instead of 16, and a tab left open from before an update offers a reload.',
      },
      {
        title: 'Fixes from your reports',
        desc: 'Recognition badges (#2213), M3U export (#2214), dead files (#2216), import tags (#2217), the phone menu (#2218), import folders (#2220), big wishlists (#2221), Greek and Cyrillic names (#2222), a Locked filter for albums (#2223), Get vs manual search (#2224), Soulseek folder hits (#2228), Various Artists (#2229), the download chain (#2230), streaming imports (#2231), yt-dlp installs (#2232), foreign titles (#2234) and playlist readback (#2235). Thanks SeadogsBooty, xblain, carlosjfcasero, Thibs, Ericsson, xcrittpm, selimovd, Ktzenjammer and PfannkuchenWolf.',
      },
    ],
    earlier: {
      desc: '3.6.8 brought Last.fm without Pro, a phone pass and Shelf Life. 3.6.7 brought People and permissions. 3.6.2 brought the new Library, built by @nick2000713.',
      version: '3.6.8',
      summary:
        'Last.fm fell back to free sources without a Pro key, phones got a pass over menus, dialogs and pages, requests could wait for you to pick the release, and Shelf Life scored the video library.',
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
