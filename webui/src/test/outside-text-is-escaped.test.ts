// nov 13 2026 review: text that comes from outside the app (a youtube video
// title, a provider's artist name, an image url, a server error message) was
// put into the page as html at these sites. a title with markup in it ran.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const read = (name: string) => readFileSync(resolve(__dirname, '../../static', name), 'utf8');

const SITES: Array<[string, string[]]> = [
  [
    'sync-services.js',
    [
      '<td class="yt-track">${escapeHtml(trackName)}</td>',
      '<td class="yt-artist">${escapeHtml(artistName)}</td>',
      '<td class="spotify-track">${escapeHtml(result.spotify_track || \'-\')}</td>',
      '<td class="spotify-album">${escapeHtml(result.spotify_album || \'-\')}</td>',
    ],
  ],
  [
    'shared-helpers.js',
    [
      'src="${escapeHtml(data.image_url)}" alt="${escapeHtml(artistBubbleData.artist.name)}"',
      '${escapeHtml(error.message)}',
    ],
  ],
  ['downloads.js', ['<img src="${escapeHtml(data.image_url)}" loading="lazy"']],
  [
    'wishlist-tools.js',
    [
      "${escapeHtml(data.name || 'Unknown')}",
      '${escapeHtml(data.artist_name)}',
      'Error: ${escapeHtml(e.message)}',
    ],
  ],
  [
    'stats-automations.js',
    [
      'Delete Group "${escapeHtml(groupName)}"',
      '${escapeHtml(groupName)} includes notification steps',
      '${escapeHtml(err.message)}',
    ],
  ],
  [
    'pages-extra.js',
    [
      "${escapeHtml(data.error || 'Could not load server playlists')}",
      'Error: ${escapeHtml(e.message)}',
    ],
  ],
  [
    'settings.js',
    [
      "${escapeHtml(data.error || 'Prowlarr not configured.')}",
      'Failed to load indexers: ${escapeHtml(e.message)}',
    ],
  ],
  ['enrichment.js', ['Search error: ${escapeHtml(err.message)}']],
];

const RAW = [
  /\$\{(?:e|err|error)\.message\}<\/(?:div|em)>/,
  /<td class="yt-(?:track|artist)">\$\{(?:trackName|artistName)\}/,
  /<td class="spotify-(?:track|artist|album)">\$\{result\./,
  /alt="\$\{artistBubbleData\.artist\.name\}"/,
  /src="\$\{data\.image_url\}"/,
];

describe('outside text is escaped before it becomes html', () => {
  for (const [file, snippets] of SITES) {
    it(file, () => {
      const source = read(file);
      for (const snippet of snippets) expect(source).toContain(snippet);
      for (const raw of RAW) expect(source).not.toMatch(raw);
    });
  }
});
