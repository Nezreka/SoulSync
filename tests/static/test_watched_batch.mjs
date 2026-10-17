// Tests for how the download modal tells the server it is still on screen
// (`webui/static/downloads.js`).
//
//     node --test tests/static/test_watched_batch.mjs
//
// spectro (discord): picking candidates for the not-found rows of a finished
// download died a few minutes in with "Task not found". the server dropped
// finished batches 5 minutes after completion even with the modal open. the
// poll now names open modals as watching=, and the server counts from that.

import { test, describe, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SOURCE = readFileSync(
    resolve(__dirname, '..', '..', 'webui', 'static', 'downloads.js'), 'utf8');

function lift(name) {
    const start = SOURCE.indexOf(`function ${name}(`);
    assert.ok(start !== -1, `${name} not found in downloads.js`);
    let i = SOURCE.indexOf('{', start);
    let depth = 0;
    for (; i < SOURCE.length; i++) {
        if (SOURCE[i] === '{') depth++;
        else if (SOURCE[i] === '}') { depth--; if (depth === 0) { i++; break; } }
    }
    return SOURCE.slice(start, i);
}

const modal = (display, isConnected = true) => ({ style: { display }, isConnected });

let sb;
beforeEach(() => {
    sb = { activeDownloadProcesses: {}, calls: [], Date };
    sb.fetch = (url, opts) => { sb.calls.push({ url, body: JSON.parse(opts.body) }); return Promise.resolve(); };
    vm.createContext(sb);
    vm.runInContext([
        lift('_modalIsOpen'), lift('_batchStatusQuery'),
        'var _HIDDEN_KEEPALIVE_MS = 60000; var _lastHiddenKeepalive = 0;',
        lift('_keepWatchedBatchesAlive'),
    ].join('\n'), sb);
});

describe('_batchStatusQuery', () => {
    test('an open modal is sent as watching', () => {
        sb.activeDownloadProcesses.p1 = { batchId: 'b1', modalElement: modal('flex') };
        assert.equal(sb._batchStatusQuery(['b1'], { b1: 'p1' }), 'batch_ids=b1&watching=b1');
    });

    test('a hidden modal still polls but does not keep the batch alive', () => {
        sb.activeDownloadProcesses.p1 = { batchId: 'b1', modalElement: modal('none') };
        assert.equal(sb._batchStatusQuery(['b1'], { b1: 'p1' }), 'batch_ids=b1');
    });

    test('a modal pulled out of the page is not watching', () => {
        sb.activeDownloadProcesses.p1 = { batchId: 'b1', modalElement: modal('flex', false) };
        assert.equal(sb._batchStatusQuery(['b1'], { b1: 'p1' }), 'batch_ids=b1');
    });

    test('no modal element is not watching', () => {
        sb.activeDownloadProcesses.p1 = { batchId: 'b1' };
        assert.equal(sb._batchStatusQuery(['b1'], { b1: 'p1' }), 'batch_ids=b1');
    });

    test('only the open one of several is watching', () => {
        sb.activeDownloadProcesses.p1 = { batchId: 'b1', modalElement: modal('none') };
        sb.activeDownloadProcesses.p2 = { batchId: 'b2', modalElement: modal('flex') };
        assert.equal(sb._batchStatusQuery(['b1', 'b2'], { b1: 'p1', b2: 'p2' }),
            'batch_ids=b1&batch_ids=b2&watching=b2');
    });
});

describe('_keepWatchedBatchesAlive (hidden tab)', () => {
    test('pings the server with only the open modals', () => {
        sb.activeDownloadProcesses.p1 = { batchId: 'b1', modalElement: modal('flex') };
        sb.activeDownloadProcesses.p2 = { batchId: 'b2', modalElement: modal('none') };
        sb.activeDownloadProcesses.p3 = { batchId: 'b3', batchGone: true, modalElement: modal('flex') };
        sb._keepWatchedBatchesAlive(100000);
        assert.equal(sb.calls.length, 1);
        assert.equal(sb.calls[0].url, '/api/download_status/watch');
        assert.deepEqual([...sb.calls[0].body.batch_ids], ['b1']);
    });

    test('at most once a minute', () => {
        sb.activeDownloadProcesses.p1 = { batchId: 'b1', modalElement: modal('flex') };
        sb._keepWatchedBatchesAlive(100000);
        sb._keepWatchedBatchesAlive(130000);
        assert.equal(sb.calls.length, 1);
        sb._keepWatchedBatchesAlive(160001);
        assert.equal(sb.calls.length, 2);
    });

    test('nothing open sends nothing', () => {
        sb.activeDownloadProcesses.p1 = { batchId: 'b1', modalElement: modal('none') };
        sb._keepWatchedBatchesAlive(100000);
        assert.equal(sb.calls.length, 0);
    });
});

test('the hidden-tab branch of the poll sends the keepalive', () => {
    assert.ok(SOURCE.includes('if (document.hidden) { _keepWatchedBatchesAlive(); return; }'));
});

test('both pollers build their query through the helper', () => {
    const uses = SOURCE.split('_batchStatusQuery(activeBatchIds, batchToPlaylistMap)').length - 1;
    assert.equal(uses, 2);
    assert.ok(!SOURCE.includes("activeBatchIds.map(id => `batch_ids=${id}`)"));
});
