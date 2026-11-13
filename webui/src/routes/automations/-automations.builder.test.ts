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
let originalShow: (id?: number) => void;
let originalVideoShow: (id?: number) => void;

beforeEach(() => {
  document.body.innerHTML = `
    <div class="page" id="automations-page"></div>
    <div id="${ROUTER_ROOT_ID}" class="active"></div>
    <div class="page video-page" id="video-page-host">
      <section class="video-subpage" data-video-subpage="video-search"></section>
      <section class="video-subpage" data-video-subpage="video-automations" hidden></section>
      <div id="video-placeholder-slot"></div>
    </div>
  `;
  originalHide = vi.fn<() => void>();
  originalShow = vi.fn<(id?: number) => void>();
  originalVideoShow = vi.fn<(id?: number) => void>();
  window.showAutomationBuilder = originalShow;
  window.showVideoAutomationBuilder = originalVideoShow;
  window.hideAutomationBuilder = originalHide;
});

afterEach(() => {
  delete window.showAutomationBuilder;
  delete window.showVideoAutomationBuilder;
  delete window.hideAutomationBuilder;
  document.body.innerHTML = '';
  document.body.className = '';
});

describe('useVanillaBuilder', () => {
  it('reveals the legacy page before opening, so the builder is not measured hidden', () => {
    const { result } = renderHook(() => useVanillaBuilder(vi.fn()));
    act(() => result.current(42));

    expect(legacy().classList.contains('active')).toBe(true);
    expect(reactRoot().classList.contains('active')).toBe(false);
    expect(originalShow).toHaveBeenCalledWith(42);
  });

  it('opens with no id for a new automation', () => {
    const { result } = renderHook(() => useVanillaBuilder(vi.fn()));
    act(() => result.current());
    expect(originalShow).toHaveBeenCalledWith(undefined);
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

  it('restores the original functions on unmount', () => {
    const { unmount } = renderHook(() => useVanillaBuilder(vi.fn()));
    expect(window.hideAutomationBuilder).not.toBe(originalHide);
    expect(window.showAutomationBuilder).not.toBe(originalShow);
    unmount();
    expect(window.hideAutomationBuilder).toBe(originalHide);
    expect(window.showAutomationBuilder).toBe(originalShow);
  });

  it('reveals the legacy page for an open it did not start: a Guides recipe', () => {
    // the Guides tab is vanilla markup, and its "Use This" calls the global
    // directly. it used to open the builder inside the hidden legacy page.
    const onClosed = vi.fn();
    renderHook(() => useVanillaBuilder(onClosed));

    act(() => window.showAutomationBuilder!());

    expect(originalShow).toHaveBeenCalled();
    expect(legacy().classList.contains('active')).toBe(true);
    expect(reactRoot().classList.contains('active')).toBe(false);

    act(() => window.hideAutomationBuilder!());
    expect(reactRoot().classList.contains('active')).toBe(true);
    expect(onClosed).toHaveBeenCalledTimes(1);
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

/**
 * The video side: the same builder, in the video shell's own automations
 * subpage. video-side.css shows the React host for the video automations page
 * only while the body does not carry vauto-building.
 */
describe('useVanillaBuilder on the video side', () => {
  const subpage = (id: string) =>
    document.querySelector<HTMLElement>(`[data-video-subpage="${id}"]`)!;

  it('opens the video builder, on the video subpage alone', () => {
    const { result } = renderHook(() => useVanillaBuilder(vi.fn(), 'video'));
    act(() => result.current(7));

    expect(originalVideoShow).toHaveBeenCalledWith(7);
    expect(originalShow).not.toHaveBeenCalled();
    expect(document.body.classList.contains('vauto-building')).toBe(true);
    expect(subpage('video-automations').hidden).toBe(false);
    // the host still held the page that was open last
    expect(subpage('video-search').hidden).toBe(true);
    expect(document.getElementById('video-placeholder-slot')!.hidden).toBe(true);
    // the music containers are not touched
    expect(legacy().classList.contains('active')).toBe(false);
    expect(reactRoot().classList.contains('active')).toBe(true);
  });

  it('takes the screen back when the builder closes', () => {
    const onClosed = vi.fn();
    const { result } = renderHook(() => useVanillaBuilder(onClosed, 'video'));
    act(() => result.current());

    act(() => window.hideAutomationBuilder!());

    expect(originalHide).toHaveBeenCalled();
    expect(document.body.classList.contains('vauto-building')).toBe(false);
    expect(subpage('video-automations').hidden).toBe(true);
    expect(onClosed).toHaveBeenCalledTimes(1);
  });

  it('takes the screen back if the page unmounts mid-edit', () => {
    const { result, unmount } = renderHook(() => useVanillaBuilder(vi.fn(), 'video'));
    act(() => result.current(1));
    unmount();
    expect(document.body.classList.contains('vauto-building')).toBe(false);
    expect(window.showVideoAutomationBuilder).toBe(originalVideoShow);
  });

  it('leaves the music builder alone', () => {
    renderHook(() => useVanillaBuilder(vi.fn(), 'video'));
    expect(window.showAutomationBuilder).toBe(originalShow);
  });
});
