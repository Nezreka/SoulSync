/**
 * The sync page's socket seam. Mirrors the dashboard's -dash.events tests: the
 * channel NAME is part of the contract with api-monitor.js, so it is pinned as
 * a literal rather than compared against itself.
 */

import { renderHook } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it, vi } from 'vitest';

import {
  DEEZER_PLAYLIST_PROGRESS_EVENT,
  SYNC_LOGS_EVENT,
  deezerProgressLabel,
  useSyncLogsEvent,
} from './-sync.events';

describe('the vanilla half of the seam', () => {
  /**
   * The dispatch lives in a browser script the port cannot import, so nothing
   * type-checks the pair. This is the SEAM direction vanilla-seams.test.ts does
   * not cover — that file guards React calling INTO vanilla; this guards
   * vanilla dispatching OUT to React. Both fail silently: rename either side
   * and the log feed just stops updating.
   */
  const API_MONITOR = readFileSync(resolve(__dirname, '../../../static/api-monitor.js'), 'utf8');

  it('dispatches the same channel React listens on', () => {
    expect(API_MONITOR).toContain("new CustomEvent('ss:sync-logs'");
  });

  it('dispatches from inside updateLogsFromData, not a socket binding', () => {
    // The rule the dashboard arc established: re-broadcast in the HANDLER, so
    // every transport reaching it reaches React — here both the `tool:logs`
    // push (core.js) and the /api/logs poll.
    const start = API_MONITOR.indexOf('function updateLogsFromData(data) {');
    expect(start, 'updateLogsFromData should exist').toBeGreaterThan(-1);
    const body = API_MONITOR.slice(start, API_MONITOR.indexOf('\n}', start));
    expect(body).toContain('ss:sync-logs');
  });

  it('dispatches BEFORE the shape guard, so React sees every frame', () => {
    const start = API_MONITOR.indexOf('function updateLogsFromData(data) {');
    const body = API_MONITOR.slice(start, API_MONITOR.indexOf('\n}', start));
    const dispatch = body.indexOf('ss:sync-logs');
    const guard = body.indexOf('if (!data.logs');
    expect(dispatch).toBeGreaterThan(-1);
    expect(guard, 'the vanilla shape guard should still be there').toBeGreaterThan(-1);
    expect(dispatch).toBeLessThan(guard);
  });
});

describe('the log channel', () => {
  it('is the name api-monitor.js dispatches', () => {
    // A literal on purpose. The other half of this contract lives in
    // updateLogsFromData, which the port cannot import — if either side is
    // renamed alone, the feed goes silent with nothing failing.
    expect(SYNC_LOGS_EVENT).toBe('ss:sync-logs');
  });

  it('delivers the frame to the handler', () => {
    const onFrame = vi.fn();
    renderHook(() => useSyncLogsEvent(onFrame));
    window.dispatchEvent(new CustomEvent(SYNC_LOGS_EVENT, { detail: { logs: ['a'] } }));
    expect(onFrame).toHaveBeenCalledWith({ logs: ['a'] });
  });

  it('ignores an event with no detail', () => {
    const onFrame = vi.fn();
    renderHook(() => useSyncLogsEvent(onFrame));
    window.dispatchEvent(new CustomEvent(SYNC_LOGS_EVENT));
    expect(onFrame).not.toHaveBeenCalled();
  });

  it('unsubscribes on unmount', () => {
    const onFrame = vi.fn();
    const { unmount } = renderHook(() => useSyncLogsEvent(onFrame));
    unmount();
    window.dispatchEvent(new CustomEvent(SYNC_LOGS_EVENT, { detail: { logs: ['a'] } }));
    expect(onFrame).not.toHaveBeenCalled();
  });

  it('does not re-subscribe on every render', () => {
    // The handler is in the effect's dependency list, so a caller passing an
    // inline arrow would rebind the listener each render. Stable callers must
    // bind exactly once.
    const add = vi.spyOn(window, 'addEventListener');
    const onFrame = vi.fn();
    const { rerender } = renderHook(() => useSyncLogsEvent(onFrame));
    const before = add.mock.calls.filter(([name]) => name === SYNC_LOGS_EVENT).length;
    rerender();
    rerender();
    const after = add.mock.calls.filter(([name]) => name === SYNC_LOGS_EVENT).length;
    expect(after).toBe(before);
    add.mockRestore();
  });
});

describe('deezerProgressLabel (#TheHomeGuy — "it just sits there saying Loading...")', () => {
  const frame = (over = {}) => ({
    playlist_id: '15653026843',
    done: 340,
    total: 877,
    phase: 'track numbers',
    ...over,
  });

  it('turns a frame into something a person can read', () => {
    expect(deezerProgressLabel(frame(), '15653026843')).toBe('track numbers 340/877 (39%)');
  });

  it('ignores frames for a different playlist', () => {
    expect(deezerProgressLabel(frame(), '999')).toBeNull();
  });

  it('matches the id loosely, since one side arrives as a number', () => {
    expect(
      deezerProgressLabel(frame({ playlist_id: 15653026843 as unknown as string }), '15653026843'),
    ).toBe('track numbers 340/877 (39%)');
  });

  it('says nothing when there is no total to divide by', () => {
    expect(deezerProgressLabel(frame({ total: 0 }), '15653026843')).toBeNull();
  });

  it('says nothing for a missing frame', () => {
    expect(deezerProgressLabel(null, '15653026843')).toBeNull();
    expect(deezerProgressLabel(undefined, '15653026843')).toBeNull();
  });

  it('never exceeds 100%', () => {
    expect(deezerProgressLabel(frame({ done: 900, total: 877 }), '15653026843')).toContain(
      '(100%)',
    );
  });
});
