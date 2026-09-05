import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { beforeEach, describe, expect, it } from 'vitest';

const source = readFileSync(resolve(process.cwd(), 'static/settings.js'), 'utf8');
const block = source.split('// MUSICBRAINZ SERVER SETTINGS')[1].split('// END MUSICBRAINZ SERVER SETTINGS')[0];
const helpers = new Function('document', `${block}; return { loadMusicBrainzServerSettings, collectMusicBrainzServerSettings };`)(document);

describe('MusicBrainz server settings', () => {
  beforeEach(() => {
    document.body.innerHTML = '<input id="musicbrainz-base-url"><input id="musicbrainz-request-interval">';
  });
  it('loads and collects saved server values including zero delay', () => {
    helpers.loadMusicBrainzServerSettings({ musicbrainz: { base_url: 'http://mirror:5000', request_interval: 0 } });
    expect(helpers.collectMusicBrainzServerSettings()).toEqual({ base_url: 'http://mirror:5000', request_interval: 0 });
  });
  it('defaults a fresh form to public service pacing', () => {
    helpers.loadMusicBrainzServerSettings({});
    expect(helpers.collectMusicBrainzServerSettings()).toEqual({ base_url: '', request_interval: 1.05 });
  });
  it.each(['ftp://mirror', 'not a url', 'https://user:secret@mirror', 'https://mirror?x=1'])(
    'rejects invalid URL %s', (base_url) => {
      helpers.loadMusicBrainzServerSettings({ musicbrainz: { base_url } });
      expect(() => helpers.collectMusicBrainzServerSettings()).toThrow();
    },
  );
  it.each([-1, 'NaN', 'Infinity'])('rejects invalid interval %s', (request_interval) => {
    helpers.loadMusicBrainzServerSettings({ musicbrainz: { request_interval } });
    expect(() => helpers.collectMusicBrainzServerSettings()).toThrow();
  });
});
