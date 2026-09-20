import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../../webui/static/chat.js', import.meta.url), 'utf8');
const protocol = readFileSync(new URL('../../webui/static/chat-protocol.js', import.meta.url), 'utf8');
const saved = new Map();
const storage = { getItem: k => saved.get(k) ?? null, setItem: (k, v) => saved.set(k, v) };
function load(localStorage = storage) {
    const host = { hidden: true, innerHTML: '' };
    const document = {
        readyState: 'complete', hidden: false, getElementById: () => null,
        querySelector: () => null, addEventListener() {}, dispatchEvent() {},
    };
    const context = vm.createContext({ window: {}, document, localStorage,
        MutationObserver: class { observe() {} }, console });
    vm.runInContext(protocol, context);
    // Expose internal actions only inside the test; execute the actual renderer.
    vm.runInContext(source.replace('window.ChatPage = {',
        'window.ChatPage = { renderPoll, _dismissPoll,'), context);
    document.getElementById = () => ({ querySelector: sel => sel === '[data-chat-poll]' ? host : null });
    const api = context.window.ChatPage;
    function show(events, room = 'SoulSync', selfName = 'Alice') {
        api._testSetState({ view: 'room', room, selfName, canSend: true, protocolLog: events });
        api.renderPoll();
        return host;
    }
    function dismiss(events) {
        api._dismissPoll(context.window.ChatProtocol.reducePoll(events));
        api.renderPoll();
    }
    return { show, dismiss };
}
const start = { room: 'SoulSync', timestamp: '2026-09-19T12:00:00Z', username: 'Alice',
    p: { k: 'poll.start', q: 'Next album?', o1: 'A', o2: 'B' } };
const vote = { room: 'SoulSync', timestamp: '2026-09-19T12:01:00Z', username: 'Bob',
    p: { k: 'poll.vote', o: '1' } };
const end = { room: 'SoulSync', timestamp: '2026-09-19T12:02:00Z', username: 'Alice', p: { k: 'poll.end' } };
const events = [start, vote, end];
let page = load();
assert.equal(page.show(events).hidden, false);
assert.match(page.show(events).innerHTML, /final/);
page.dismiss(events);
assert.equal(page.show(events).hidden, true);
// A fresh JS environment with only browser storage retained simulates refresh.
page = load();
assert.equal(page.show(events).hidden, true, 'dismissed poll returns after refresh');
assert.equal(page.show(events.map(e => ({ ...e, room: 'Other' })), 'Other').hidden, false);
assert.equal(page.show(events).hidden, true, 'room round trip forgets dismissal');
assert.equal(page.show(events, 'SoulSync', 'Bob').hidden, false, 'another viewer loses results');
assert.equal(page.show([start, vote]).hidden, false, 'active poll must remain visible');
const next = [...events, { ...start, timestamp: '2026-09-19T13:00:00Z' }];
assert.equal(page.show(next).hidden, false, 'next poll hidden');
assert.equal(page.show([...next, end]).hidden, false, 'next results hidden');
// Blocked storage still permits dismissal in this page and across room switches.
page = load({ getItem() { throw Error('blocked'); }, setItem() { throw Error('blocked'); } });
page.show(events);
page.dismiss(events);
assert.equal(page.show(events).hidden, true);
page.show([], 'Other');
assert.equal(page.show(events).hidden, true);
console.log('Poll dismissal refresh, room, viewer, new poll and blocked-storage checks passed');
