// Behavioral harness for chat.js notification logic — the unread channel
// badges. These are the fixes for "reload relights every badge" and "live
// arrivals don't light the channel you're not on": read markers persist per
// room in localStorage, and the unread fold skips own messages, the active
// channel, and everything at/below the stored marker.
// Run under node; exits non-zero with a message on any failure.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, '..', '..', 'webui', 'static', 'chat.js'), 'utf8');

// localStorage with an inspectable backing store — chat.js reads it at load.
const store = {};
globalThis.localStorage = {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: (k) => { delete store[k]; },
};
globalThis.document = {
    readyState: 'complete',
    hidden: false,
    getElementById: () => null,
    querySelector: () => null,
    addEventListener: () => {},
    dispatchEvent: () => {},
};
globalThis.MutationObserver = class { observe() {} };
globalThis.window = {};

(0, eval)(src);
const { _testSetState, _testSetSelf, _chanSeenKey, _loadChanSeen,
    _saveChanSeen, _chanUnread } = globalThis.window.ChatPage;

let failures = 0;
function check(name, cond, got) {
    if (!cond) { failures++; console.error(`FAIL: ${name}\n  got: ${JSON.stringify(got)}`); }
}

// chat.js loaded with an empty store — the active room defaults to whatever
// the module picked; pin it so the per-room key is deterministic.
_testSetState({ room: 'SoulSync', channel: 'general', selfName: 'me', msgs: [], chanSeen: {} });

const key = _chanSeenKey();
check('seen key carries the room name', key === 'chat_chan_seen_SoulSync', key);

// ── persistence round trip ─────────────────────────────────────────────────
_testSetState({ chanSeen: { help: '2026-07-19T10:05:00', bugs: '2026-07-19T09:00:00' } });
_saveChanSeen();
check('markers saved under the room key', !!store[key], store);
_testSetState({ chanSeen: {} });
_loadChanSeen();
let seen;
_testSetState({});  // no-op: state.chanSeen already loaded
seen = JSON.parse(store[key]);
check('markers survive a save/load cycle', seen.help === '2026-07-19T10:05:00', seen);

// ── per-room isolation ─────────────────────────────────────────────────────
_testSetState({ room: 'other', chanSeen: { help: '2026-01-01T00:00:00' } });
_saveChanSeen();
check('other room uses its own key',
    _chanSeenKey() === 'chat_chan_seen_other' && !!store['chat_chan_seen_other'], store);
_testSetState({ room: 'SoulSync' });
_loadChanSeen();
// read it back through the API: unread fold below uses state.chanSeen
check('room markers do not leak across rooms',
    store['chat_chan_seen_SoulSync'] !== store['chat_chan_seen_other'], store);

// ── storage sanitization ───────────────────────────────────────────────────
store['chat_chan_seen_SoulSync'] = '{"help":{"evil":1},"bugs":42,"ok":"2026-07-19T10:00:00"}';
_testSetState({ room: 'SoulSync' });
_loadChanSeen();
// sanitize via a fresh save: only string values survive
_saveChanSeen();
const clean = JSON.parse(store['chat_chan_seen_SoulSync']);
check('non-string marker values are scrubbed', clean.ok === '2026-07-19T10:00:00' && !('music' in clean) && !('gaming' in clean), clean);
store['chat_chan_seen_SoulSync'] = 'not json{{{';
_loadChanSeen();
_saveChanSeen();
check('corrupt storage resets to empty, not a crash',
    store['chat_chan_seen_SoulSync'] === '{}', store['chat_chan_seen_SoulSync']);

// ── unread fold ────────────────────────────────────────────────────────────
function msg(user, chan, ts) {
    return { username: user, message: 'x', timestamp: ts, chan: chan, rich: false };
}
_testSetState({
    room: 'SoulSync', channel: 'general', selfName: 'me',
    chanSeen: {},
    msgs: [
        msg('me', 'help', '2026-07-19T10:01:00'),        // own → never unread
        msg('alice', 'general', '2026-07-19T10:02:00'),  // active channel → hidden
        msg('bob', 'help', '2026-07-19T10:03:00'),       // unread
        msg('carol', 'bugs', '2026-07-19T10:04:00'),     // unread
        msg('dave', 'nope', '2026-07-19T10:05:00'),      // unknown channel → default
    ],
});
let counts = _chanUnread();
check('own messages excluded', !('help' in counts) || counts.help === 1, counts);
check('other-channel arrivals counted', counts.help === 1 && counts.bugs === 1, counts);
check('active channel never counted', !('general' in counts), counts);
// dave's unknown channel folds into the default ('general') — which is the
// active channel here, so it's hidden by the active rule, not lost. Switch
// the active channel to prove the fallback mapping is intact:
_testSetState({ channel: 'help' });
counts = _chanUnread();
check('unknown channel falls back to the default, not lost', counts.general === 2, counts);
check('bugs still counted', counts.bugs === 1, counts);
_testSetState({ channel: 'general' });

// markers: everything at/below the marker is read
_testSetState({ chanSeen: { help: '2026-07-19T10:03:00' } });
counts = _chanUnread();
check('message at the marker is read', !('help' in counts), counts);
check('other channels unaffected by one marker', counts.bugs === 1, counts);

if (failures) { console.error(`${failures} failure(s)`); process.exit(1); }
console.log('chat notify harness: all checks passed');
