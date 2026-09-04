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

/**
 * the picker is a modal, not a prompt().
 *
 * first cut asked you to type a number into window.prompt. you can't see what
 * you're picking that way, and on a page with a design system it just looks
 * broken. three things silently kill a data-attribute modal: markup that stops
 * matching the selectors, css nobody wrote, and cards that show names instead
 * of previews. one test each.
 */
const HTML = readFileSync(resolve(process.cwd(), '../webui/index.html'), 'utf8');
const CSS = readFileSync(resolve(process.cwd(), 'static/style.css'), 'utf8');

const PICKER = JS.slice(
  JS.indexOf('function _pickOverlayToShare'),
  JS.indexOf('function _shareOverlayById'),
);

describe('the overlay picker is a real modal', () => {
  it('does not ask through prompt() or alert()', () => {
    const code = PICKER.split('\n')
      .filter((l) => !l.trim().startsWith('//'))
      .join('\n');
    expect(code).not.toMatch(/\bprompt\(/);
    expect(code).not.toMatch(/\balert\(/);
  });

  it.each(['data-chat-ovl-modal', 'data-chat-ovl-grid', 'data-chat-ovl-close'])(
    'index.html carries %s for the JS to find',
    (hook) => {
      // the attribute has to end there. a bare includes() also matches
      // data-chat-ovl-gridXX, which querySelector would never find.
      const attrRe = new RegExp(`${hook}(?=[\\s=>])`);
      expect(attrRe.test(HTML), `${hook} is missing from index.html`).toBe(true);
    },
  );

  it('sits inside the same overlay/modal shell as the other chat dialogs', () => {
    const at = HTML.indexOf('data-chat-ovl-modal');
    const shell = HTML.slice(at - 200, at + 600);
    expect(shell).toContain('chat-settings-overlay');
    expect(shell).toContain('chat-settings-modal');
  });

  it.each([
    '.chat-ovl-grid',
    '.chat-ovl-card',
    '.chat-ovl-shot',
    '.chat-ovl-cardname',
    '.chat-ovl-empty',
  ])('%s is actually styled', (cls) => {
    expect(CSS.includes(`${cls} `) || CSS.includes(`${cls},`) || CSS.includes(`${cls}.`) ||
      CSS.includes(`${cls}:`) || CSS.includes(`${cls}\n`), `${cls} has no rule`).toBe(true);
  });

  it('shows a rendered example of each template, not just its name', () => {
    // the whole point of the modal
    expect(PICKER).toContain('/api/video/overlays/templates/');
    expect(PICKER).toContain('/thumb');
    expect(PICKER).toContain('<img');
  });

  it('keeps posters at 2:3 so the preview is not cropped', () => {
    const rule = CSS.slice(CSS.indexOf('.chat-ovl-shot {'), CSS.indexOf('.chat-ovl-shot img'));
    expect(rule).toContain('aspect-ratio: 2 / 3');
  });

  it('survives a thumb that fails to render', () => {
    // route 404s when pillow can't render. a broken image icon reads as a
    // broken template
    expect(PICKER).toContain('onerror=');
    expect(CSS).toContain('.chat-ovl-shot.is-noshot::after');
  });

  it('reads the fields the templates endpoint really returns', () => {
    // layer_count and kind come from list_overlay_templates. invent a field
    // here and every card says "0 layers" forever
    expect(PICKER).toContain('t.layer_count');
    expect(PICKER).toContain('d.templates');
  });

  it('says something useful when you have no templates yet', () => {
    expect(PICKER).toContain('chat-ovl-empty');
    expect(PICKER).toMatch(/no overlay templates yet/i);
  });

  it('escapes names into the grid', () => {
    // names are user text going into innerHTML
    expect(PICKER).toContain('esc(t.name');
    expect(PICKER).toContain('attr(t.id)');
  });

  it('closes on Escape and unbinds the listener again', () => {
    const closer = JS.slice(
      JS.indexOf('function _closeOverlayPicker'),
      JS.indexOf('function _shareOverlayById'),
    );
    expect(closer).toContain("removeEventListener('keydown'");
    expect(JS).toContain("ev.key === 'Escape'");
  });
});
