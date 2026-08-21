/**
 * Closing a popover without fighting the button that opened it.
 *
 * The bug this exists for: clicking the `...` trigger of an OPEN menu fired
 * both the trigger's toggle and the document-level outside-click handler, so
 * the menu was torn down and rebuilt instead of closed — and the rebuild
 * replaced the quality-profile markup while its async fill was still in flight,
 * leaving a menu that looked open and had no options in it.
 */

import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { usePopoverDismiss } from './use-popover-dismiss';

let popover: HTMLElement;
let trigger: HTMLElement;
let outside: HTMLElement;

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  popover = document.createElement('div');
  popover.innerHTML = '<button id="inner">Rename</button>';
  trigger = document.createElement('button');
  trigger.innerHTML = '<span id="glyph">…</span>';
  outside = document.createElement('div');
  document.body.append(popover, trigger, outside);
});

afterEach(() => {
  vi.useRealTimers();
  document.body.innerHTML = '';
});

/** Past the one-tick defer, so the listener is live. */
function settle() {
  act(() => {
    vi.advanceTimersByTime(1);
  });
}

function mount(anchor?: HTMLElement | null) {
  const onClose = vi.fn();
  const hook = renderHook(() =>
    usePopoverDismiss({ ref: { current: popover }, anchor, onClose }),
  );
  settle();
  return { onClose, ...hook };
}

describe('what closes it', () => {
  it('a click outside', () => {
    const { onClose } = mount(trigger);
    outside.click();
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('Escape', () => {
    const { onClose } = mount(trigger);
    act(() => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe('what does NOT close it', () => {
  it('a click inside the popover', () => {
    const { onClose } = mount(trigger);
    (popover.querySelector('#inner') as HTMLElement).click();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('a click on the TRIGGER — that is the trigger’s own toggle to handle', () => {
    // Without this, both handlers fire on one click and the popover is rebuilt
    // rather than closed.
    const { onClose } = mount(trigger);
    trigger.click();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('a click on something INSIDE the trigger, like its glyph', () => {
    // The click target is the span, not the button, so a target === trigger
    // check would miss it and the race would come straight back.
    const { onClose } = mount(trigger);
    (trigger.querySelector('#glyph') as HTMLElement).click();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('the click that OPENED it, still bubbling when the effect ran', () => {
    const onClose = vi.fn();
    renderHook(() => usePopoverDismiss({ ref: { current: popover }, onClose }));
    // No settle(): the listener is attached on a timer for exactly this reason.
    outside.click();
    expect(onClose).not.toHaveBeenCalled();
  });
});

describe('without an anchor', () => {
  it('still closes on an outside click', () => {
    // The anchor is optional; a popover with no trigger element loses only the
    // trigger exemption.
    const { onClose } = mount(undefined);
    outside.click();
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe('teardown', () => {
  it('stops listening once unmounted', () => {
    const { onClose, unmount } = mount(trigger);
    unmount();
    outside.click();
    act(() => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    });
    expect(onClose).not.toHaveBeenCalled();
  });
});
