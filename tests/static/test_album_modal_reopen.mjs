// Reopening an album's download modal after its run finished
// (`webui/static/shared-helpers.js`, `webui/static/downloads.js`).
//
//     node --test tests/static/test_album_modal_reopen.mjs
//
// #1386 (cremonies): "I selected a single song on the album and downloaded
// it. Closed the current page and did other things. Re-searched the album to
// download another part of it. Page loaded with previous event? Refreshed
// browser cleared it." closing a running modal only hides it, so the
// finished run was still registered for that album and opening the album
// again replayed it instead of starting a new check.

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const read = (name) => readFileSync(resolve(__dirname, '..', '..', 'webui', 'static', name), 'utf8');
const HELPERS = read('shared-helpers.js');
const DOWNLOADS = read('downloads.js');

function lift(source, name) {
    const start = source.search(new RegExp(`(async )?function ${name}\\(`));
    assert.ok(start !== -1, `${name} not found`);
    let i = source.indexOf('{', source.indexOf(')', start));
    let depth = 0;
    for (; i < source.length; i++) {
        if (source[i] === '{') depth++;
        else if (source[i] === '}') { depth--; if (depth === 0) { i++; break; } }
    }
    return source.slice(start, i);
}

describe('_isFinishedDownloadProcess', () => {
    const ctx = {};
    vm.createContext(ctx);
    vm.runInContext(lift(HELPERS, '_isFinishedDownloadProcess'), ctx);

    test('complete and cancelled runs are over; running and unstarted ones are not', () => {
        assert.equal(ctx._isFinishedDownloadProcess({ status: 'complete' }), true);
        assert.equal(ctx._isFinishedDownloadProcess({ status: 'cancelled' }), true);
        assert.equal(ctx._isFinishedDownloadProcess({ status: 'running' }), false);
        assert.equal(ctx._isFinishedDownloadProcess({ status: 'idle' }), false);
        assert.equal(ctx._isFinishedDownloadProcess(undefined), false);
    });
});

test('opening an album closes a finished run for it before anything else', () => {
    const body = lift(HELPERS, 'openDownloadMissingModalForArtistAlbum');
    const close = body.indexOf('await closeDownloadMissingModal(virtualPlaylistId)');
    const reuse = body.indexOf('already exists. Showing it.');
    assert.ok(close !== -1, 'a finished run is never closed');
    assert.ok(close < reuse, 'the finished run is closed after the reuse check, so it gets replayed');
    assert.match(body.slice(0, close), /_isFinishedDownloadProcess\(activeDownloadProcesses\[virtualPlaylistId\]\)/);
});

test('closing a finished modal waits until its process is gone (no race with a fresh run)', async () => {
    const order = [];
    const ctx = {
        console: { log() {}, warn() {}, error() {}, debug() {} },
        activeDownloadProcesses: {},
        discoverDownloads: {},
        cleanupSearchDownload() {},
        async handlePostDownloadAutomation() {},
        async cleanupDownloadProcess(id) {
            // the real one awaits a fetch before deleting the process
            await new Promise((r) => setTimeout(r, 10));
            delete ctx.activeDownloadProcesses[id];
            order.push('deleted');
        },
    };
    ctx.activeDownloadProcesses.enhanced_search_x = {
        status: 'complete', modalElement: { style: {} },
    };
    vm.createContext(ctx);
    vm.runInContext(lift(DOWNLOADS, 'closeDownloadMissingModal'), ctx);
    await ctx.closeDownloadMissingModal('enhanced_search_x');
    order.push('close resolved');
    assert.deepEqual(order, ['deleted', 'close resolved']);
    assert.equal(ctx.activeDownloadProcesses.enhanced_search_x, undefined);
});
