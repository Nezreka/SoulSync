import { fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { EnhancedBulkBar } from './enhanced-bulk-bar';

let requests: { url: string; body: unknown }[] = [];

function stubApi(result: unknown = { success: true, updated_count: 2 }) {
  requests = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      requests.push({
        url: String(input),
        body: init?.body ? JSON.parse(String(init.body)) : null,
      });
      return new Response(JSON.stringify(result), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }),
  );
}

function renderBar(selected = new Set(['1', '2']), isAdmin = true) {
  const onClear = vi.fn();
  const onEdited = vi.fn();
  const view = render(
    <EnhancedBulkBar selected={selected} isAdmin={isAdmin} onClear={onClear} onEdited={onEdited} />,
  );
  return { onClear, onEdited, ...view };
}

const bar = () => document.getElementById('enhanced-bulk-bar') as HTMLElement;
const field = (id: string) => document.getElementById(id) as HTMLInputElement;

beforeEach(() => {
  window.showToast = vi.fn();
  window.showBatchTagPreview = vi.fn();
  window._pollBatchRgStatus = vi.fn();
  stubApi();
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete window.showBatchTagPreview;
  delete window._pollBatchRgStatus;
  document.body.innerHTML = '';
});

describe('visibility', () => {
  it('stays hidden with nothing selected', () => {
    renderBar(new Set());
    expect(bar().className).not.toContain('visible');
  });

  it('appears once something is ticked, with the count', () => {
    renderBar();
    expect(bar().className).toContain('visible');
    expect(document.getElementById('enhanced-bulk-count')?.textContent).toBe('2');
  });

  it('stays hidden for a NON-admin even with a selection', () => {
    // The vanilla hid it outright rather than showing disabled actions.
    renderBar(new Set(['1']), false);
    expect(bar().className).not.toContain('visible');
  });
});

describe('the simple actions', () => {
  it('hands the ids straight to the batch tag modal', () => {
    renderBar();
    fireEvent.click(document.querySelector('.tag-write') as HTMLElement);
    expect(window.showBatchTagPreview).toHaveBeenCalledWith(['1', '2'], null);
  });

  it('starts a batch ReplayGain job and polls it', async () => {
    renderBar();
    fireEvent.click(document.querySelector('.rg-analyze') as HTMLElement);

    await waitFor(() => expect(window._pollBatchRgStatus).toHaveBeenCalled());
    expect(requests[0].url).toBe('/api/library/tracks/analyze-replaygain-batch');
    expect(requests[0].body).toEqual({ track_ids: ['1', '2'] });
    expect(window.showToast).toHaveBeenCalledWith(
      'ReplayGain analysis started for 2 tracks…',
      'info',
    );
  });

  it('does NOT poll when the job was refused', async () => {
    stubApi({ success: false, error: 'busy' });
    renderBar();
    fireEvent.click(document.querySelector('.rg-analyze') as HTMLElement);

    await waitFor(() => expect(window.showToast).toHaveBeenCalledWith('ReplayGain: busy', 'error'));
    expect(window._pollBatchRgStatus).not.toHaveBeenCalled();
  });

  it('clears the selection', () => {
    const { onClear } = renderBar();
    fireEvent.click(document.querySelector('.btn--danger') as HTMLElement);
    expect(onClear).toHaveBeenCalled();
  });
});

describe('the batch edit modal', () => {
  const open = () => fireEvent.click(document.querySelectorAll('.enhanced-bulk-btn')[0]);

  it('titles itself with the selection count', () => {
    renderBar();
    open();
    expect(document.getElementById('enhanced-bulk-modal-title')?.textContent).toBe(
      'Batch Edit 2 Tracks',
    );
  });

  it('singularises a single track', () => {
    renderBar(new Set(['1']));
    open();
    expect(document.getElementById('enhanced-bulk-modal-title')?.textContent).toBe(
      'Batch Edit 1 Track',
    );
  });

  it('sends ONLY the fields that were filled in', async () => {
    // A blank box means "leave this column alone", never "clear it everywhere".
    const { onEdited, onClear } = renderBar();
    open();
    fireEvent.change(field('bulk-edit-style'), { target: { value: 'IDM' } });
    fireEvent.click(
      document.querySelector('.enhanced-bulk-modal-footer .btn--primary') as HTMLElement,
    );

    await waitFor(() => expect(onEdited).toHaveBeenCalledWith(['1', '2'], { style: 'IDM' }));
    expect(requests[0].url).toBe('/api/library/tracks/batch');
    expect(requests[0].body).toEqual({ track_ids: ['1', '2'], updates: { style: 'IDM' } });
    expect(window.showToast).toHaveBeenCalledWith('Updated 2 tracks', 'success');
    // The selection is dropped once applied.
    expect(onClear).toHaveBeenCalled();
  });

  it('parses the numeric fields, and explicit as a flag', async () => {
    const { onEdited } = renderBar();
    open();
    fireEvent.change(field('bulk-edit-track-number'), { target: { value: '3' } });
    fireEvent.change(field('bulk-edit-bpm'), { target: { value: '128.5' } });
    fireEvent.change(field('bulk-edit-explicit'), { target: { value: '1' } });
    fireEvent.click(
      document.querySelector('.enhanced-bulk-modal-footer .btn--primary') as HTMLElement,
    );

    await waitFor(() =>
      expect(onEdited).toHaveBeenCalledWith(['1', '2'], {
        track_number: 3,
        bpm: 128.5,
        explicit: 1,
      }),
    );
  });

  it('sends explicit:0 — "No" is a real choice, not a blank', async () => {
    const { onEdited } = renderBar();
    open();
    fireEvent.change(field('bulk-edit-explicit'), { target: { value: '0' } });
    fireEvent.click(
      document.querySelector('.enhanced-bulk-modal-footer .btn--primary') as HTMLElement,
    );
    await waitFor(() => expect(onEdited).toHaveBeenCalledWith(['1', '2'], { explicit: 0 }));
  });

  it('refuses an empty edit without calling the API', () => {
    renderBar();
    open();
    fireEvent.click(
      document.querySelector('.enhanced-bulk-modal-footer .btn--primary') as HTMLElement,
    );
    expect(requests).toHaveLength(0);
    expect(window.showToast).toHaveBeenCalledWith('No changes to apply', 'error');
  });

  it('keeps the modal open and the selection intact when the API rejects', async () => {
    stubApi({ success: false, error: 'locked' });
    const { onEdited, onClear } = renderBar();
    open();
    fireEvent.change(field('bulk-edit-style'), { target: { value: 'IDM' } });
    fireEvent.click(
      document.querySelector('.enhanced-bulk-modal-footer .btn--primary') as HTMLElement,
    );

    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith('Bulk edit failed: locked', 'error'),
    );
    expect(onEdited).not.toHaveBeenCalled();
    expect(onClear).not.toHaveBeenCalled();
    expect(document.getElementById('enhanced-bulk-edit-overlay')).not.toBeNull();
  });

  it('closes on Cancel without touching anything', () => {
    const { onEdited } = renderBar();
    open();
    // Scoped to the modal: the bar's own buttons are .btn--secondary too.
    fireEvent.click(
      document.querySelector('.enhanced-bulk-modal-footer .btn--secondary') as HTMLElement,
    );
    expect(document.getElementById('enhanced-bulk-edit-overlay')).toBeNull();
    expect(onEdited).not.toHaveBeenCalled();
    expect(requests).toHaveLength(0);
  });
});
