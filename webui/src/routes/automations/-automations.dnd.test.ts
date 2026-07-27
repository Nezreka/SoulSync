import { act, renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { useAutomationDnd } from './-automations.dnd';

/** Minimal DragEvent stand-in — jsdom does not implement dataTransfer. */
function dragEvent() {
  return {
    preventDefault: vi.fn(),
    dataTransfer: { setData: vi.fn(), effectAllowed: '', dropEffect: '' },
  } as unknown as React.DragEvent;
}

type Props = Record<string, (e: React.DragEvent) => void>;

describe('useAutomationDnd', () => {
  it('makes user cards draggable and system cards not', () => {
    const { result } = renderHook(() => useAutomationDnd(vi.fn()));
    expect(result.current.cardProps(1, null, false)).toHaveProperty('draggable', true);
    // System automations live in the protected section and cannot be moved.
    expect(result.current.cardProps(2, null, true)).toEqual({});
  });

  it('moves a card to the dropped group', () => {
    const onDrop = vi.fn();
    const { result } = renderHook(() => useAutomationDnd(onDrop));

    act(() => (result.current.cardProps(7, null, false) as Props).onDragStart(dragEvent()));
    act(() => (result.current.zoneProps('group:Chores', 'Chores') as Props).onDrop(dragEvent()));

    expect(onDrop).toHaveBeenCalledWith({ id: 7, groupName: null }, 'Chores');
  });

  it('treats a drop back into the same group as a no-op', () => {
    const onDrop = vi.fn();
    const { result } = renderHook(() => useAutomationDnd(onDrop));

    act(() => (result.current.cardProps(7, 'Chores', false) as Props).onDragStart(dragEvent()));
    act(() => (result.current.zoneProps('group:Chores', 'Chores') as Props).onDrop(dragEvent()));

    // No PUT for a move that changes nothing.
    expect(onDrop).not.toHaveBeenCalled();
  });

  it('drops into My Automations as a null group', () => {
    const onDrop = vi.fn();
    const { result } = renderHook(() => useAutomationDnd(onDrop));

    act(() => (result.current.cardProps(7, 'Chores', false) as Props).onDragStart(dragEvent()));
    act(() => (result.current.zoneProps('ungrouped', null) as Props).onDrop(dragEvent()));

    expect(onDrop).toHaveBeenCalledWith({ id: 7, groupName: 'Chores' }, null);
  });

  it('refuses protected sections as drop targets', () => {
    const { result } = renderHook(() => useAutomationDnd(vi.fn()));
    expect(result.current.zoneProps('system', null, { isProtected: true })).toEqual({});
  });

  it('keeps the zone lit while the pointer crosses child cards', () => {
    // dragenter/dragleave fire for CHILDREN too, so a naive handler clears the
    // highlight the moment the pointer moves over a card inside the zone.
    // The counter is what keeps it lit until the pointer really leaves.
    const { result } = renderHook(() => useAutomationDnd(vi.fn()));
    act(() => (result.current.cardProps(1, null, false) as Props).onDragStart(dragEvent()));

    const zone = () => result.current.zoneProps('group:A', 'A') as Props;
    act(() => zone().onDragEnter(dragEvent())); // enter the body
    act(() => zone().onDragEnter(dragEvent())); // enter a card inside it
    expect(result.current.overKey).toBe('group:A');

    act(() => zone().onDragLeave(dragEvent())); // leave the card, still inside
    expect(result.current.overKey).toBe('group:A');

    act(() => zone().onDragLeave(dragEvent())); // now leave the body
    expect(result.current.overKey).toBeNull();
  });

  it('calls preventDefault on dragover, or the browser refuses the drop', () => {
    const { result } = renderHook(() => useAutomationDnd(vi.fn()));
    act(() => (result.current.cardProps(1, null, false) as Props).onDragStart(dragEvent()));

    const e = dragEvent();
    act(() => (result.current.zoneProps('group:A', 'A') as Props).onDragOver(e));
    expect(e.preventDefault).toHaveBeenCalled();
  });

  it('ignores drag events when nothing is being dragged', () => {
    const onDrop = vi.fn();
    const { result } = renderHook(() => useAutomationDnd(onDrop));
    const e = dragEvent();

    act(() => (result.current.zoneProps('group:A', 'A') as Props).onDragOver(e));
    act(() => (result.current.zoneProps('group:A', 'A') as Props).onDrop(dragEvent()));

    expect(e.preventDefault).not.toHaveBeenCalled();
    expect(onDrop).not.toHaveBeenCalled();
  });

  it('reports the dragging card and clears state on dragend', () => {
    const { result } = renderHook(() => useAutomationDnd(vi.fn()));
    act(() => (result.current.cardProps(9, null, false) as Props).onDragStart(dragEvent()));
    expect(result.current.dragging).toBe(true);
    expect(result.current.isDraggingCard(9)).toBe(true);

    act(() => (result.current.cardProps(9, null, false) as Props).onDragEnd(dragEvent()));
    expect(result.current.dragging).toBe(false);
    expect(result.current.isDraggingCard(9)).toBe(false);
    expect(result.current.overKey).toBeNull();
  });
});
