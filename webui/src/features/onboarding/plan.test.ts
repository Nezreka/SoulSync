import { describe, expect, it } from 'vitest';

import type { Draft } from './types';

import {
  buildSettingsPatch,
  buildVideoDownloadConfig,
  canContinue,
  emptyDraft,
  folderRows,
  formatBytes,
  visibleSteps,
} from './plan';

const BARE = {
  download: './downloads',
  library: './Transfer',
  staging: './Staging',
  movies: '',
  tv: '',
  youtube: '',
  podcasts: './podcasts',
  audiobooks: './audiobooks',
};

function draft(fn?: (d: Draft) => void): Draft {
  const d = emptyDraft({ folders: BARE });
  d.account.name = 'Boulder';
  d.account.pin = '1234';
  d.server.kind = 'soulsync';
  fn?.(d);
  return d;
}

describe('steps follow what setup was told it is for', () => {
  it('music only skips movies and tv', () => {
    expect(visibleSteps(['music'])).toEqual([
      'account',
      'purpose',
      'server',
      'folders',
      'sources',
      'metadata',
      'library',
      'ready',
    ]);
  });

  it('video only skips every music step', () => {
    expect(visibleSteps(['video'])).toEqual([
      'account',
      'purpose',
      'server',
      'folders',
      'video',
      'ready',
    ]);
  });

  it('folders only ask for what was picked', () => {
    expect(folderRows(['podcasts'])).toEqual(['podcasts']);
    expect(folderRows(['music', 'video'])).toEqual([
      'download',
      'library',
      'staging',
      'movies',
      'tv',
    ]);
  });
});

describe('gates only block what would leave the install broken', () => {
  it('needs a name and a real pin', () => {
    expect(
      canContinue(
        'account',
        draft((d) => void (d.account.name = ' ')),
      ).ok,
    ).toBe(false);
    expect(
      canContinue(
        'account',
        draft((d) => void (d.account.pin = '12')),
      ).ok,
    ).toBe(false);
    expect(canContinue('account', draft()).ok).toBe(true);
    expect(
      canContinue(
        'account',
        draft((d) => void (d.account.lock = 'open')),
      ).ok,
    ).toBe(true);
  });

  it('a password is at least 8 characters', () => {
    const d = draft((x) => {
      x.account.lock = 'password';
      x.account.password = 'short';
    });
    expect(canContinue('account', d).reason).toMatch(/8 characters/);
  });

  it('a picked server has to actually connect', () => {
    const d = draft((x) => void (x.server.kind = 'plex'));
    expect(canContinue('server', d).ok).toBe(false);
    d.server.connected = true;
    expect(canContinue('server', d).ok).toBe(true);
  });

  it('soulseek in the chain needs slskd connected, deezer needs an arl', () => {
    const d = draft((x) => void (x.chain = ['soulseek', 'hifi']));
    expect(canContinue('sources', d).ok).toBe(false);
    d.slskd.connected = true;
    expect(canContinue('sources', d).ok).toBe(true);
    d.chain.push('deezer_dl');
    expect(canContinue('sources', d).ok).toBe(false);
  });

  it('spotify as the catalogue needs its keys', () => {
    const d = draft((x) => void (x.catalogue = 'spotify'));
    expect(canContinue('metadata', d).ok).toBe(false);
    d.spotify = { clientId: 'a', clientSecret: 'b' };
    expect(canContinue('metadata', d).ok).toBe(true);
  });
});

describe('the settings the ready step saves', () => {
  it('bare metal saves the folders it was given, never a container path', () => {
    // the old wizard forced /app/downloads on every install
    const patch = buildSettingsPatch(draft());
    expect(patch.soulseek).toMatchObject({
      download_path: './downloads',
      transfer_path: './Transfer',
    });
    expect(JSON.stringify(patch)).not.toContain('/app/');
  });

  it('two sources become a hybrid chain in order, one stays a plain mode', () => {
    expect(buildSettingsPatch(draft()).download_source).toEqual({
      mode: 'hybrid',
      hybrid_order: ['hifi', 'youtube'],
    });
    expect(buildSettingsPatch(draft((d) => void (d.chain = ['youtube']))).download_source).toEqual({
      mode: 'youtube',
    });
  });

  it('slskd credentials only go in when soulseek is a source', () => {
    const d = draft((x) => {
      x.slskd = { url: 'http://slskd:5030', apiKey: 'k', connected: true };
    });
    expect(buildSettingsPatch(d).soulseek).not.toHaveProperty('slskd_url');
    d.chain = ['soulseek'];
    expect(buildSettingsPatch(d).soulseek).toMatchObject({
      slskd_url: 'http://slskd:5030',
      api_key: 'k',
    });
  });

  it('the media server section uses the config keys each server reads', () => {
    const d = draft((x) => {
      x.server = {
        ...x.server,
        kind: 'jellyfin',
        url: 'http://jf:8096',
        apiKey: 'abc',
        connected: true,
      };
    });
    const patch = buildSettingsPatch(d);
    expect(patch.active_media_server).toBe('jellyfin');
    expect(patch.jellyfin).toEqual({ base_url: 'http://jf:8096', api_key: 'abc' });
  });

  it('no server saves the standalone library and no server section', () => {
    const patch = buildSettingsPatch(draft());
    expect(patch.active_media_server).toBe('soulsync');
    expect(patch).not.toHaveProperty('plex');
  });

  it('a video-only install touches no music settings', () => {
    const d = draft((x) => {
      x.purposes = ['video'];
      x.folders.movies = '/media/movies';
      x.video.prowlarr = { url: 'http://p:9696', apiKey: 'k' };
      x.video.client.url = 'http://q:8080';
    });
    const patch = buildSettingsPatch(d);
    expect(patch).not.toHaveProperty('download_source');
    expect(patch).not.toHaveProperty('metadata');
    expect(patch.prowlarr).toEqual({ url: 'http://p:9696', api_key: 'k' });
    expect(patch.torrent_client).toMatchObject({ type: 'qbittorrent', url: 'http://q:8080' });
    expect(buildVideoDownloadConfig(d)).toEqual({
      movies_path: '/media/movies',
      download_mode: 'torrent',
    });
  });

  it('nothing video-side is saved when video was not picked', () => {
    expect(buildVideoDownloadConfig(draft())).toBeNull();
  });
});

it('formats free space', () => {
  expect(formatBytes(1.8 * 1024 ** 4)).toBe('1.8 TB');
  expect(formatBytes(512)).toBe('512 B');
  expect(formatBytes(null)).toBe('');
});
