/**
 * The drift table is transcription, so the tests are literal pins plus raw
 * vanilla anchors: every drift-bearing fact asserted with the value typed out,
 * and the vanilla endpoint spellings grepped in the live source so a silent
 * backend-route rename fails here before it fails at runtime.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { SOURCE_IDS, SYNC_SOURCES, sourceForFakeHash, sourceForVpid } from './-sync.sources';

const SYNC_SERVICES = readFileSync(resolve(process.cwd(), 'static/sync-services.js'), 'utf8');

describe('endpoint families', () => {
  it('the hyphen/underscore update-phase drift is exactly beatport + listenbrainz', () => {
    expect(SYNC_SOURCES.beatport.api.updatePhase('h')).toBe('/api/beatport/charts/update-phase/h');
    expect(SYNC_SOURCES.listenbrainz.api.updatePhase('m')).toBe('/api/listenbrainz/update-phase/m');
    expect(SYNC_SOURCES.tidal.api.updatePhase('1')).toBe('/api/tidal/update_phase/1');
    expect(SYNC_SOURCES.qobuz.api.updatePhase('1')).toBe('/api/qobuz/update_phase/1');
    expect(SYNC_SOURCES.deezer.api.updatePhase('1')).toBe('/api/deezer/update_phase/1');
    expect(SYNC_SOURCES.youtube.api.updatePhase('h')).toBe('/api/youtube/update_phase/h');
    expect(SYNC_SOURCES.spotify_public.api.updatePhase('h')).toBe(
      '/api/spotify-public/update_phase/h',
    );
    expect(SYNC_SOURCES.itunes_link.api.updatePhase('h')).toBe('/api/itunes-link/update_phase/h');
  });

  it('anchors the drift spellings in the live vanilla', () => {
    expect(SYNC_SERVICES).toContain('/api/beatport/charts/update-phase/');
    expect(SYNC_SERVICES).toContain('/api/listenbrainz/update-phase/');
    expect(SYNC_SERVICES).toContain('/api/tidal/update_phase/');
    expect(SYNC_SERVICES).toContain('/api/spotify-public/update_phase/');
  });

  it('listenbrainz cancel BORROWS the youtube endpoint (no LB cancel exists)', () => {
    expect(SYNC_SOURCES.listenbrainz.api.syncCancel('mbid-1')).toBe(
      '/api/youtube/sync/cancel/mbid-1',
    );
    // The vanilla proves it: no LB cancel route anywhere in the family.
    expect(SYNC_SERVICES).not.toContain('/api/listenbrainz/sync/cancel');
  });

  it('beatport state + reset ride the charts endpoints', () => {
    expect(SYNC_SOURCES.beatport.api.state?.('h')).toBe('/api/beatport/charts/status/h');
    expect(SYNC_SOURCES.beatport.api.reset?.('h')).toBe('/api/beatport/charts/update-phase/h');
    expect(SYNC_SOURCES.youtube.api.reset?.('h')).toBe('/api/youtube/reset/h');
  });

  it('standard verticals share the /discovery /sync path shape', () => {
    expect(SYNC_SOURCES.tidal.api.discoveryStart('7')).toBe('/api/tidal/discovery/start/7');
    expect(SYNC_SOURCES.tidal.api.discoveryStatus('7')).toBe('/api/tidal/discovery/status/7');
    expect(SYNC_SOURCES.tidal.api.syncStart('7')).toBe('/api/tidal/sync/start/7');
    expect(SYNC_SOURCES.tidal.api.syncStatus('7')).toBe('/api/tidal/sync/status/7');
    expect(SYNC_SOURCES.itunes_link.api.state?.('h')).toBe('/api/itunes-link/state/h');
    expect(SYNC_SOURCES.qobuz.api.playlistsStates).toBe('/api/qobuz/playlists/states');
    // Mirrored rides the youtube vertical with its own hash prefix.
    expect(SYNC_SOURCES.mirrored.api.discoveryStart('mirrored_5')).toBe(
      '/api/youtube/discovery/start/mirrored_5',
    );
  });
});

describe('id spaces', () => {
  it('the spotify_public modal-hash/vpid pair stays inconsistent on purpose', () => {
    expect(SYNC_SOURCES.spotify_public.ids.fakeHashPrefix).toBe('spotifypublic_');
    expect(SYNC_SOURCES.spotify_public.ids.vpidPrefix).toBe('spotify_public_');
    expect(SYNC_SERVICES).toContain('spotifypublic_');
  });

  it('beatport, listenbrainz and youtube use bare registry keys', () => {
    expect(SYNC_SOURCES.beatport.ids.fakeHashPrefix).toBe('');
    expect(SYNC_SOURCES.listenbrainz.ids.fakeHashPrefix).toBe('');
    expect(SYNC_SOURCES.youtube.ids.fakeHashPrefix).toBe('');
    expect(SYNC_SOURCES.mirrored.ids.fakeHashPrefix).toBe('mirrored_');
  });

  it('state flags name the shared-modal dispatch booleans', () => {
    expect(SYNC_SOURCES.tidal.ids.stateFlag).toBe('is_tidal_playlist');
    expect(SYNC_SOURCES.listenbrainz.ids.stateFlag).toBe('is_listenbrainz_playlist');
    expect(SYNC_SOURCES.youtube.ids.stateFlag).toBe(''); // the ELSE branch
  });
});

describe('transports', () => {
  it('discovery poll policy: skip-when-connected is exactly deezer + the two link clones', () => {
    const skippers = SOURCE_IDS.filter(
      (id) => SYNC_SOURCES[id].discovery.pollPolicy === 'skip-when-connected',
    );
    expect(skippers.sort()).toEqual(['deezer', 'itunes_link', 'spotify_public']);
  });

  it('discovery cadence is 1000ms everywhere except beatport 2000ms', () => {
    for (const id of SOURCE_IDS) {
      expect(SYNC_SOURCES[id].discovery.pollMs, id).toBe(id === 'beatport' ? 2000 : 1000);
    }
    expect(SYNC_SOURCES.beatport.sync.pollMs).toBe(2000);
    expect(SYNC_SOURCES.tidal.sync.pollMs).toBe(1000);
  });

  it('wing-it handling (live bug #4): socket transforms only, and never yt/beatport/LB/mirrored', () => {
    const socketWingIt = SOURCE_IDS.filter((id) => SYNC_SOURCES[id].discovery.wingItInSocket);
    expect(socketWingIt.sort()).toEqual([
      'deezer',
      'itunes_link',
      'qobuz',
      'spotify_public',
      'tidal',
    ]);
    for (const id of SOURCE_IDS) {
      expect(SYNC_SOURCES[id].discovery.wingItInPoll, id).toBe(false);
    }
  });

  it('discovery start bodies: LB sends the playlist, beatport the chart, nobody else anything', () => {
    expect(SYNC_SOURCES.listenbrainz.discovery.startBody).toBe('playlist');
    expect(SYNC_SOURCES.beatport.discovery.startBody).toBe('chart_data');
    for (const id of SOURCE_IDS.filter((s) => s !== 'listenbrainz' && s !== 'beatport')) {
      expect(SYNC_SOURCES[id].discovery.startBody, id).toBe('none');
    }
  });

  it('the alternate matched/total percent formula is exactly beatport + listenbrainz', () => {
    const alternates = SOURCE_IDS.filter(
      (id) => SYNC_SOURCES[id].sync.percentFormula === 'matched',
    );
    expect(alternates.sort()).toEqual(['beatport', 'listenbrainz']);
  });
});

describe('ux drift', () => {
  it('only Tidal got the #867 open-modal-immediately flow', () => {
    const openFirst = SOURCE_IDS.filter((id) => SYNC_SOURCES[id].ux.openModalImmediately);
    expect(openFirst).toEqual(['tidal']);
  });

  it("only Qobuz accepts the bare 'Found' literal", () => {
    const qobuzVariant = SOURCE_IDS.filter((id) => SYNC_SOURCES[id].ux.foundVariant === 'qobuz');
    expect(qobuzVariant).toEqual(['qobuz']);
  });

  it('only Deezer renders the check-note spans card progress', () => {
    const spans = SOURCE_IDS.filter(
      (id) => SYNC_SOURCES[id].ux.cardProgressFormat === 'check-note-spans',
    );
    expect(spans).toEqual(['deezer']);
  });

  it('hero labels agree with the heroSourceLabel ladder in -sync.core', () => {
    expect(SYNC_SOURCES.spotify_public.heroLabel).toBe('Spotify');
    expect(SYNC_SOURCES.itunes_link.heroLabel).toBe('iTunes');
    expect(SYNC_SOURCES.mirrored.heroLabel).toBe('SoulSync');
    expect(SYNC_SOURCES.youtube.heroLabel).toBe('YouTube');
  });
});

describe('resolvers', () => {
  it('sourceForFakeHash resolves every prefixed key and refuses bare ones', () => {
    expect(sourceForFakeHash('tidal_123')?.id).toBe('tidal');
    expect(sourceForFakeHash('qobuz_9')?.id).toBe('qobuz');
    expect(sourceForFakeHash('deezer_9')?.id).toBe('deezer');
    expect(sourceForFakeHash('spotifypublic_h4sh')?.id).toBe('spotify_public');
    expect(sourceForFakeHash('itunes_link_h4sh')?.id).toBe('itunes_link');
    expect(sourceForFakeHash('mirrored_5')?.id).toBe('mirrored');
    // Bare mbids / chart hashes / yt url-hashes need flag dispatch, not prefixes.
    expect(sourceForFakeHash('0b5eff34-mbid')).toBe(null);
    expect(sourceForFakeHash('a1b2c3chart')).toBe(null);
  });

  it('sourceForVpid resolves the download-engine prefix ladder', () => {
    expect(sourceForVpid('listenbrainz_m')?.id).toBe('listenbrainz');
    expect(sourceForVpid('deezer_9')?.id).toBe('deezer');
    expect(sourceForVpid('beatport_h')?.id).toBe('beatport');
    expect(sourceForVpid('tidal_1')?.id).toBe('tidal');
    expect(sourceForVpid('qobuz_1')?.id).toBe('qobuz');
    expect(sourceForVpid('youtube_h')?.id).toBe('youtube');
    expect(sourceForVpid('spotify_public_h')?.id).toBe('spotify_public');
    expect(sourceForVpid('itunes_link_h')?.id).toBe('itunes_link');
    expect(sourceForVpid('spotify:playlist:x')).toBe(null); // account playlists are not a vertical
  });
});
