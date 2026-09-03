// Tests the Library-maintenance progress-card TITLE in `webui/static/downloads.js`.
//
//     node --test tests/static/test_repair_card_names.mjs
//
// #1211 (wishx): four maintenance jobs running at once produced four cards all
// titled "Library maintenance", so there was no way to tell which was which or
// which one to stop. The server had been sending the job's display_name in the
// progress state the whole time (_repair_job_start in web_server.py); the card
// read t.name / t.job_name, neither of which exists, and fell through to the
// generic label every single time.

import { test, describe, before } from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const DOWNLOADS_PATH = resolve(__dirname, '..', '..', 'webui', 'static', 'downloads.js');

function lift(source, name) {
    const start = source.indexOf(`function ${name}(`);
    assert.ok(start !== -1, `${name} not found in downloads.js`);
    let i = source.indexOf('{', start);
    let depth = 0;
    for (; i < source.length; i++) {
        if (source[i] === '{') depth++;
        else if (source[i] === '}') { depth--; if (depth === 0) { i++; break; } }
    }
    return source.slice(start, i);
}

let sandbox;
before(() => {
    const source = readFileSync(DOWNLOADS_PATH, 'utf8');
    // Only the card builder is under test. Its helpers are stubbed so the test
    // reports the TITLE it chose rather than a wall of markup.
    const code = [
        lift(source, '_musicRepairActiveHTML'),
        // stubs
        'function _taskCardHTML(title) { return "TITLE:" + title; }',
        'function _notifActionHTML() { return ""; }',
        'function _escToast(s) { return String(s); }',
        'function _taskPct() { return 0; }',
        'function _taskHasPct() { return true; }',
        'function _taskClampPct(p) { return p; }',
    ].join('\n');
    sandbox = { _musicRepairTasks: {} };
    vm.createContext(sandbox);
    vm.runInContext(code, sandbox);
});

function titleFor(state) {
    sandbox._musicRepairTasks = { the_job: { id: 'the_job', status: 'running', ...state } };
    return vm.runInContext('_musicRepairActiveHTML()', sandbox).replace('TITLE:', '').split('<')[0];
}

describe('Library maintenance card title (#1211)', () => {
    test('uses the display_name the server already sends', () => {
        assert.equal(titleFor({ display_name: 'Duplicate Detector' }), 'Duplicate Detector');
    });

    test('two jobs at once are told apart', () => {
        sandbox._musicRepairTasks = {
            a: { id: 'a', status: 'running', display_name: 'Duplicate Detector' },
            b: { id: 'b', status: 'running', display_name: 'Quality Upgrade Finder' },
        };
        const html = vm.runInContext('_musicRepairActiveHTML()', sandbox);
        assert.ok(html.includes('Duplicate Detector'), 'first job not named');
        assert.ok(html.includes('Quality Upgrade Finder'), 'second job not named');
        assert.ok(!html.includes('Library maintenance'), 'still falling back to the generic label');
    });

    test('falls back to the job id before the generic label', () => {
        assert.equal(titleFor({}), 'the_job');
    });

    test('the generic label is the last resort only', () => {
        sandbox._musicRepairTasks = { '': { id: '', status: 'running' } };
        const html = vm.runInContext('_musicRepairActiveHTML()', sandbox);
        assert.ok(html.includes('Library maintenance'));
    });
});
