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
    version: '3.6.8',
    month: 'November 2026',
    headline: 'A phone pass, Last.fm without Pro and request fixes',
    summary:
      'Last.fm API keys need a Pro subscription from 1 December 2026, so everything Last.fm did now falls back to free sources and keeps what it already knew. Phones get a pass over the worker status, menus, dialogs and pages, audiobooks and podcast episodes can ask first, and the permission screens show what is really in force. Plus a track you own can move into an album’s missing slot, track numbers stop beating titles, and the transfer folder is called the music library folder everywhere.',
    features: [
      'Last.fm keeps working with a Pro key and falls back to ListenBrainz, Deezer, MusicBrainz, TheAudioDB and Wikipedia without one. Answers are cached, so a lapsed key keeps what it knew.',
      'Phones: worker status opens on a tap, the menu button stays, every page reaches its end, menus and dialogs fit the screen, and Re-identify is a full-screen sheet (thanks Thibs, c5pie and wolf39us, #2180 to #2187, #2197).',
      'Require approval for audiobook and podcast episode requests, controls that show the setting in force, and one set of words for requests everywhere (thanks SeadogsBooty, #2188 to #2192).',
      'Chat messages only reach people allowed to chat, a member’s notifications skip refusals and progress noise (#2191), and the enrichment workers are the admin’s alone (thanks SeadogsBooty, #2196).',
      'Titles beat track numbers when lining up a tracklist, a song’s number heals from its file, and a deleted file stops borrowing another album’s (thanks wolf39us).',
      'Rename / Organize keeps $quality in the name (thanks Maegfea, #2195) and skips a removed album instead of failing (thanks wolf39us, #2194).',
      'Move a track you own into an album’s missing slot, and Reassign moves instead of leaving a copy behind (#2193).',
      'The admin menu no longer flashes for a member after signing back in (thanks SeadogsBooty, #2156).',
      'The transfer folder is called the music library folder everywhere you read it. In Docker it is still the /app/Transfer mount.',
    ],
    items: [
      {
        title: 'Last.fm without Pro',
        desc: 'From 1 December 2026 a Last.fm API key needs Last.fm Pro. With a working key nothing changes. Without one, top tracks, similar artists and tracks, radio and Your Artists come from ListenBrainz and Deezer, artist bios from TheAudioDB or Wikipedia and tags from MusicBrainz, with a note saying where. Answers are cached, scrobbles wait for a working key, and ListenBrainz is the recommended scrobbler.',
        page: 'settings',
      },
      {
        title: 'A phone pass',
        desc: 'A tap on a worker shows its status with Pause or Resume (#2180), the menu button stays on an open playlist (#2181), every page scrolls to its end (#2182), the album menu (#2183), import cards (#2185) and long confirm boxes (#2186) fit the screen, Get Show draws its arrow (#2184), Settings says Saved only after a save (#2187), and Re-identify opens as a full-screen sheet with the search and results first (#2197). Thanks Thibs, c5pie and wolf39us.',
      },
      {
        title: 'Require approval for books and episodes',
        desc: 'Audiobook and podcast episode requests can Require approval like movies, TV and music. Requests land on the Requests page, and approving one gets the book or episode (thanks SeadogsBooty, #2188).',
        page: 'settings',
      },
      {
        title: 'Permissions say what is in force',
        desc: 'A control held by the global policy shows the value in force and says what the template has, Back to template only shows when there are changes, and requests read Auto-approve, Require approval or Requests off everywhere (thanks SeadogsBooty, #2189, #2190, #2192).',
        page: 'settings',
      },
      {
        title: 'A member’s notifications',
        desc: 'Chat messages only reach people allowed to chat, refusals and progress notes no longer pile up in a member’s notifications, and the enrichment workers, their status, Manage Workers and the Library card’s scan and backup are for admins only (thanks SeadogsBooty, #2191, #2196).',
      },
      {
        title: 'Titles before track numbers',
        desc: 'Other versions, reassign and tracklist refresh match a file by its title first, a song sitting on another song’s number moves to its own, and a file deleted from its album is no longer found in another album’s folder (thanks wolf39us).',
        page: 'library',
      },
      {
        title: 'Move a track here',
        desc: 'A missing track’s ⋯ menu has Move a track here: pick a track you already own, see where its file goes and which tags change, and it moves into the slot with its plays and history. Nothing is copied. Reassign to another artist now moves too, so it no longer leaves the old album behind (#2193).',
        page: 'library',
      },
      {
        title: 'Rename / Organize',
        desc: '$quality stays in the name when renaming files already in the library (thanks Maegfea, #2195), and Preview rename skips an album you removed instead of failing (thanks wolf39us, #2194).',
        page: 'library',
      },
      {
        title: 'No admin menu flash',
        desc: 'A member’s menu starts hidden until their own profile trims it, so the admin menu never shows for a moment after signing back in (thanks SeadogsBooty, #2156).',
      },
      {
        title: 'Music library folder',
        desc: 'What used to be called the transfer folder reads as the music library folder everywhere. In Docker it is still the /app/Transfer mount, so nothing about your setup changes.',
        page: 'settings',
      },
    ],
    earlier: {
      desc: '3.6.7 brought People and permissions, Settings in React, a Seerr compatible API and a rebuilt Server Activity. 3.6.6 turned the video Library into a manager. 3.6.2 brought the new Library, built by @nick2000713.',
      version: '3.6.7',
      summary:
        'Settings, People became one place for who can do what, Settings moved to React, SoulSync could answer as a Seerr server, and Server Activity got its own history and stats.',
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
