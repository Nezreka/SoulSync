import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Sharing an overlay template into a chat room, client side.
 *
 * Boulder's ask: not a file attachment but a native one — pick a template, it
 * rides the message, the reader clicks it into their own Overlay Studio. Plus
 * the constraint that shapes the card: "there are some pieces of the overlay
 * that the user must manually download to use."
 *
 * Those pieces are the images. An image layer points at asset://<sha1> on the
 * SENDER's install, so the card has to say what will be missing BEFORE the
 * click, not after.
 *
 * Three functions in this file were invented before they were checked —
 * window.openVideoDetail, _lastMessages, sendRoomMessage — and each would have
 * produced a control that silently does nothing. The last group of tests here
 * exists to make that class of mistake fail loudly instead.
 */

const JS = readFileSync(resolve(process.cwd(), 'static/chat.js'), 'utf8');
const CSS = readFileSync(resolve(process.cwd(), 'static/style.css'), 'utf8');
const HTML = readFileSync(resolve(process.cwd(), 'index.html'), 'utf8');

const BLOCK = JS.slice(
  JS.indexOf('function _ovToast'),
  JS.indexOf("// ── shared file card (filepost.dev links dressed by envelope 'f') ────"),
);

describe('the card', () => {
  it('says what the template IS before you commit to it', () => {
    expect(BLOCK).toContain('chat-overlay-name');
    expect(BLOCK).toContain("' layer' : ' layers'");
  });

  it('warns about images this install will not have, up front', () => {
    // Finding out after the click that half the layers paint nothing feels
    // like a broken import.
    expect(BLOCK).toContain('chat-overlay-warn');
    expect(BLOCK).toContain("you may not have");
  });

  it('says nothing about images when the template needs none', () => {
    // The common case must not be dressed as a warning.
    expect(BLOCK).toMatch(/assets \?/);
  });

  it('colours the warning amber, not red', () => {
    // Missing art is a thing to know, not a failure: the template still
    // imports and still renders everything else.
    const rule = CSS.slice(CSS.indexOf('.chat-overlay-warn'), CSS.indexOf('.chat-overlay-warn') + 160);
    expect(rule).toContain('#f1c40f');
  });
});

describe('the definition has to survive from render to click', () => {
  it('keeps it in a registry rather than an attribute', () => {
    // A few KB of JSON cannot ride a data- attribute the way the file card's
    // url can.
    expect(BLOCK).toContain('_rememberOverlayShare');
    expect(BLOCK).toContain("data-chat-overlay-add=\"' + key + '\"");
  });

  it('bounds that registry', () => {
    // A busy room would otherwise hold every template ever scrolled past for
    // the life of the tab.
    expect(JS).toContain('OVERLAY_SHARE_KEEP');
    expect(JS).toContain('_overlayShares.order.shift()');
  });

  it('says so plainly when the key has aged out', () => {
    expect(BLOCK).toContain('That template is no longer on screen');
  });
});

describe('adopting one', () => {
  it('reports the missing images after the success, not instead of it', () => {
    // It really was added. It just has holes the sender has to fill.
    expect(BLOCK).toContain('missing_assets');
    expect(BLOCK).toContain('ask the sender for them');
    expect(BLOCK).toMatch(/Added "' \+ d\.name/);
  });

  it('restores the button when it fails, rather than leaving it dead', () => {
    expect(BLOCK).toContain('btn.disabled = false; btn.textContent = was;');
  });
});

describe('sending one', () => {
  it('is offered beside Upload, which is the same gesture', () => {
    expect(HTML).toContain('data-chat-attach-overlay');
  });

  it('refuses in a PM, where an envelope-only message would arrive as nothing', () => {
    // PMs are sent as plaintext by design.
    expect(BLOCK).toContain("state.view !== 'room'");
    expect(BLOCK).toContain('can only be shared in a room');
  });

  it('surfaces the server reason, which names the half that did not fit', () => {
    expect(BLOCK).toContain('res.body && res.body.error');
  });
});
