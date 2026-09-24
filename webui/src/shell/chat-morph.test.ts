/**
 * the chat list is patched, not rebuilt. what matters is node IDENTITY: the
 * same iframe element surviving a render is the video still playing. a
 * rebuild (the old innerHTML) makes every one of these fail.
 */

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { patchChatMessages } from './chat-morph';

// the shape renderGroups emits
function line(text: string): string {
  return `<div class="chat-line">${text}</div>`;
}
function group(user: string, lines: string[]): string {
  return (
    `<div class="chat-group"><span class="chat-avatar">${user[0]}</span>` +
    `<div class="chat-group-body"><div class="chat-group-head">${user}</div>` +
    lines.map(line).join('') +
    '</div></div>'
  );
}

let host: HTMLElement;

beforeEach(() => {
  host = document.createElement('div');
  document.body.appendChild(host);
});

afterEach(() => {
  host.remove();
});

// what the click handler does to "▶ play": an iframe added to the live line
function openPlayer(lineText: string): HTMLIFrameElement {
  const target = Array.from(host.querySelectorAll('.chat-line')).find((l) =>
    l.textContent?.startsWith(lineText),
  );
  if (!target) throw new Error(`no line ${lineText}`);
  const frame = document.createElement('iframe');
  target.appendChild(frame);
  return frame;
}

describe('patchChatMessages', () => {
  it('renders from empty like innerHTML would', () => {
    const html = group('cuz', ['hi', 'bruh']) + '<div class="chat-day-sep">today</div>';
    patchChatMessages(host, html);
    expect(host.innerHTML).toBe(html);
  });

  it('keeps a playing video when a new message arrives in another group', () => {
    patchChatMessages(host, group('cuz', ['watch this']));
    const frame = openPlayer('watch this');
    patchChatMessages(host, group('cuz', ['watch this']) + group('jaj', ['nice']));
    expect(frame.isConnected).toBe(true);
    expect(host.querySelectorAll('.chat-group')).toHaveLength(2);
  });

  it('keeps it when the new message joins the same group', () => {
    patchChatMessages(host, group('cuz', ['watch this']));
    const frame = openPlayer('watch this');
    patchChatMessages(host, group('cuz', ['watch this', 'so good']));
    expect(frame.isConnected).toBe(true);
    expect(
      Array.from(host.querySelectorAll('.chat-line'), (l) => l.firstChild?.textContent),
    ).toEqual(['watch this', 'so good']);
  });

  it('keeps it when older history is loaded above', () => {
    patchChatMessages(host, group('cuz', ['watch this']));
    const frame = openPlayer('watch this');
    patchChatMessages(host, group('old', ['earlier']) + group('cuz', ['watch this']));
    expect(frame.isConnected).toBe(true);
    expect(host.firstElementChild?.textContent).toContain('earlier');
  });

  it('never moves a kept node, since moving an iframe reloads it', () => {
    patchChatMessages(host, group('a', ['one']) + group('b', ['two']));
    const kept = host.querySelectorAll('.chat-group')[1];
    const moves: Node[] = [];
    const obs = new MutationObserver((recs) =>
      recs.forEach((r) => r.removedNodes.forEach((n) => moves.push(n))),
    );
    obs.observe(host, { childList: true, subtree: true });
    patchChatMessages(host, group('new', ['x']) + group('b', ['two']) + group('c', ['three']));
    obs.takeRecords().forEach((r) => r.removedNodes.forEach((n) => moves.push(n)));
    obs.disconnect();
    expect(kept.isConnected).toBe(true);
    expect(moves).not.toContain(kept);
  });

  it('replaces a line whose content changed (an edit, a reaction)', () => {
    patchChatMessages(host, group('cuz', ['typo']));
    const before = host.querySelector('.chat-line');
    patchChatMessages(host, group('cuz', ['fixed']));
    const after = host.querySelector('.chat-line');
    expect(after).not.toBe(before);
    expect(after?.textContent).toBe('fixed');
  });

  it('drops messages that are no longer shown (filter, block)', () => {
    patchChatMessages(host, group('a', ['one']) + group('spam', ['buy']) + group('b', ['two']));
    patchChatMessages(host, group('a', ['one']) + group('b', ['two']));
    expect(host.textContent).not.toContain('buy');
    expect(host.querySelectorAll('.chat-group')).toHaveLength(2);
  });

  it('keeps a dismissed link card dismissed', () => {
    patchChatMessages(host, group('cuz', ['link']));
    const l = host.querySelector('.chat-line')!;
    const card = document.createElement('div');
    card.className = 'chat-unfurl-card';
    l.appendChild(card);
    card.remove(); // the reader closed it
    patchChatMessages(host, group('cuz', ['link']) + group('jaj', ['ok']));
    expect(host.querySelector('.chat-unfurl-card')).toBeNull();
  });

  it('swaps out a placeholder like "Loading…" that was set with innerHTML', () => {
    host.innerHTML = '<div class="chat-empty">Loading…</div>';
    patchChatMessages(host, group('cuz', ['hi']));
    expect(host.querySelector('.chat-empty')).toBeNull();
    expect(host.querySelector('.chat-line')?.textContent).toBe('hi');
  });
});
