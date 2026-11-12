// Tests for the Shelf Life panel on the detail page (`webui/static/video/video-detail.js`).
//
//     node --test tests/static/test_shelf_life_detail.mjs
//
// admins see the stored score in its band (or the gate), the top reasons, when
// it was scored, and a Keep toggle; members never fetch it.

import { test, describe, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SOURCE = readFileSync(
    resolve(__dirname, '..', '..', 'webui', 'static', 'video', 'video-detail.js'), 'utf8');

function lift(name) {
    const start = SOURCE.indexOf(`function ${name}(`);
    assert.ok(start !== -1, `${name} not found in video-detail.js`);
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
    sb = { Date, isNaN, Math, String, calls: [], ctx: { isAdmin: true } };
    sb.getCurrentProfileContext = () => sb.ctx;
    sb.fetch = (url) => { sb.calls.push(url); return new Promise(() => {}); };
    sb.renderShelf = () => {};
    vm.createContext(sb);
    vm.runInContext(['esc', '_isAdmin', 'shelfAgo', 'shelfPanelHtml', 'loadShelfLife'].map(lift).join('\n') +
        '\nvar currentId = 5, currentKind = "movie";', sb);
});

const ago = (hours) => new Date(Date.now() - hours * 3600000).toISOString().slice(0, 19).replace('T', ' ');
const view = (over) => ({ kind: 'movie', item_id: 5, scored: true, score: 12, gate: null, threshold: 30,
    reasons: [{ signal: 'watch', text: 'nobody has watched it' }], scored_at: ago(3),
    keep: false, can_keep: true, enabled: true, ...over });

describe('shelfPanelHtml', () => {
    test('a flagged title shows its score in red, the threshold, its reasons and when', () => {
        const html = sb.shelfPanelHtml(view());
        assert.match(html, /vd-shelf-chip--low">12</);
        assert.match(html, /Flagged for cleanup \(under 30\)/);
        assert.match(html, /<li>nobody has watched it<\/li>/);
        assert.match(html, /scored 3 hours ago/);
        assert.match(html, /data-vd-shelf-keep="1"[^>]*>Keep</);
    });

    test('at or above the threshold is not flagged; the band follows the threshold', () => {
        assert.match(sb.shelfPanelHtml(view({ score: 40 })), /vd-shelf-chip--mid/);
        assert.match(sb.shelfPanelHtml(view({ score: 45 })), /vd-shelf-chip--ok/);
        assert.doesNotMatch(sb.shelfPanelHtml(view({ score: 30 })), /Flagged/);
        assert.match(sb.shelfPanelHtml(view({ score: 45, threshold: 50 })), /Flagged for cleanup \(under 50\)/);
    });

    test('a gated title shows the gate, not a score or reasons', () => {
        const html = sb.shelfPanelHtml(view({ score: null, gate: 'in_progress', gate_text: 'someone is part-way through it' }));
        assert.match(html, /Protected/);
        assert.match(html, /someone is part-way through it/);
        assert.doesNotMatch(html, /<li>/);
    });

    test('kept wins over the stored score, and the button takes it off', () => {
        const html = sb.shelfPanelHtml(view({ keep: true }));
        assert.match(html, /Kept/);
        assert.doesNotMatch(html, /vd-shelf-chip--low/);
        assert.match(html, /data-vd-shelf-keep="0" aria-pressed="true"/);
    });

    test('not scored yet says when it will be', () => {
        assert.match(sb.shelfPanelHtml(view({ scored: false })), /next Shelf Life run/);
    });

    test('no TMDB match means no Keep button', () => {
        assert.doesNotMatch(sb.shelfPanelHtml(view({ can_keep: false })), /data-vd-shelf-keep/);
    });

    test('reasons are escaped', () => {
        assert.match(sb.shelfPanelHtml(view({ reasons: [{ signal: 'x', text: '<b>' }] })), /&lt;b&gt;/);
    });
});

describe('shelfAgo', () => {
    test('hours, then days', () => {
        assert.equal(sb.shelfAgo(ago(0)), 'scored just now');
        assert.equal(sb.shelfAgo(ago(1)), 'scored 1 hour ago');
        assert.equal(sb.shelfAgo(ago(72)), 'scored 3 days ago');
        assert.equal(sb.shelfAgo(null), '');
    });
});

describe('loadShelfLife', () => {
    test('an admin fetches it', () => {
        sb.loadShelfLife('movie', 5);
        assert.deepEqual(sb.calls, ['/api/video/repair/shelf-life/movie/5']);
    });

    test('the job switched off shows nothing, even a stored score', async () => {
        const shown = [];
        sb.renderShelf = (v) => shown.push(v);
        for (const enabled of [false, true]) {
            sb.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({ success: true, ...view({ enabled }) }) });
            vm.runInContext(lift('loadShelfLife'), sb);
            sb.loadShelfLife('movie', 5);
            await new Promise((r) => setTimeout(r, 0));
        }
        // each load clears first (null), then renders what came back
        assert.deepEqual(shown.map((v) => v && v.score), [null, null, null, 12]);
    });

    test('a member never asks', () => {
        sb.ctx = { isAdmin: false };
        sb.loadShelfLife('movie', 5);
        sb.ctx = null;
        sb.loadShelfLife('movie', 5);
        assert.deepEqual(sb.calls, []);
    });
});
