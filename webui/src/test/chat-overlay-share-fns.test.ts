import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Every function the overlay-share feature calls has to actually exist.
 *
 * Three were invented before being checked in a single session —
 * window.openVideoDetail, _lastMessages, sendRoomMessage — and each produced a
 * control that silently did nothing when clicked. The bug is always the same
 * shape and always invisible to a syntax check, so this is the cheap test that
 * would have caught all three.
 */

const JS = readFileSync(resolve(process.cwd(), 'static/chat.js'), 'utf8');

const BLOCK = JS.slice(
  JS.indexOf('function _ovToast'),
  JS.indexOf("// ── shared file card (filepost.dev links dressed by envelope 'f') ────"),
);

describe('the helpers it leans on are defined in chat.js', () => {
  it.each(['postJSON', '_tagRoomPayload', 'toggleAttachPanel', '_ovToast'])(
    '%s',
    (name) => {
      expect(JS.includes(`function ${name}(`), `${name} is not defined in chat.js`).toBe(true);
    },
  );
});

/** The block with comment lines stripped. Several of the comments explain WHY a
 *  name was wrong, so they legitimately mention the very strings the checks
 *  below ban — a bare includes() would flag the explanation, not a call. */
const CODE = BLOCK.split('\n')
  .filter((l) => !l.trim().startsWith('//'))
  .join('\n');

describe('and it invents nothing', () => {
  it.each(['sendRoomMessage(', '_lastMessages', 'openVideoDetail(', 'loadRoom('])(
    'does not call %s',
    (invented) => {
      expect(CODE.includes(invented), `${invented} does not exist`).toBe(false);
    },
  );

  it('reaches the room endpoint the rest of the composer uses', () => {
    expect(BLOCK).toContain("postJSON('/api/chat/room/message'");
    expect(BLOCK).toContain('_tagRoomPayload(');
  });

  it('refreshes the way the composer does, not via a function that does not exist', () => {
    // loadRoom() was invented; only loadRooms() exists and it reloads the room
    // LIST. Clearing lastStamp is what actually forces the next poll to render.
    expect(BLOCK).toContain('state.lastStamp = null');
  });

  it('guards showToast the way the other call sites do', () => {
    // chat.js can load without downloads.js, which defines it — 45 places
    // check first. The local helper is the one place allowed to call directly.
    const direct = BLOCK.split('\n').filter(
      (l) => /[^_.]showToast\(/.test(l) && !l.includes('typeof showToast'),
    );
    expect(direct).toEqual([]);
    expect(JS).toContain("if (typeof showToast === 'function') showToast(msg, kind);");
  });
});
