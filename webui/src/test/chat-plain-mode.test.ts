import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * one control, not two.
 *
 * every room message was enveloped, unconditionally, so a vanilla soulseek
 * client saw !SS1! and base64 and the person typing had no way to know. the
 * room LOOKED shared and was not.
 *
 * boulder's call on the shape: the room filter already says which world you
 * are in, so it picks the send format too. "soulsync only" means you are
 * talking to soulsync clients, so the envelope earns its keep. "all messages"
 * means you can SEE the vanilla users, and it would be a lie to look at them
 * while sending something they cannot read.
 */

const JS = readFileSync(resolve(process.cwd(), 'static/chat.js'), 'utf8');
const HTML = readFileSync(resolve(process.cwd(), '../webui/index.html'), 'utf8');

function fn(name: string, deps = '') {
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  return new Function(`${deps}${extractFunction(name, JS)}; return ${name};`)();
}

type St = { view: string; ssOnly: boolean };
const plainOn = fn('_plainOn', 'var state;\n') as unknown as () => boolean;

function withState(st: St): boolean {
  // _plainOn closes over `state`; rebuild it with the state we want
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  return new Function(
    'st',
    `var state = st; ${extractFunction('_plainOn', JS)}; return _plainOn();`,
  )(st);
}

describe('the filter decides the send format', () => {
  it('showing everything sends plain text everyone can read', () => {
    expect(withState({ view: 'room', ssOnly: false })).toBe(true);
  });

  it('filtering to SoulSync sends the envelope', () => {
    expect(withState({ view: 'room', ssOnly: true })).toBe(false);
  });

  it('a PM is never plain-moded, it is already plaintext', () => {
    // offering the choice in a PM would imply PMs are enveloped, which they
    // are not — they must stay readable, and the ProveIt bots need literal text
    expect(withState({ view: 'pm', ssOnly: false })).toBe(false);
    expect(withState({ view: 'pm', ssOnly: true })).toBe(false);
  });
});

describe('there is no second switch', () => {
  it.each(['plainSend', '_toggleSendMode', 'chat-mode-btn'])(
    'no leftover %s',
    (dead) => {
      expect(JS.includes(dead), `${dead} should be gone`).toBe(false);
    },
  );

  it('and no orphan button in the markup', () => {
    expect(HTML).not.toContain('data-chat-mode-btn');
  });
});

describe('what a plain send puts on the wire', () => {
  const TAG = JS.slice(JS.indexOf('function _tagRoomPayload'), JS.indexOf('function _tagRoomPayload') + 900);

  it('asks the server for plain and stops there', () => {
    // an avatar, a channel tag and a thread id all live INSIDE the envelope.
    // attaching them anyway would make the server refuse a message the user
    // had no way to know was tagged.
    expect(TAG).toContain('payload.plain = true');
    const plainBranch = TAG.slice(TAG.indexOf('_plainOn()'), TAG.indexOf('_myAvatar()'));
    expect(plainBranch).toContain('return payload');
  });
});

describe('flipping the filter cleans up after itself', () => {
  const HANDLER = JS.slice(
    JS.indexOf("t = e.target.closest('[data-chat-filter]')"),
    JS.indexOf("t = e.target.closest('[data-chat-browse-retry]')"),
  );

  it('drops a half-built rich message that cannot survive plain text', () => {
    expect(HANDLER).toContain('cancelReply()');
    expect(HANDLER).toContain('cancelEdit()');
  });

  it('redraws the composer, because the format just changed', () => {
    expect(HANDLER).toContain('renderComposer()');
  });
});

describe('the hint explains the exception', () => {
  it('only shows in plain mode', () => {
    const sync = extractFunction('_syncModeBtn', JS);
    expect(sync).toContain('hint.hidden = !on');
    expect(sync).toContain('_plainOn()');
  });

  it('hides the controls that cannot work without an envelope', () => {
    const sync = extractFunction('_syncModeBtn', JS);
    for (const sel of ['chat-gif-btn', 'chat-poll-btn', 'chat-attach-btn', 'chat-toolbar']) {
      expect(sync, `${sel} must be hidden in plain mode`).toContain(sel);
    }
  });

  it('is in the markup', () => {
    expect(HTML).toContain('data-chat-mode-hint');
  });
});
