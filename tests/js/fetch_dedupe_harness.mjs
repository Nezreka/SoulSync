// Behavioral harness for webui/static/fetch-dedupe.js — run under node 18+
// (real Response objects, so the clone-per-consumer semantics are truly
// exercised). Exits non-zero with a message on any failure.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, '..', '..', 'webui', 'static', 'fetch-dedupe.js'), 'utf8');

let rawCalls = [];
let nextResponse = () => new Response(JSON.stringify({ n: rawCalls.length }), {
    status: 200, headers: { 'Content-Type': 'application/json' } });
let failNext = false;

const fakeFetch = (input, init) => {
    rawCalls.push({ input, init });
    if (failNext) { failNext = false; return Promise.resolve(new Response('nope', { status: 500 })); }
    return Promise.resolve(nextResponse());
};

globalThis.window = {
    fetch: fakeFetch,
    location: { origin: 'http://localhost:8008' },
};

(0, eval)(src);
const patched = globalThis.window.fetch;
const hook = globalThis.window._apiGetDedupe;

function assert(cond, msg) {
    if (!cond) { console.error('FAIL: ' + msg); process.exit(1); }
}

const t = async () => {
    // 1) burst dedupe: two identical /api GETs → ONE raw call, both bodies readable
    rawCalls = [];
    const [a, b] = await Promise.all([patched('/api/video/issues/counts'), patched('/api/video/issues/counts')]);
    assert(rawCalls.length === 1, `burst should make 1 raw call, made ${rawCalls.length}`);
    const [ja, jb] = await Promise.all([a.json(), b.json()]);
    assert(ja.n === 1 && jb.n === 1, 'both consumers must read the SAME response body');

    // 2) a third consumer after the originals were read still works (clone-per-consumer)
    const c = await patched('/api/video/issues/counts');
    assert(rawCalls.length === 1, 'third consumer within TTL must not refetch');
    assert((await c.json()).n === 1, 'late consumer clone must still be readable');

    // 3) different query string = different request
    await patched('/api/video/issues/counts?x=1');
    assert(rawCalls.length === 2, 'query strings must not collide');

    // 4) POST bypasses entirely
    rawCalls = [];
    await patched('/api/video/issues/counts', { method: 'POST' });
    await patched('/api/video/issues/counts', { method: 'POST' });
    assert(rawCalls.length === 2, 'POSTs must never dedupe');

    // 5) streams and socket.io bypass
    rawCalls = [];
    await patched('/api/artist/similar/311/stream');
    await patched('/api/artist/similar/311/stream');
    assert(rawCalls.length === 2, '/stream paths must never dedupe');
    assert(hook.dedupeKey('/socket.io/?EIO=4') === null, 'socket.io must be excluded');

    // 6) abortable requests bypass (a shared abort would kill every consumer)
    assert(hook.dedupeKey('/api/x', { signal: {} }) === null, 'signal requests must bypass');

    // 7) non-api and cross-origin bypass
    assert(hook.dedupeKey('/static/style.css') === null, 'non-api must bypass');
    assert(hook.dedupeKey('https://evil.example/api/x') === null, 'cross-origin must bypass');
    assert(hook.dedupeKey('/status') === '/status', '/status is deduped');

    // 8) failures are not cached — the next caller retries for real
    rawCalls = [];
    failNext = true;
    const f = await patched('/api/failing/counts');
    assert(f.status === 500, 'failure passes through');
    await patched('/api/failing/counts');
    assert(rawCalls.length === 2, 'a failed response must not be served from cache');

    // 9) TTL expiry refetches
    rawCalls = [];
    await patched('/api/ttl/check');
    hook.entries.get('/api/ttl/check').t -= (hook.ttl + 1);
    await patched('/api/ttl/check');
    assert(rawCalls.length === 2, 'expired entries must refetch');

    console.log('fetch-dedupe harness: all assertions passed');
};

t().catch((e) => { console.error('FAIL: ' + (e && e.stack || e)); process.exit(1); });
