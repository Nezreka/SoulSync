import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, it, vi } from 'vitest';

import { extractFunction } from './vanilla-extract';
const source = readFileSync(resolve(process.cwd(), 'static/chat.js'), 'utf8');
it('turns a failed network request into a recoverable send result without retrying', async () => {
  const fetcher = vi.fn().mockRejectedValue(new Error('offline'));
  const post = new Function('fetch', `${extractFunction('postJSON', source)};return postJSON;`)(
    fetcher,
  );
  const result = await post('/api/chat/conversations/peer', { message: 'draft' });
  expect(result.ok).toBe(false);
  expect(result.body.error).toContain('not confirmed');
  expect(fetcher).toHaveBeenCalledOnce();
});
it('restores a failed draft without overwriting text typed while the request was pending', async () => {
  const input = document.createElement('textarea');
  input.value = 'original message';
  const state = { canSend: true, view: 'pm', pmUser: 'peer' };
  let finish!: (value: unknown) => void;
  const pending = new Promise((resolve) => {
    finish = resolve;
  });
  const post = vi.fn(() => pending);
  const send = new Function(
    'q',
    'state',
    'postJSON',
    '_arcOn',
    'showToast',
    'renderComposer',
    `${extractFunction('send', source)};return send;`,
  )(
    () => input,
    state,
    post,
    () => false,
    vi.fn(),
    vi.fn(),
  );
  send();
  expect(input.value).toBe('');
  input.value = 'new words';
  finish({ ok: false, body: { code: 'slskd_disconnected', error: 'Reconnect in slskd' } });
  await pending;
  await Promise.resolve();
  expect(input.value).toBe('original message\nnew words');
  expect(state.canSend).toBe(false);
  expect(post).toHaveBeenCalledOnce();
});
