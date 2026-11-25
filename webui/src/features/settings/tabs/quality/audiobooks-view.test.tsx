/**
 * Quality > Audiobooks (settings-bic phase 3; it was Audiobook Quality on
 * Library > Organization > Audiobooks): the page read back, every field with
 * its default, and each change saved under audiobooks.quality.* as before.
 */

import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import {
  createSettingsStore,
  type SettingsPayload,
  type SettingsStore,
} from '../../settings-store';
import { AudiobookQualityView } from './audiobooks-view';

let store: SettingsStore;
let posted: SettingsPayload[];

beforeEach(() => {
  const sink: SettingsPayload[] = [];
  posted = sink;
  store = createSettingsStore({
    post: (body) => {
      sink.push(structuredClone(body));
      return Promise.resolve({ success: true });
    },
    delay: 0,
  });
});
afterEach(async () => {
  await act(() => store.flush());
});

const input = (id: string) => document.getElementById(id) as HTMLInputElement;
const format = (v: string) =>
  document.querySelector<HTMLInputElement>(`#audiobook-allowed-formats input[value="${v}"]`)!;
const layout = (v: string) =>
  document.querySelector<HTMLInputElement>(`#audiobook-file-layout input[value="${v}"]`)!;
function saved(): SettingsPayload {
  const merged: SettingsPayload = {};
  for (const body of posted)
    for (const [k, v] of Object.entries(body)) merged[k] = { ...merged[k], ...v };
  return merged;
}

describe('Quality > Audiobooks', () => {
  it('every field at its default, named without a colon', () => {
    act(() => store.hydrate({ audiobooks: {} }));
    const { container } = render(<AudiobookQualityView store={store} />);
    expect(input('audiobook-format-first').value).toBe('m4b');
    expect(
      [...document.querySelectorAll<HTMLInputElement>('#audiobook-allowed-formats input')].every(
        (b) => b.checked,
      ),
    ).toBe(true);
    expect(layout('single').checked && layout('multiple').checked).toBe(true);
    expect(input('audiobook-min-bitrate').value).toBe('0');
    expect(input('audiobook-max-bitrate').value).toBe('0');
    expect(input('audiobook-allow-dramatized').checked).toBe(true);
    for (const l of container.querySelectorAll('label')) expect(l.textContent).not.toMatch(/:\s*$/);
    // the units are in the fields
    expect(input('audiobook-min-bitrate').closest('span')!.textContent).toContain('kbps');
  });

  it('each change saves its key as the page save collected it', async () => {
    act(() =>
      store.hydrate({
        audiobooks: { quality: { format_order: ['m4b', 'mp3'], min_bitrate_kbps: 32 } },
      }),
    );
    render(<AudiobookQualityView store={store} />);
    fireEvent.change(input('audiobook-format-first'), { target: { value: 'mp3' } });
    fireEvent.click(format('flac'));
    fireEvent.click(format('ogg'));
    fireEvent.click(layout('multiple'));
    fireEvent.change(input('audiobook-max-bitrate'), { target: { value: '-5' } });
    fireEvent.click(input('audiobook-allow-dramatized'));
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved()).toEqual({
      audiobooks: {
        'quality.format_order': ['mp3', 'm4b', 'm4a', 'opus', 'ogg', 'flac'],
        'quality.allowed_formats': ['m4b', 'm4a', 'mp3', 'opus'],
        'quality.file_layout': 'single',
        'quality.max_bitrate_kbps': 0,
        'quality.allow_dramatized': false,
      },
    });
    // the rest of the quality dict is the server's still
    expect(store.getState().settings.audiobooks?.quality).toMatchObject({ min_bitrate_kbps: 32 });
  });

  it('every format switched off stays off on screen, and saves as all allowed', async () => {
    act(() => store.hydrate({ audiobooks: { quality: { allowed_formats: ['mp3'] } } }));
    render(<AudiobookQualityView store={store} />);
    fireEvent.click(format('mp3'));
    expect(
      [...document.querySelectorAll<HTMLInputElement>('#audiobook-allowed-formats input')].some(
        (b) => b.checked,
      ),
    ).toBe(false);
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved().audiobooks).toEqual({ 'quality.allowed_formats': [] });
  });
});
