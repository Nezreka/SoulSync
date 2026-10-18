// Tests for the surviving compatibility helpers in `webui/static/auto-sync.js`.
//
// The monolithic AutoSync manager was retired and ported to TypeScript
// (webui/src/routes/sync/-sync.autosync.test.ts); the legacy auto-sync.js file
// now retains only source reference resolution and card phase adaptation.
//
// Run via:
//     node --test tests/static/test_auto_sync.mjs

import { test, describe, before } from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const AUTOSYNC_PATH = resolve(__dirname, '..', '..', 'webui', 'static', 'auto-sync.js');

let AUTOSYNC_SOURCE;
before(() => {
    AUTOSYNC_SOURCE = readFileSync(AUTOSYNC_PATH, 'utf8');
});

function makeSandbox() {
    const cardPhases = {};
    const sandbox = {
        window: {},
        document: { getElementById: () => null, body: {}, querySelectorAll: () => [] },
        console: { debug: () => {}, error: () => {}, log: () => {} },
        fetch: async () => { throw new Error('fetch not stubbed for this test'); },
        showToast: () => {},
        loadMirroredPlaylists: () => {},
        updateMirroredCardPhase: (hash, phase) => { cardPhases[hash] = phase; },
        openMirroredPlaylistModal: () => {},
        closeMirroredModal: () => {},
        youtubePlaylistStates: {},
        _cardPhases: cardPhases,
    };
    vm.createContext(sandbox);
    vm.runInContext(AUTOSYNC_SOURCE, sandbox);
    return sandbox;
}

// =========================================================================
// getMirroredSourceRef — playlist source URL resolution
// =========================================================================

describe('getMirroredSourceRef', () => {
    test('explicit source_ref wins', () => {
        const sb = makeSandbox();
        assert.equal(
            sb.getMirroredSourceRef({ source_ref: 'https://example.com/x' }),
            'https://example.com/x',
        );
    });

    test('falls back to description URL for spotify_public', () => {
        const sb = makeSandbox();
        const p = {
            source: 'spotify_public',
            description: 'https://open.spotify.com/playlist/abc',
        };
        assert.equal(sb.getMirroredSourceRef(p), 'https://open.spotify.com/playlist/abc');
    });

    test('falls back to description URL for youtube', () => {
        const sb = makeSandbox();
        const p = {
            source: 'youtube',
            description: 'https://www.youtube.com/playlist?list=PL123',
        };
        assert.equal(sb.getMirroredSourceRef(p), 'https://www.youtube.com/playlist?list=PL123');
    });

    test('non-URL description ignored, falls through to source_playlist_id', () => {
        const sb = makeSandbox();
        const p = {
            source: 'spotify_public',
            description: 'just a note about this playlist',
            source_playlist_id: 'abc123',
        };
        assert.equal(sb.getMirroredSourceRef(p), 'abc123');
    });

    test('empty playlist returns empty string', () => {
        const sb = makeSandbox();
        assert.equal(sb.getMirroredSourceRef({}), '');
        assert.equal(sb.getMirroredSourceRef(null), '');
    });
});

// =========================================================================
// applyMirroredPipelineState — updates card phase and playlist state
// =========================================================================

describe('applyMirroredPipelineState', () => {
    test('running status sets pipeline_running phase', () => {
        const sb = makeSandbox();
        sb.applyMirroredPipelineState('pl1', { status: 'running', progress: 42, phase: 'matching' });
        assert.equal(sb.youtubePlaylistStates['mirrored_pl1'].phase, 'pipeline_running');
        assert.equal(sb.youtubePlaylistStates['mirrored_pl1'].pipeline_status, 'running');
        assert.equal(sb.youtubePlaylistStates['mirrored_pl1'].pipeline_progress, 42);
        assert.equal(sb._cardPhases['mirrored_pl1'], 'pipeline_running');
    });

    test('finished status sets pipeline_complete phase', () => {
        const sb = makeSandbox();
        sb.applyMirroredPipelineState('pl2', { status: 'finished', result: { synced: 10 } });
        assert.equal(sb.youtubePlaylistStates['mirrored_pl2'].phase, 'pipeline_complete');
        assert.equal(sb._cardPhases['mirrored_pl2'], 'pipeline_complete');
    });

    test('error or skipped status sets pipeline_error phase', () => {
        const sb = makeSandbox();
        sb.applyMirroredPipelineState('pl3', { status: 'error', error: 'boom' });
        assert.equal(sb.youtubePlaylistStates['mirrored_pl3'].phase, 'pipeline_error');
        assert.equal(sb._cardPhases['mirrored_pl3'], 'pipeline_error');

        sb.applyMirroredPipelineState('pl4', { status: 'skipped' });
        assert.equal(sb.youtubePlaylistStates['mirrored_pl4'].phase, 'pipeline_error');
    });
});
