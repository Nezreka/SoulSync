// behavioral harness for webui/static/profile-api-gate.js, run under node 18+.
// exits non-zero with a message on any failure.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, '..', '..', 'webui', 'static', 'profile-api-gate.js'), 'utf8');

let wire = [];
const fakeFetch = (input) => {
    wire.push(typeof input === 'string' ? input : input.url);
    return Promise.resolve(new Response('{"ok":true}', { status: 200 }));
};

function boot({ base = '' } = {}) {
    wire = [];
    globalThis.window = {
        fetch: fakeFetch,
        location: { origin: 'http://localhost:8008', href: 'http://localhost:8008/dashboard' },
        SoulSyncURL: base ? {
            strip: (p) => (p === base || p.startsWith(base + '/')) ? p.slice(base.length) || '/' : p,
        } : undefined,
    };
    (0, eval)(src);
    return globalThis.window;
}

function assert(cond, msg) {
    if (!cond) { console.error('FAIL: ' + msg); process.exit(1); }
}

const MEMBER = { id: 4, is_admin: false, allowed_sides: 'music' };
const VIDEO_MEMBER = { id: 5, is_admin: false, allowed_sides: 'both' };
const ADMIN = { id: 1, is_admin: true };

const t = async () => {
    // 1) the member's page load from the issue: none of it reaches the wire
    let w = boot();
    w.SoulSyncProfileGate.setProfile(MEMBER);
    const issue = [
        '/api/settings', '/api/settings/config-status', '/api/video/dashboard',
        '/api/video/youtube/channels', '/api/video/watchlist/counts',
        '/api/video/scan/server/status?media_type=all', '/api/video/overlays/apply/status',
    ];
    const answers = await Promise.all(issue.map((p) => w.fetch(p)));
    assert(wire.length === 0, `member calls must not hit the wire, sent ${wire.join(', ')}`);
    assert(answers.every((r) => r.status === 403), 'refused calls answer 403 like the server');
    const body = await answers[2].json();
    assert(body.success === false && /Video access/.test(body.error), 'body carries the server error');

    // 2) everything else passes untouched
    await w.fetch('/api/discover/weekly');
    await w.fetch('/api/settingsish');
    await w.fetch('https://example.com/api/settings');
    assert(wire.length === 3, `ungated calls go through, sent ${wire.length}`);

    // 3) a video member gets the content api, not the admin surfaces
    w = boot();
    w.SoulSyncProfileGate.setProfile(VIDEO_MEMBER);
    assert((await w.fetch('/api/video/dashboard')).status === 200, 'video member reads the dashboard');
    assert((await w.fetch('/api/video/collections/sync/status')).status === 403, 'collections are admin-only');
    assert((await w.fetch('/api/video/enrichment/config')).status === 403, 'enrichment config is admin-only');
    assert((await w.fetch('/api/video/enrichment/status')).status === 200, 'enrichment status stays open');
    assert(wire.length === 2, `only the allowed two hit the wire, sent ${wire.join(', ')}`);

    // 4) admin: nothing gated
    w = boot();
    w.SoulSyncProfileGate.setProfile(ADMIN);
    await w.fetch('/api/settings');
    await w.fetch('/api/video/overlays/apply/status');
    assert(wire.length === 2, 'admin calls all go through');

    // 5) calls made before the profile is known wait for it
    w = boot();
    const early = w.fetch('/api/video/dashboard');
    const plain = w.fetch('/api/status');
    await plain;
    assert(wire.length === 1, 'ungated calls never wait');
    w.SoulSyncProfileGate.setProfile(MEMBER);
    assert((await early).status === 403 && wire.length === 1, 'an early member call is refused once known');
    w = boot();
    const early2 = w.fetch('/api/settings');
    w.SoulSyncProfileGate.setProfile(null);
    assert((await early2).status === 200, 'unknown profile lets calls through as before');

    // 6) a reverse-proxy sub-path still matches, and Request/URL inputs work
    w = boot({ base: '/soulsync' });
    w.SoulSyncProfileGate.setProfile(MEMBER);
    assert((await w.fetch('/soulsync/api/settings')).status === 403, 'sub-path settings refused');
    assert((await w.fetch(new Request('http://localhost:8008/soulsync/api/video/scan/status'))).status === 403,
        'Request input refused');
    assert((await w.fetch(new URL('http://localhost:8008/api/video/issues/counts'))).status === 403,
        'URL input refused');
    assert(wire.length === 0, 'none of those hit the wire');

    console.log('all assertions passed');
};
t().catch((e) => { console.error('FAIL: ' + (e && e.stack || e)); process.exit(1); });
