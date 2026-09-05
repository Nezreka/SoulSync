import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * The JS half of the protocol-validator parity check.
 *
 * ChatProtocol.parseProtocol (chat-protocol.js) and core/chat_codec.protocol_of
 * (python) gate the SAME message bus from opposite ends. Python decides what a
 * client may SEND and what it is handed on RECEIVE; this side decides what the
 * client acts on. The codec docstring already said they "must agree or clients
 * desync" — nothing checked, and they had drifted:
 *
 *     python:  abs(v) < 1e15
 *     js:      isFinite(v)
 *
 * So anything from 1e15 up was blessed by a client's own validator and refused
 * by the server: a payload you built came back a 400, and an inbound carrier
 * python dropped never reached the bus.
 *
 * This file and tests/test_chat_protocol_parity.py read the SAME corpus. A rule
 * added to one validator and not the other fails here or there.
 */

const CORPUS = JSON.parse(
  readFileSync(resolve(process.cwd(), '../tests/data/chat_protocol_corpus.json'), 'utf8'),
) as {
  accept: { why: string; p: unknown }[];
  reject: { why: string; p: unknown }[];
};

/** Load the real IIFE and take the exported namespace off a fake window. */
function loadProtocol() {
  const src = readFileSync(resolve(process.cwd(), 'static/chat-protocol.js'), 'utf8');
  const win: Record<string, unknown> = {};
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  new Function('window', `${src}\n;return window;`)(win);
  const api = win.ChatProtocol as { parseProtocol: (e: unknown) => unknown } | undefined;
  if (!api?.parseProtocol) throw new Error('ChatProtocol.parseProtocol was not exported');
  return api;
}

const ChatProtocol = loadProtocol();

describe('the js validator accepts everything python accepts', () => {
  it.each(CORPUS.accept.map((c) => [c.why, c.p] as const))('%s', (_why, p) => {
    expect(ChatProtocol.parseProtocol({ p })).not.toBeNull();
  });
});

describe('the js validator rejects everything python rejects', () => {
  it.each(CORPUS.reject.map((c) => [c.why, c.p] as const))('%s', (_why, p) => {
    expect(ChatProtocol.parseProtocol({ p })).toBeNull();
  });
});

describe('the corpus itself', () => {
  it('exercises both sides of every cap', () => {
    // A corpus of only-valid or only-invalid cases would pass while proving
    // nothing about where the line actually sits.
    expect(CORPUS.accept.length).toBeGreaterThanOrEqual(10);
    expect(CORPUS.reject.length).toBeGreaterThanOrEqual(15);
    const whys = [...CORPUS.accept, ...CORPUS.reject].map((c) => c.why).join(' ').toLowerCase();
    for (const cap of ['24 char', '512', 'sixteen', '32', 'nesting', 'magnitude']) {
      expect(whys, `the corpus never exercises the ${cap} cap`).toContain(cap);
    }
  });

  it('still contains the case that started this', () => {
    expect(CORPUS.reject.some((c) => c.why.includes('magnitude cap'))).toBe(true);
  });
});
