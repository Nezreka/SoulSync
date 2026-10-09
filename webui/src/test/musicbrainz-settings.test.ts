import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { beforeEach, describe, expect, it } from 'vitest';

const source = readFileSync(resolve(process.cwd(), 'static/settings.js'), 'utf8');
const block = source
  .split('// MUSICBRAINZ SERVER SETTINGS')[1]
  .split('// END MUSICBRAINZ SERVER SETTINGS')[0];
const helpers = new Function(
  'document',
  `${block}; return { loadMusicBrainzServerSettings, collectMusicBrainzServerSettings, updateMusicBrainzRateControls };`,
)(document);
const rateMode = () => document.getElementById('musicbrainz-rate-mode') as HTMLSelectElement;
const rateInput = () => document.getElementById('musicbrainz-request-rate') as HTMLInputElement;

describe('MusicBrainz server settings', () => {
  beforeEach(() => {
    document.body.innerHTML =
      '<input id="musicbrainz-base-url"><select id="musicbrainz-rate-mode"><option value="fixed">Fixed</option><option value="adaptive">Adaptive</option></select><input id="musicbrainz-request-rate">';
  });
  it('loads a saved zero interval as adaptive mode', () => {
    helpers.loadMusicBrainzServerSettings({
      musicbrainz: { base_url: 'http://mirror:5000', request_interval: 0 },
    });
    expect(rateMode().value).toBe('adaptive');
    expect(rateInput().disabled).toBe(true);
    expect(helpers.collectMusicBrainzServerSettings()).toEqual({
      base_url: 'http://mirror:5000',
      request_interval: 0,
    });
  });
  it('shows a saved 0.1 second interval as 10 requests/second', () => {
    helpers.loadMusicBrainzServerSettings({
      musicbrainz: { base_url: 'http://mirror:5000', request_interval: 0.1 },
    });
    expect(rateInput().value).toBe('10');
    expect(helpers.collectMusicBrainzServerSettings().request_interval).toBeCloseTo(0.1);
  });
  it('defaults a fresh form to public service pacing', () => {
    helpers.loadMusicBrainzServerSettings({});
    expect(helpers.collectMusicBrainzServerSettings().request_interval).toBeCloseTo(1.05, 4);
  });
  it.each(['ftp://mirror', 'not a url', 'https://user:secret@mirror', 'https://mirror?x=1'])(
    'rejects invalid URL %s',
    (base_url) => {
      helpers.loadMusicBrainzServerSettings({ musicbrainz: { base_url } });
      expect(() => helpers.collectMusicBrainzServerSettings()).toThrow();
    },
  );
  it.each(['-1', '0', 'NaN', 'Infinity', ''])('rejects invalid fixed rate %s', (request_rate) => {
    helpers.loadMusicBrainzServerSettings({ musicbrainz: { base_url: 'http://mirror:5000' } });
    rateInput().value = request_rate;
    expect(() => helpers.collectMusicBrainzServerSettings()).toThrow();
  });
  it('rejects adaptive mode on public MusicBrainz', () => {
    helpers.loadMusicBrainzServerSettings({});
    rateMode().value = 'adaptive';
    expect(() => helpers.collectMusicBrainzServerSettings()).toThrow(/self-hosted/);
  });
});
