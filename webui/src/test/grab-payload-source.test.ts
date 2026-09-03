import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * A grab has to use the ROW's source, not the panel's.
 *
 * Boulder: "when im doing a manual search on a wishlist item and it pulls up the
 * results and i click get. it says grab failed"
 *
 * Caused by merging EXT.to into the torrent lane. buildGrabPayload took the
 * source from the panel - the thing the user searched - so an EXT.to row was
 * sent as 'torrent'. The grab endpoint keys magnet resolution off
 * source='extto', these rows deliberately carry no download_url yet, so it fell
 * through to the "Missing the release's download URL" guard and surfaced as a
 * bare "grab failed" with nothing to act on.
 */

const JS = readFileSync(
  resolve(process.cwd(), 'static/video/video-download-view.js'),
  'utf8',
);

const BUILD = JS.slice(
  JS.indexOf('function buildGrabPayload'),
  JS.indexOf('function sendGrab'),
);

describe('the grab payload', () => {
  it('prefers the row source over the panel source', () => {
    expect(BUILD).toContain("var src = r.source || p.source || 'soulseek'");
  });

  it('still falls back to the panel for rows that carry no source', () => {
    // Soulseek rows have never carried one, and must keep working.
    expect(BUILD).toMatch(/r\.source \|\| p\.source/);
  });

  it('carries what grab-time magnet resolution reads from', () => {
    // EXT.to resolves the magnet once, for the release actually picked. Without
    // these the backend has a source but nothing to resolve.
    expect(BUILD).toContain('payload.info_url = r.info_url');
    expect(BUILD).toContain('payload.magnet_id = r.magnet_id');
  });

  it('puts them on the torrent branch, not the soulseek one', () => {
    const soulseek = BUILD.slice(BUILD.indexOf("if (src === 'soulseek')"), BUILD.indexOf('} else {'));
    expect(soulseek).not.toContain('info_url');
  });
});
