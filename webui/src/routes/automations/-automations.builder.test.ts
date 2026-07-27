import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ROUTER_ROOT_ID } from '@/platform/shell/route-controllers';

import { useVanillaBuilder } from './-automations.builder';

/**
 * The hybrid handoff.
 *
 * The builder markup lives inside #automations-page, and `.page` is
 * display:none unless it carries .active — which the shell strips while React
 * owns the route. So the page has to be handed back for the edit and reclaimed
 * on close, and the reclaim has to survive every exit path.
 */

const legacy = () => document.getElementById('automations-page')!;
const reactRoot = () => document.getElementById(ROUTER_ROOT_ID)!;

let originalHide: () => void;

beforeEach(() => {
  document.body.innerHTML = `
    <div class="page" id="automations-page"></div>
    <div id="${ROUTER_ROOT_ID}" class="active"></div>
  `;
  originalHide = vi.fn<() => void>();
  window.showAutomationBuilder = vi.fn<(id?: number) => void>();
  window.hideAutomationBuilder = originalHide;
});

afterEach(() => {
  delete window.showAutomationBuilder;
  delete window.hideAutomationBuilder;
  document.body.innerHTML = '';
});

describe('useVanillaBuilder', () => {
  it('reveals the legacy page before opening, so the builder is not measured hidden', () => {
    const { result } = renderHook(() => useVanillaBuilder(vi.fn()));
    act(() => result.current(42));

    expect(legacy().classList.contains('active')).toBe(true);
    expect(reactRoot().classList.contains('active')).toBe(false);
    expect(window.showAutomationBuilder).toHaveBeenCalledWith(42);
  });

  it('opens with no id for a new automation', () => {
    const { result } = renderHook(() => useVanillaBuilder(vi.fn()));
    act(() => result.current());
    expect(window.showAutomationBuilder).toHaveBeenCalledWith(undefined);
  });

  it('reclaims the shell when the builder closes, and still runs the original', () => {
    const onClosed = vi.fn();
    const { result } = renderHook(() => useVanillaBuilder(onClosed));
    act(() => result.current(1));

    act(() => window.hideAutomationBuilder!());

    expect(originalHide).toHaveBeenCalled(); // the real close still happened
    expect(reactRoot().classList.contains('active')).toBe(true);
    expect(legacy().classList.contains('active')).toBe(false);
    expect(onClosed).toHaveBeenCalledTimes(1);
  });

  it('ignores a close it did not cause', () => {
    // The VIDEO page shares hideAutomationBuilder. If its builder closes while
    // this page happens to be mounted, we must not yank the shell around.
    const onClosed = vi.fn();
    renderHook(() => useVanillaBuilder(onClosed));

    act(() => window.hideAutomationBuilder!());

    expect(originalHide).toHaveBeenCalled();
    expect(onClosed).not.toHaveBeenCalled();
    expect(legacy().classList.contains('active')).toBe(false);
  });

  it('only reclaims once per open', () => {
    const onClosed = vi.fn();
    const { result } = renderHook(() => useVanillaBuilder(onClosed));
    act(() => result.current(1));
    act(() => window.hideAutomationBuilder!());
    act(() => window.hideAutomationBuilder!());
    expect(onClosed).toHaveBeenCalledTimes(1);
  });

  it('restores the original function on unmount', () => {
    const { unmount } = renderHook(() => useVanillaBuilder(vi.fn()));
    expect(window.hideAutomationBuilder).not.toBe(originalHide);
    unmount();
    expect(window.hideAutomationBuilder).toBe(originalHide);
  });

  it('restores the shell if the page unmounts mid-edit', () => {
    // Navigating away with the builder open would otherwise leave the legacy
    // container visible and the React host hidden.
    const { result, unmount } = renderHook(() => useVanillaBuilder(vi.fn()));
    act(() => result.current(1));
    expect(legacy().classList.contains('active')).toBe(true);

    unmount();

    expect(reactRoot().classList.contains('active')).toBe(true);
    expect(legacy().classList.contains('active')).toBe(false);
  });

  it('does nothing when the vanilla builder is unavailable', () => {
    delete window.showAutomationBuilder;
    const { result } = renderHook(() => useVanillaBuilder(vi.fn()));
    act(() => result.current(1));
    // No half-handover: the shell must not be left showing an empty legacy page.
    expect(legacy().classList.contains('active')).toBe(false);
    expect(reactRoot().classList.contains('active')).toBe(true);
  });
});
