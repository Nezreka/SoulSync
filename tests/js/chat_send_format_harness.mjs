// Behavioral harness for the room send format: envelope or plain.
// "All messages" mode sends plain text so vanilla Soulseek clients can read
// it, but a channel other than #general (and any thread) only exists inside
// the envelope. Sending plain from there stripped the tag and refiled the
// message into #general behind the sender's back. These pin that the channel
// you are standing in wins over the filter. Run under node; exits non-zero
// with a message on any failure.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, '..', '..', 'webui', 'static', 'chat.js'), 'utf8');

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
const { _plainOn, _tagRoomPayload, _testSetState } = globalThis.window.ChatPage;

let failures = 0;
function check(name, cond, got) {
    if (!cond) { failures++; console.error(`FAIL: ${name}\n  got: ${JSON.stringify(got)}`); }
}
function setup(patch) {
    _testSetState(Object.assign({
        view: 'room', room: 'SoulSync', homeRoom: 'SoulSync',
        channel: 'general', thread: null, ssOnly: false,
    }, patch));
}
function payload() { return _tagRoomPayload({ message: 'hi' }); }

// "SoulSync only" always envelopes, and the channel rides along
setup({ ssOnly: true, channel: 'bugs' });
check('ss-only: not plain', _plainOn() === false, _plainOn());
let p = payload();
check('ss-only: chan tagged', p.chan === 'bugs' && !p.plain, p);

// "All messages" in #general is the plain case, nothing to tag
setup({ ssOnly: false, channel: 'general' });
check('all-messages #general: plain', _plainOn() === true, _plainOn());
p = payload();
check('all-messages #general: plain payload, no chan', p.plain === true && !('chan' in p), p);

// THE BUG: "All messages" while standing in #bugs must still envelope,
// tagged with the channel the user can see they are in
setup({ ssOnly: false, channel: 'bugs' });
check('all-messages #bugs: not plain', _plainOn() === false, _plainOn());
p = payload();
check('all-messages #bugs: chan tagged', p.chan === 'bugs' && !p.plain, p);

// same for a thread — a thread id has nowhere to go in plain text
setup({ ssOnly: false, channel: 'general', thread: { id: 'bob|2026-09-17T00:00:00Z', name: 'a thread' } });
check('all-messages thread: not plain', _plainOn() === false, _plainOn());
p = payload();
check('all-messages thread: thread tagged', p.thread === 'bob|2026-09-17T00:00:00Z' && !p.plain, p);

// a non-home room has no channels, so the filter alone decides
setup({ ssOnly: false, room: 'Other', channel: 'bugs' });
check('other room all-messages: plain', _plainOn() === true, _plainOn());
p = payload();
check('other room: plain payload, no chan', p.plain === true && !('chan' in p), p);
setup({ ssOnly: true, room: 'Other' });
check('other room ss-only: envelope', _plainOn() === false, _plainOn());

// a PM is plaintext by nature, never "plain mode"
setup({ view: 'pm', ssOnly: false });
check('pm: never plain mode', _plainOn() === false, _plainOn());

if (failures) { console.error(`${failures} failure(s)`); process.exit(1); }
console.log('chat send format harness: all checks passed');
