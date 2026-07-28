import { fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { EditableCell } from './editable-cell';

let requests: { url: string; body: unknown }[] = [];

function stubPut(result: unknown = { success: true }) {
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

function renderCell(props: Partial<React.ComponentProps<typeof EditableCell>> = {}) {
  const onSaved = vi.fn();
  const view = render(
    <table>
      <tbody>
        <tr>
          <EditableCell
            className="col-title editable"
            editable
            entityType="track"
            entityId={5}
            field="title"
            value="Xtal"
            onSaved={onSaved}
            {...props}
          >
            Xtal
          </EditableCell>
        </tr>
      </tbody>
    </table>,
  );
  return { onSaved, ...view };
}

const cell = () => document.querySelector('td') as HTMLElement;
const input = () => document.querySelector('.enhanced-inline-input') as HTMLInputElement;

beforeEach(() => {
  window.showToast = vi.fn();
  stubPut();
});

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = '';
});

describe('a non-editable cell', () => {
  it('never turns into an input', () => {
    renderCell({ editable: false });
    fireEvent.click(cell());
    expect(input()).toBeNull();
    expect(cell().textContent).toBe('Xtal');
  });
});

describe('entering the editor', () => {
  it('opens on click, seeded with the current value and focused', () => {
    renderCell();
    fireEvent.click(cell());
    expect(input().value).toBe('Xtal');
    expect(document.activeElement).toBe(input());
  });

  it('starts EMPTY for a null value rather than showing "null"', () => {
    // A TEXT field on purpose: a number input silently coerces an invalid
    // "null" to '', so it could not tell the two apart.
    renderCell({ field: 'title', value: null, children: '-' });
    fireEvent.click(cell());
    expect(input().value).toBe('');
  });

  it('does not let the click reach the album toggle', () => {
    const onOuter = vi.fn();
    render(
      <table onClick={onOuter}>
        <tbody>
          <tr>
            <EditableCell
              className="c"
              editable
              entityType="track"
              entityId={5}
              field="title"
              value="X"
              onSaved={vi.fn()}
            >
              X
            </EditableCell>
          </tr>
        </tbody>
      </table>,
    );
    fireEvent.click(document.querySelector('td') as HTMLElement);
    expect(onOuter).not.toHaveBeenCalled();
  });

  it('uses a number input with the right step and floor per field', () => {
    renderCell({ field: 'track_number', value: 3, children: '3' });
    fireEvent.click(cell());
    expect(input().type).toBe('number');
    expect(input().step).toBe('1');
    expect(input().min).toBe('1');
    expect(input().className).toContain('num');
  });

  it('lets BPM take fractions, with no floor', () => {
    renderCell({ field: 'bpm', value: 120, children: '120' });
    fireEvent.click(cell());
    expect(input().step).toBe('0.1');
    expect(input().min).toBe('');
  });
});

describe('saving', () => {
  it('PUTs the field to the TRACK endpoint on Enter', async () => {
    const { onSaved } = renderCell();
    fireEvent.click(cell());
    fireEvent.change(input(), { target: { value: 'Xtal (Remaster)' } });
    fireEvent.keyDown(input(), { key: 'Enter' });

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith('title', 'Xtal (Remaster)'));
    expect(requests[0].url).toBe('/api/library/track/5');
    expect(requests[0].body).toEqual({ title: 'Xtal (Remaster)' });
    expect(window.showToast).toHaveBeenCalledWith('Updated title', 'success');
  });

  it('PUTs to the ALBUM endpoint for an album field', async () => {
    const { onSaved } = renderCell({
      entityType: 'album',
      entityId: 9,
      field: 'label',
      value: 'x',
    });
    fireEvent.click(cell());
    fireEvent.change(input(), { target: { value: 'Warp' } });
    fireEvent.keyDown(input(), { key: 'Enter' });

    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(requests[0].url).toBe('/api/library/album/9');
  });

  it('saves on blur too', async () => {
    const { onSaved } = renderCell();
    fireEvent.click(cell());
    fireEvent.change(input(), { target: { value: 'Changed' } });
    fireEvent.blur(input());
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith('title', 'Changed'));
  });

  it('does NOT save twice when Enter is followed by the blur', async () => {
    const { onSaved } = renderCell();
    fireEvent.click(cell());
    const el = input();
    fireEvent.change(el, { target: { value: 'Once' } });
    fireEvent.keyDown(el, { key: 'Enter' });
    // Held from before the Enter: the input is unmounted by then, and the real
    // browser still fires its blur.
    fireEvent.blur(el);

    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(requests).toHaveLength(1);
  });

  it('sends null for an emptied number, so the field CLEARS', async () => {
    const { onSaved } = renderCell({ field: 'bpm', value: 120, children: '120' });
    fireEvent.click(cell());
    fireEvent.change(input(), { target: { value: '' } });
    fireEvent.keyDown(input(), { key: 'Enter' });

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith('bpm', null));
    expect(requests[0].body).toEqual({ bpm: null });
  });

  it('parses a fractional BPM but an INTEGER track number', async () => {
    const { onSaved } = renderCell({ field: 'bpm', value: 1, children: '1' });
    fireEvent.click(cell());
    fireEvent.change(input(), { target: { value: '128.5' } });
    fireEvent.keyDown(input(), { key: 'Enter' });
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith('bpm', 128.5));

    document.body.innerHTML = '';
    const second = renderCell({ field: 'track_number', value: 1, children: '1' });
    fireEvent.click(cell());
    fireEvent.change(input(), { target: { value: '7.9' } });
    fireEvent.keyDown(input(), { key: 'Enter' });
    await waitFor(() => expect(second.onSaved).toHaveBeenCalledWith('track_number', 7));
  });

  it('leaves the cell showing its old value when the save is REJECTED', async () => {
    stubPut({ success: false, error: 'locked' });
    const { onSaved } = renderCell();
    fireEvent.click(cell());
    fireEvent.change(input(), { target: { value: 'Nope' } });
    fireEvent.keyDown(input(), { key: 'Enter' });

    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith('Failed to update: locked', 'error'),
    );
    expect(onSaved).not.toHaveBeenCalled();
    expect(cell().textContent).toBe('Xtal');
  });
});

describe('escaping', () => {
  it('reverts without saving', async () => {
    const { onSaved } = renderCell();
    fireEvent.click(cell());
    fireEvent.change(input(), { target: { value: 'Discarded' } });
    fireEvent.keyDown(input(), { key: 'Escape' });

    expect(input()).toBeNull();
    expect(cell().textContent).toBe('Xtal');
    await new Promise((r) => setTimeout(r, 20));
    expect(onSaved).not.toHaveBeenCalled();
    expect(requests).toHaveLength(0);
  });

  it('does not then save on the blur that follows', async () => {
    renderCell();
    fireEvent.click(cell());
    const el = input();
    fireEvent.keyDown(el, { key: 'Escape' });
    fireEvent.blur(el);
    await new Promise((r) => setTimeout(r, 20));
    expect(requests).toHaveLength(0);
  });

  it('can be reopened after an escape', () => {
    renderCell();
    fireEvent.click(cell());
    fireEvent.keyDown(input(), { key: 'Escape' });
    fireEvent.click(cell());
    expect(input()).not.toBeNull();
  });
});
