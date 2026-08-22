/**
 * Work out which service a pasted playlist URL belongs to.
 *
 * The page has four paste-a-URL tabs — Spotify Link, iTunes Link, Deezer Link
 * and YouTube — whose input step is identical: one field, one button. Which one
 * you must stand on is decided entirely by the string you are about to paste,
 * which is something the page can work out for itself. Making the user pick
 * first is how you get `Invalid Deezer playlist URL` from a perfectly good
 * Deezer link that happened to be pasted into the wrong tab.
 *
 * NO NEW REGEXES. Every branch delegates to the extractor that already owns
 * that service's URL shapes and is already pinned by -sync.urls.test.ts. This
 * file only decides WHICH of them applies, so detection can never disagree with
 * the parse that follows it.
 *
 * Order is load-bearing where the shapes could overlap:
 *   * the Deezer SHARE link is tested before the ordinary Deezer link, because
 *     it must produce its own actionable message rather than "not recognised";
 *   * Apple Music is tested before the generic fallbacks because its extractor
 *     accepts bare `itunes:`/`applemusic:` URI schemes that look like nothing
 *     else;
 *   * the bare-numeric case that `extractDeezerPlaylistId` accepts is
 *     deliberately NOT honoured here. A lone `12345` is not evidence of Deezer;
 *     it is evidence the user has not finished pasting.
 */

import {
  extractDeezerPlaylistId,
  extractITunesLinkId,
  extractSpotifyPublicId,
  isDeezerShareUrl,
} from './-sync.urls';

/**
 * The vertical ids these URLs route to — the SAME strings SYNC_VERTICAL_IDS
 * registers, so a detection result indexes the registry directly. Note Deezer's
 * is plain `deezer`: the Deezer LINK tab owns that vertical, while the
 * Deezer-ARL account tab is engine-driven and has none.
 */
export type DetectedSource = 'spotify_public' | 'itunes_link' | 'deezer' | 'youtube';

export interface DetectedUrl {
  source: DetectedSource;
  /** What the source's own parse step will be handed. */
  url: string;
  /** Present when the extractor already resolved one. */
  id?: string;
  /** Spotify/Apple distinguish playlist vs album vs track. */
  kind?: string;
}

export interface UndetectedUrl {
  source: null;
  /**
   * Why, in the user's terms. Never "invalid input" — either it names the one
   * thing they can do about it, or it lists what this field accepts.
   */
  error: string;
}

export type UrlDetection = DetectedUrl | UndetectedUrl;

const NOTHING_PASTED = 'Paste a playlist link to get started.';

const UNRECOGNISED =
  'That link is not one we can read. Paste a playlist or album link from ' +
  'Spotify, Apple Music, Deezer or YouTube.';

/**
 * A Deezer share link carries no id — it only exists behind a redirect the
 * browser cannot follow cross-origin. Naming the one step that fixes it beats
 * calling the user's own share button invalid.
 */
const DEEZER_SHARE =
  'That is a Deezer share link, which hides the playlist id behind a redirect. ' +
  'Open it in a browser and paste the deezer.com/playlist/… address it lands on.';

export function detectPlaylistUrl(raw: string): UrlDetection {
  const url = (raw || '').trim();
  if (!url) return { source: null, error: NOTHING_PASTED };

  // Before the ordinary Deezer branch — a share link would otherwise fall all
  // the way through to "not recognised", which is the least useful thing we
  // could say about a link the user got from Deezer itself.
  if (isDeezerShareUrl(url)) return { source: null, error: DEEZER_SHARE };

  const spotify = extractSpotifyPublicId(url);
  if (spotify) {
    return { source: 'spotify_public', url, id: spotify.id, kind: spotify.type };
  }

  // Apple's extractor also accepts `itunes:`/`applemusic:` URI schemes, which
  // are not URLs at all and would confuse anything host-based.
  const itunes = extractITunesLinkId(url);
  if (itunes) {
    return { source: 'itunes_link', url, id: itunes.id, kind: itunes.type };
  }

  // A bare number satisfies extractDeezerPlaylistId, so require the host too:
  // `12345` is an unfinished paste, not a Deezer playlist.
  if (/deezer\.com/i.test(url)) {
    const id = extractDeezerPlaylistId(url);
    if (id) return { source: 'deezer', url, id };
    return {
      source: null,
      error: 'That Deezer link has no playlist id. It should look like deezer.com/playlist/12345.',
    };
  }

  if (/(?:music\.)?youtube\.com\/playlist/i.test(url)) {
    return { source: 'youtube', url };
  }

  // Recognisably YouTube, but not a playlist — worth saying so, because it is
  // the single most common near-miss (pasting a video instead of its playlist).
  if (/(?:music\.)?youtube\.com|youtu\.be/i.test(url)) {
    return {
      source: null,
      error: 'That is a YouTube link, but not a playlist one. Open the playlist and copy its URL.',
    };
  }

  return { source: null, error: UNRECOGNISED };
}

/** Narrowing helper, so call sites read as intent rather than as a null check. */
export function isDetected(d: UrlDetection): d is DetectedUrl {
  return d.source !== null;
}

/** What each paste-a-link tab is called on screen. */
export const DETECTED_SOURCE_TAB_LABELS: Readonly<Record<DetectedSource, string>> = {
  spotify_public: 'Spotify Link',
  itunes_link: 'iTunes Link',
  deezer: 'Deezer Link',
  youtube: 'YouTube',
};

/**
 * Which TAB each detected source opens.
 *
 * Not the same strings as the vertical ids, and the difference is a real trap:
 * the Deezer paste-a-link tab is `deezer-link`, while the vertical it drives is
 * plain `deezer`. Underscores vs hyphens differ too. Pinned against SYNC_TABS
 * in the tests so a tab rename cannot silently route someone nowhere.
 */
export const DETECTED_SOURCE_TAB_IDS: Readonly<Record<DetectedSource, string>> = {
  spotify_public: 'spotify-public',
  itunes_link: 'itunes-link',
  deezer: 'deezer-link',
  youtube: 'youtube',
};

/**
 * What each SERVICE is called — which is not the tab name. Saying "that is a
 * Deezer Link link" is the sentence you get from reusing one label for both.
 */
export const DETECTED_SOURCE_SERVICE_LABELS: Readonly<Record<DetectedSource, string>> = {
  spotify_public: 'Spotify',
  itunes_link: 'Apple Music',
  deezer: 'Deezer',
  youtube: 'YouTube',
};

/**
 * The message for a link that is perfectly good, just not for THIS tab.
 *
 * Four tabs take a URL and each rejects anything that is not its own service —
 * so pasting a Deezer link into the Spotify field said "Please enter a valid
 * Spotify playlist or album URL", which describes the field rather than the
 * mistake and leaves the user re-reading their own correct link. Naming the tab
 * that does want it turns a dead end into one click.
 *
 * Returns null when the URL is not another supported service, so the caller
 * falls through to its own message — this only ever REPLACES a wrong answer,
 * never suppresses a right one.
 */
export function wrongTabError(url: string, thisSource: DetectedSource): string | null {
  const detected = detectPlaylistUrl(url);
  if (!isDetected(detected)) return null;
  if (detected.source === thisSource) return null;
  const service = DETECTED_SOURCE_SERVICE_LABELS[detected.source];
  const tab = DETECTED_SOURCE_TAB_LABELS[detected.source];
  return `That is a ${service} link. Paste it on the ${tab} tab to load it.`;
}
