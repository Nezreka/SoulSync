import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SearchBar } from './search-bar';

/** A host that actually re-renders, so the controlled input behaves like the page. */
function Host({
  onQueryChange,
  onClear,
  onSubmit = () => {},
  picker,
  searching,
}: {
  onQueryChange?: (value: string) => void;
  onClear?: () => void;
  onSubmit?: () => void;
  picker?: React.ReactNode;
  searching?: boolean;
}) {
  const [query, setQuery] = useState('');
  return (
    <SearchBar
      query={query}
      onQueryChange={(value) => {
        setQuery(value);
        onQueryChange?.(value);
      }}
      onSubmit={onSubmit}
      onClear={() => {
        setQuery('');
        onClear?.();
      }}
      picker={picker}
      searching={searching}
    />
  );
}

const input = () => document.getElementById('enhanced-search-input') as HTMLInputElement;

/**
 * The prototype setter, which is how you simulate an actual keystroke.
 *
 * React patches the INSTANCE `value` descriptor to remember the last value it
 * knows about. Going through the prototype leaves that tracker stale, so React
 * treats the following event as a genuine edit — exactly like typing. Writing
 * `input.value = x` instead hits React's patched setter, updating the tracker,
 * and React then ignores the event: that is the downloads.js widget's path.
 */
const nativeValueSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!
  .set as (this: HTMLInputElement, value: string) => void;

function typeInto(element: HTMLInputElement, value: string) {
  act(() => {
    nativeValueSetter.call(element, value);
    element.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

afterEach(cleanup);

describe('SearchBar', () => {
  it('reports a keystroke EXACTLY once, and keeps the input in sync', () => {
    // Both listeners see the one `input` event a keystroke produces. Without the
    // claim, each edit is reported twice — which this test is the only thing
    // that catches, since fireEvent.change dispatches no `input` event at all
    // and so never reaches the native listener.
    const onQueryChange = vi.fn();
    render(<Host onQueryChange={onQueryChange} />);

    typeInto(input(), 'a');
    typeInto(input(), 'ap');

    expect(onQueryChange.mock.calls).toEqual([['a'], ['ap']]);
    expect(input().value).toBe('ap');
  });

  it('accepts a programmatic write from the global download widget', () => {
    // downloads.js:5753 sets `.value` and dispatches a bubbling native `input`
    // event to hand a query over. React does not fire onChange for it — its value
    // tracker was updated by the very assignment — so the native listener is the
    // ONLY thing making the handoff work.
    const onQueryChange = vi.fn();
    render(<Host onQueryChange={onQueryChange} />);

    act(() => {
      input().value = 'from the widget';
      input().dispatchEvent(new Event('input', { bubbles: true }));
    });

    expect(onQueryChange).toHaveBeenCalledExactlyOnceWith('from the widget');
    expect(input().value).toBe('from the widget');
  });

  it('ignores a programmatic write of the text already on screen', () => {
    const onQueryChange = vi.fn();
    render(<Host onQueryChange={onQueryChange} />);
    typeInto(input(), 'aphex');
    onQueryChange.mockClear();

    act(() => {
      input().value = 'aphex';
      input().dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(onQueryChange).not.toHaveBeenCalled();
  });

  it('still reports a change event that arrives on its own', () => {
    // Autofill and undo can produce one without a preceding `input`; that lands
    // on React's onChange and nowhere else, so the React path has to stay live.
    const onQueryChange = vi.fn();
    render(<Host onQueryChange={onQueryChange} />);
    fireEvent.change(input(), { target: { value: 'autofilled' } });
    expect(onQueryChange).toHaveBeenCalledExactlyOnceWith('autofilled');
  });

  it('submits on Enter, bypassing the debounce', () => {
    const onSubmit = vi.fn();
    render(<Host onSubmit={onSubmit} />);
    fireEvent.change(input(), { target: { value: 'aphex' } });
    fireEvent.keyDown(input(), { key: 'Enter' });
    expect(onSubmit).toHaveBeenCalledOnce();
  });

  it('ignores other keys', () => {
    const onSubmit = vi.fn();
    render(<Host onSubmit={onSubmit} />);
    fireEvent.keyDown(input(), { key: 'a' });
    fireEvent.keyDown(input(), { key: 'Escape' });
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('shows the ✕ whenever there is text, not while a search runs', () => {
    // search.js:241-243 toggles it on `query.length === 0` — nothing to do with
    // a request being in flight. Tying it to loading hides it while you type.
    const onClear = vi.fn();
    render(<Host onClear={onClear} />);
    expect(document.getElementById('enhanced-cancel-btn')).toBeNull();

    typeInto(input(), 'aphex');
    expect(document.getElementById('enhanced-cancel-btn')).not.toBeNull();
  });

  it('empties the box when the ✕ is clicked', () => {
    // It CLEARS; it does not cancel (search.js:278-283).
    const onClear = vi.fn();
    render(<Host onClear={onClear} />);
    typeInto(input(), 'aphex');

    fireEvent.click(document.getElementById('enhanced-cancel-btn') as HTMLElement);
    expect(onClear).toHaveBeenCalledOnce();
    expect(input().value).toBe('');
    // And the button goes away with the text.
    expect(document.getElementById('enhanced-cancel-btn')).toBeNull();
  });

  it('has no second lookup box: a pasted link goes in the one field', () => {
    // the page routes a link to the id resolver, so the field says it takes one
    render(<Host />);
    expect(document.getElementById('enh-id-input')).toBeNull();
    expect(input().placeholder).toBe('Artists, albums, tracks, or paste a link');
    expect(input().getAttribute('autocomplete')).toBe('off');
    expect(input().getAttribute('spellcheck')).toBe('false');
  });

  it('puts the source picker inside the field, before the input', () => {
    render(<Host picker={<button type="button">Spotify</button>} />);
    const box = document.getElementById('enhanced-search-bar') as HTMLElement;
    expect(box.firstElementChild?.textContent).toBe('Spotify');
    expect(box.contains(input())).toBe(true);
  });

  it('shows a spinner only while searching', () => {
    render(<Host />);
    expect(screen.queryByRole('status', { name: 'Searching' })).toBeNull();
    cleanup();
    render(<Host searching />);
    expect(screen.getByRole('status', { name: 'Searching' })).toBeInTheDocument();
  });

  it('keeps the ids the rest of the app reaches for', () => {
    // downloads.js targets #enhanced-search-input by id; renaming it silently
    // breaks the handoff.
    render(<Host />);
    expect(input()).not.toBeNull();
    expect(document.getElementById('enhanced-search-bar')).not.toBeNull();
  });
});
