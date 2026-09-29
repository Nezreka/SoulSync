// Tests for how the download modal ends a batch the server dropped
// (`webui/static/downloads.js`).
//
//     node --test tests/static/test_vanished_batch.mjs
//
// #1384 (cremonies): after a playlist download was cancelled, reopening the
// playlist's download modal showed the process still running with nothing
// downloading, and the log repeated "Returning status for 0 batches" every
// poll. The batch had been deleted server-side; the modal only learns a batch
// ended through a status update, and a deleted batch never sends one.

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

let sb;
beforeEach(() => {
    sb = {
        activeDownloadProcesses: {},
        cleaned: [],
        toasts: [],
        cleared: [],
    };
    sb.closeDownloadMissingModal = (id) => { sb.cleaned.push(id); delete sb.activeDownloadProcesses[id]; };
    sb.showToast = (msg) => sb.toasts.push(msg);
    sb.clearInterval = (h) => sb.cleared.push(h);
    vm.createContext(sb);
    vm.runInContext(`${lift('_vanishedBatchIds')}\n${lift('_endVanishedProcess')}`, sb);
});

// arrays built inside the vm are from another realm; copy them out so strict
// deepEqual compares values, not prototypes
const vanished = (...args) => Array.from(sb._vanishedBatchIds(...args));

describe('_vanishedBatchIds', () => {
    test('a batch missing twice in a row is gone; once is a blip', () => {
        const misses = {};
        assert.deepEqual(vanished(['a', 'b'], { a: {} }, misses), []);
        assert.deepEqual(vanished(['a', 'b'], { a: {} }, misses), ['b']);
        // counted once, then forgotten: it doesn't fire again
        assert.deepEqual(misses, {});
    });

    test('showing up again resets the count', () => {
        const misses = {};
        vanished(['b'], {}, misses);
        vanished(['b'], { b: {} }, misses);
        assert.deepEqual(vanished(['b'], {}, misses), []);
    });

    test('no batches in the answer at all', () => {
        const misses = {};
        vanished(['a'], undefined, misses);
        assert.deepEqual(vanished(['a'], undefined, misses), ['a']);
    });
});

describe('_endVanishedProcess', () => {
    test('a stuck running process with its modal closed is closed properly, so reopening starts fresh', () => {
        sb.activeDownloadProcesses.p1 = {
            status: 'running', poller: 7, modalElement: { style: { display: 'none' } },
            playlist: { name: 'Road Trip' },
        };
        sb._endVanishedProcess('p1');
        assert.deepEqual([...sb.cleaned], ['p1']);
        assert.equal(sb.activeDownloadProcesses.p1, undefined);
        assert.deepEqual([...sb.cleared], [7]);
    });

    test('with the modal open, it stops running and says so', () => {
        const process = {
            status: 'running', modalElement: { style: { display: 'flex' } },
            playlist: { name: 'Road Trip' },
        };
        sb.activeDownloadProcesses.p1 = process;
        sb._endVanishedProcess('p1');
        assert.equal(process.status, 'cancelled');
        assert.equal(process.batchGone, true);
        assert.equal(sb.cleaned.length, 0);
        assert.match(sb.toasts[0], /Road Trip is no longer running/);
    });

    test('a finished download the server reaped stays viewable, it just stops being polled', () => {
        const process = {
            status: 'complete', modalElement: { style: { display: 'none' } },
            playlist: { name: 'Road Trip' },
        };
        sb.activeDownloadProcesses.p1 = process;
        sb._endVanishedProcess('p1');
        assert.equal(process.status, 'complete');
        assert.equal(process.batchGone, true);
        assert.equal(sb.cleaned.length, 0);
        assert.equal(sb.toasts.length, 0);
        assert.equal(sb.activeDownloadProcesses.p1, process);
    });

    test('an unknown playlist is a no-op', () => {
        sb._endVanishedProcess(undefined);
        sb._endVanishedProcess('nope');
        assert.equal(sb.cleaned.length, 0);
    });
});

test('both global pollers run the check and skip a batch that is gone', () => {
    const calls = SOURCE.split('_vanishedBatchIds(activeBatchIds, data.batches, _batchMissCounts)').length - 1;
    assert.equal(calls, 2);
    const skips = SOURCE.split('process.batchId && !process.batchGone &&').length - 1;
    assert.equal(skips, 2);
});
