import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * How a grab decides which download source it is using.
 *
 * A torrent search returns Prowlarr AND EXT.to rows together, and the two are
 * not grabbed the same way: an EXT.to row carries an info_url and no magnet
 * yet, because resolving 25 magnets behind Cloudflare to draw a list would take
 * minutes. So it has to reach the backend labelled 'extto' or the magnet is
 * never resolved and the grab fails on a missing download URL.
 *
 * The discriminator is `indexer_id`, and this file mostly exists to stop
 * anybody reaching for `r.source` again. A result row's `source` is the RELEASE
 * source parsed out of the filename - "BluRay", "WEB-DL", "HDTV". An earlier
 * attempt at this read it as the download source, which sent source="BluRay" to
 * an endpoint that accepts only soulseek/torrent/usenet/extto, so EVERY grab
 * failed rather than one being fixed.
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
  it('starts from the source the panel actually searched', () => {
    expect(BUILD).toContain("var src = p.source || 'soulseek'");
  });

  it('overrides to extto on the indexer id, never on the row source', () => {
    expect(BUILD).toContain("String(r.indexer_id || '').toLowerCase() === 'extto'");
    // the trap: r.source is "BluRay"/"WEB-DL", not a download source
    expect(BUILD).not.toMatch(/var src = r\.source/);
    expect(BUILD).not.toMatch(/src = r\.source \|\|/);
  });

  it('records why, so the next person does not retry the bug', () => {
    expect(BUILD).toContain('RELEASE source parsed out of the filename');
  });

  it('carries what grab-time magnet resolution reads from', () => {
    expect(BUILD).toContain('payload.info_url = r.info_url');
    expect(BUILD).toContain('payload.magnet_id = r.magnet_id');
  });

  it('puts those on the torrent branch, not the soulseek one', () => {
    const soulseek = BUILD.slice(
      BUILD.indexOf("if (src === 'soulseek')"),
      BUILD.indexOf('} else {'),
    );
    expect(soulseek).not.toContain('info_url');
  });

  it('only ever sends a source the backend accepts', () => {
    // The endpoint rejects anything outside this set outright, which surfaces
    // as a bare "grab failed" with nothing to act on.
    const sources = [...BUILD.matchAll(/src = '([a-z]+)'/g)].map((m) => m[1]);
    for (const s of sources) {
      expect(['soulseek', 'torrent', 'usenet', 'extto']).toContain(s);
    }
  });
});
