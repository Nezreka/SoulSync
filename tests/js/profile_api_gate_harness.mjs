// behavioral harness for webui/static/profile-api-gate.js, run under node 18+.
// argv[2] is the server's client_gate_rules() as json (the pytest wrapper
// passes the live ones). exits non-zero with a message on any failure.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, '..', '..', 'webui', 'static', 'profile-api-gate.js'), 'utf8');

const RULES = JSON.parse(process.argv[2] || 'null');
if (!RULES) { console.error('FAIL: pass client_gate_rules() json as argv[2]'); process.exit(1); }

let wire = [];
const fakeFetch = (input) => {
    wire.push(typeof input === 'string' ? input : input.url);
    return Promise.resolve(new Response('{"ok":true}', { status: 200 }));
};

function boot({ base = '', rules = RULES } = {}) {
    wire = [];
    globalThis.window = {
        fetch: fakeFetch,
        SOULSYNC_PROFILE_GATE: rules,
        location: { origin: 'http://localhost:8008', href: 'http://localhost:8008/dashboard' },
        SoulSyncURL: base ? {
            strip: (p) => (p === base || p.startsWith(base + '/')) ? p.slice(base.length) || '/' : p,
        } : undefined,
    };
    (0, eval)(src);
    return globalThis.window;
}

// a booted gate with the app already in as this profile
function bootAs(profile, opts) {
    const w = boot(opts);
    w.SoulSyncProfileGate.setProfile(profile);
    w.SoulSyncProfileGate.open();
    return w;
}

const tick = () => new Promise((r) => setTimeout(r, 10));

function assert(cond, msg) {
    if (!cond) { console.error('FAIL: ' + msg); process.exit(1); }
}

const MEMBER = { id: 4, is_admin: false, allowed_sides: 'music' };
const VIDEO_MEMBER = { id: 5, is_admin: false, allowed_sides: 'both' };
const ADMIN = { id: 1, is_admin: true };

const t = async () => {
    // 1) the member's page load from the issue: none of it reaches the wire
    let w = bootAs(MEMBER);
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
    w = bootAs(VIDEO_MEMBER);
    assert((await w.fetch('/api/video/dashboard')).status === 200, 'video member reads the dashboard');
    assert((await w.fetch('/api/video/collections/sync/status')).status === 403, 'collections are admin-only');
    assert((await w.fetch('/api/video/enrichment/config')).status === 403, 'enrichment config is admin-only');
    assert((await w.fetch('/api/video/enrichment/status')).status === 200, 'enrichment status stays open');
    assert(wire.length === 2, `only the allowed two hit the wire, sent ${wire.join(', ')}`);

    // 4) admin: nothing gated
    w = bootAs(ADMIN);
    await w.fetch('/api/settings');
    await w.fetch('/api/video/overlays/apply/status');
    assert(wire.length === 2, 'admin calls all go through');

    // 5) before the app opens, data calls wait; the picker's own calls don't
    w = boot();
    const held = [w.fetch('/api/weather'), w.fetch('/api/repair/status'), w.fetch('/api/video/dashboard')];
    await Promise.all([
        w.fetch('/api/profiles/current'), w.fetch('/api/profiles'), w.fetch('/api/setup/status'),
        w.fetch('/api/profiles/3/avatar'), w.fetch('/api/auth/plex/available'),
        w.fetch('/api/profiles/select', { method: 'POST' }),
        w.fetch('/api/profiles/verify-launch-pin', { method: 'POST' }),
        w.fetch('/api/auth/login', { method: 'POST' }),
        w.fetch('/static/style.css'), w.fetch('/status'),
    ]);
    await tick();
    assert(wire.length === 10, `the picker and sign-in calls go straight out, sent ${wire.length}`);
    assert(!wire.some((u) => /weather|repair|video/.test(u)), 'data calls must wait for a profile');
    w.SoulSyncProfileGate.setProfile(MEMBER);
    w.SoulSyncProfileGate.open();
    const [weather, repair, video] = await Promise.all(held);
    assert(weather.status === 200 && repair.status === 200, 'held data calls go out once the app opens');
    assert(video.status === 403, 'a held call is judged by the profile it opened with');

    // 5b) a POST to an open GET path still waits (method matters)
    w = boot();
    let settled = false;
    w.fetch('/api/setup/status', { method: 'POST' }).then(() => { settled = true; });
    w.fetch(new Request('http://localhost:8008/api/profiles/select', { method: 'POST' }));
    await tick();
    assert(!settled, 'a POST to a GET-only open path waits');
    assert(wire.length === 1, 'a Request POST to an open POST path goes out');

    // 5c) opened with no profile (init failed, or the setup wizard): all through
    w = boot();
    const early = w.fetch('/api/settings');
    w.SoulSyncProfileGate.open();
    assert((await early).status === 200, 'no profile to judge by lets calls through as before');

    // 5d) no rules on the page: nothing is held
    w = boot({ rules: null });
    await w.fetch('/api/weather');
    assert(wire.length === 1, 'without rules the gate never holds');

    // 5e) page_denied: a page api the profile isn't given
    w = bootAs({ id: 6, is_admin: false, allowed_pages: ['dashboard', 'search'] });
    assert((await w.fetch('/api/listening-stats/overview')).status === 403, 'stats api refused without the page');
    assert((await w.fetch('/api/import/inbox')).status === 403, 'import api refused without the page');
    assert((await w.fetch('/api/discover/weekly')).status === 200, 'shared apis stay open');
    w = bootAs({ id: 6, is_admin: false, allowed_pages: ['stats'] });
    assert((await w.fetch('/api/listening-stats/overview')).status === 200, 'given page passes');
    w = bootAs({ id: 6, is_admin: false, allowed_pages: null });
    assert((await w.fetch('/api/audiobooks/library')).status === 200, 'null pages = every page');

    // 6) a reverse-proxy sub-path still matches, and Request/URL inputs work
    w = bootAs(MEMBER, { base: '/soulsync' });
    assert((await w.fetch('/soulsync/api/settings')).status === 403, 'sub-path settings refused');
    assert((await w.fetch(new Request('http://localhost:8008/soulsync/api/video/scan/status'))).status === 403,
        'Request input refused');
    assert((await w.fetch(new URL('http://localhost:8008/api/video/issues/counts'))).status === 403,
        'URL input refused');
    assert(wire.length === 0, 'none of those hit the wire');

    console.log('all assertions passed');
};
await t();
