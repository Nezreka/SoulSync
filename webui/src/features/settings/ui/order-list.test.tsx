/**
 * The order list: every way of moving a row (pointer, touch, keyboard, the ⋯
 * menu, the pool) ends in one onChange, a move that changes nothing saves
 * nothing, what a screen reader hears, and that nothing dragged in from
 * elsewhere is taken for a row.
 */

import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { type OrderChange, OrderList, type OrderListProps } from './order-list';

const NAMES: Record<string, string> = {
  soulseek: 'Soulseek',
  youtube: 'YouTube',
  qobuz: 'Qobuz',
  tidal: 'Tidal',
  deezer: 'Deezer',
};
const ROW = 68;
const STEP = 76;
const POOL_TOP = 400;

let changes: Array<{ next: string[]; change: OrderChange }>;

function Harness({
  start = ['soulseek', 'youtube', 'qobuz'],
  pool = ['tidal', 'deezer'],
  ...rest
}: { start?: string[]; pool?: string[] | null } & Partial<Omit<OrderListProps, 'pool'>>) {
  const [order, setOrder] = useState(start);
  const unused = (pool ?? []).filter((id) => !order.includes(id));
  return (
    <OrderList
      label="Download order for music"
      items={order.map((id) => ({
        id,
        name: NAMES[id],
        mark: <i />,
        menu: [
          {
            key: 'settings',
            label: 'Settings',
            onSelect: () => changes.push({ next: ['settings', id], change: null as never }),
          },
        ],
      }))}
      role={(id, index, all) =>
        index === 0 ? 'Tried first' : `Tried when ${NAMES[all[index - 1]]} finds nothing`
      }
      dropText={(id, index) => `Drop here: ${NAMES[id]} is number ${index + 1}`}
      afterText={(all) => `Music tries ${NAMES[all[0]]} first.`}
      pool={
        pool === null
          ? undefined
          : unused.map((id) => ({ id, name: NAMES[id], mark: <i />, line: 'Not tested yet' }))
      }
      removeLabel="Stop using for music"
      keepText="Keep at least one source"
      onChange={(next, change) => {
        changes.push({ next, change });
        setOrder(next);
      }}
      {...rest}
    />
  );
}

/** jsdom lays nothing out: rows are 68 tall, 76 apart, in document order; the pool starts at 400 */
function layout() {
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(
    function (this: HTMLElement) {
      const box = (top: number, height: number) =>
        ({
          top,
          bottom: top + height,
          left: 0,
          right: 600,
          width: 600,
          height,
          x: 0,
          y: top,
        }) as DOMRect;
      if (this.matches('[data-pool]')) return box(POOL_TOP, 200);
      const list = this.closest('ul');
      if (this.matches('ul'))
        return box(0, this.querySelectorAll(':scope > li[data-id]').length * STEP - 8);
      if (this.matches('li[data-id]') && list) {
        const index = Array.from(list.querySelectorAll(':scope > li[data-id]')).indexOf(this);
        return box((list.matches('[data-pool]') ? POOL_TOP : 0) + index * STEP, ROW);
      }
      return box(0, 0);
    },
  );
}

function pointer(type: string, target: EventTarget, y: number, pointerType = 'mouse', x = 10) {
  const event = new MouseEvent(type, {
    bubbles: true,
    cancelable: true,
    clientX: x,
    clientY: y,
    button: 0,
  });
  Object.defineProperty(event, 'pointerType', { value: pointerType });
  act(() => {
    target.dispatchEvent(event);
  });
}

const grip = (name: string) =>
  screen.getByRole('button', { name: new RegExp(`^Reorder ${name},`) });
const order = () =>
  Array.from(document.querySelectorAll<HTMLElement>('ul[aria-label] > li[data-id]')).map(
    (li) => li.dataset.id,
  );
const live = () => document.querySelector('[data-live]')?.textContent ?? '';
const rowMiddle = (index: number) => index * STEP + ROW / 2;

/** press the handle of the row at `from`, carry its middle to `toY`, let go */
function drag(name: string, from: number, toY: number, pointerType = 'mouse') {
  pointer('pointerdown', grip(name), rowMiddle(from), pointerType);
  pointer('pointermove', window, rowMiddle(from) + (toY > rowMiddle(from) ? 6 : -6), pointerType);
  pointer('pointermove', window, toY, pointerType);
}

beforeEach(() => {
  changes = [];
  layout();
});
afterEach(() => vi.restoreAllMocks());

describe('dragging by the handle', () => {
  it('moves a row up with a mouse, and says what the drop will mean while it is held', () => {
    render(<Harness />);
    drag('YouTube', 1, rowMiddle(0) - 10);
    expect(document.querySelector('[data-slot]')?.textContent).toBe(
      'Drop here: YouTube is number 1',
    );
    // the ranks and roles already read as they would be
    expect(document.querySelector('li[data-id="soulseek"]')?.textContent).toContain(
      'Tried when YouTube finds nothing',
    );
    pointer('pointerup', window, rowMiddle(0) - 10);
    expect(changes).toHaveLength(1);
    expect(changes[0].next).toEqual(['youtube', 'soulseek', 'qobuz']);
    expect(changes[0].change).toEqual({ type: 'move', id: 'youtube', from: 1, to: 0, by: 'drag' });
    expect(order()).toEqual(['youtube', 'soulseek', 'qobuz']);
    expect(live()).toBe('Dropped. Music tries YouTube first.');
    expect(document.querySelector('[data-slot]')).toBeNull();
  });

  it('moves a row down one place (the old widget could not)', () => {
    render(<Harness />);
    drag('Soulseek', 0, rowMiddle(1) + 10);
    pointer('pointerup', window, rowMiddle(1) + 10);
    expect(changes.map((c) => c.next)).toEqual([['youtube', 'soulseek', 'qobuz']]);
  });

  it('works with a finger, at once, without the 4px a mouse needs', () => {
    render(<Harness />);
    pointer('pointerdown', grip('Qobuz'), rowMiddle(2), 'touch');
    // already in the hand
    expect(document.querySelector<HTMLElement>('li[data-id="qobuz"]')?.style.position).toBe(
      'fixed',
    );
    pointer('pointermove', window, rowMiddle(0) - 5, 'touch');
    pointer('pointerup', window, rowMiddle(0) - 5, 'touch');
    expect(changes.map((c) => c.next)).toEqual([['qobuz', 'soulseek', 'youtube']]);
  });

  it('a mouse press that does not move is not a drag', () => {
    render(<Harness />);
    pointer('pointerdown', grip('YouTube'), rowMiddle(1));
    pointer('pointermove', window, rowMiddle(1) + 2);
    expect(document.querySelector<HTMLElement>('li[data-id="youtube"]')?.style.position).not.toBe(
      'fixed',
    );
    pointer('pointerup', window, rowMiddle(1) + 2);
    expect(changes).toEqual([]);
  });

  it('put down where it was picked up changes nothing and saves nothing', () => {
    render(<Harness />);
    drag('YouTube', 1, rowMiddle(1) + 12);
    pointer('pointerup', window, rowMiddle(1) + 12);
    expect(changes).toEqual([]);
    expect(live()).toBe('Dropped. YouTube is back at position 2 of 3.');
  });

  it('Escape puts the row back', () => {
    render(<Harness />);
    drag('YouTube', 1, rowMiddle(0) - 10);
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(document.querySelector('[data-slot]')).toBeNull();
    pointer('pointerup', window, rowMiddle(0) - 10);
    expect(changes).toEqual([]);
    expect(live()).toBe('Cancelled. YouTube was not moved.');
  });

  it('a cancelled pointer (the browser took the touch) puts the row back', () => {
    render(<Harness />);
    drag('YouTube', 1, rowMiddle(0) - 10, 'touch');
    pointer('pointercancel', window, 0, 'touch');
    expect(changes).toEqual([]);
    expect(order()).toEqual(['soulseek', 'youtube', 'qobuz']);
  });

  it('only the handle starts a drag', () => {
    render(<Harness />);
    const row = document.querySelector('li[data-id="youtube"]') as HTMLElement;
    pointer('pointerdown', within(row).getByText('YouTube'), rowMiddle(1));
    pointer('pointermove', window, rowMiddle(0) - 10);
    pointer('pointerup', window, rowMiddle(0) - 10);
    expect(changes).toEqual([]);
  });
});

describe('the pool', () => {
  it('dragging a row out of the order stops using it', () => {
    render(<Harness />);
    drag('Qobuz', 2, POOL_TOP + 30);
    pointer('pointerup', window, POOL_TOP + 30);
    expect(changes[0].next).toEqual(['soulseek', 'youtube']);
    expect(changes[0].change).toMatchObject({ type: 'remove', id: 'qobuz', by: 'drag' });
  });

  it('the last row cannot be dragged out', () => {
    render(<Harness start={['soulseek']} />);
    drag('Soulseek', 0, POOL_TOP + 30);
    pointer('pointerup', window, POOL_TOP + 30);
    expect(changes).toEqual([]);
  });

  it('dragging a pool row in places it exactly', () => {
    render(<Harness />);
    const row = document.querySelector('li[data-id="tidal"]') as HTMLElement;
    pointer('pointerdown', within(row).getByText('Tidal'), POOL_TOP + ROW / 2);
    pointer('pointermove', window, POOL_TOP + ROW / 2 - 8);
    pointer('pointermove', window, rowMiddle(1) - 10);
    pointer('pointerup', window, rowMiddle(1) - 10);
    expect(changes[0].next).toEqual(['soulseek', 'tidal', 'youtube', 'qobuz']);
    expect(changes[0].change).toEqual({ type: 'add', id: 'tidal', from: -1, to: 1, by: 'drag' });
  });

  it('a pool row let go over the pool is not added', () => {
    render(<Harness />);
    const row = document.querySelector('li[data-id="tidal"]') as HTMLElement;
    pointer('pointerdown', within(row).getByText('Tidal'), POOL_TOP + ROW / 2);
    pointer('pointermove', window, POOL_TOP + ROW / 2 + 40);
    pointer('pointerup', window, POOL_TOP + ROW / 2 + 40);
    expect(changes).toEqual([]);
  });

  it('a finger on a pool row scrolls the page: Use is the way in', () => {
    render(<Harness />);
    const row = document.querySelector('li[data-id="tidal"]') as HTMLElement;
    pointer('pointerdown', within(row).getByText('Tidal'), POOL_TOP + ROW / 2, 'touch');
    pointer('pointermove', window, rowMiddle(0), 'touch');
    pointer('pointerup', window, rowMiddle(0), 'touch');
    expect(changes).toEqual([]);
    fireEvent.click(screen.getByRole('button', { name: 'Use Tidal' }));
    expect(changes[0].next).toEqual(['soulseek', 'youtube', 'qobuz', 'tidal']);
    expect(changes[0].change).toEqual({ type: 'add', id: 'tidal', from: -1, to: 3, by: 'button' });
  });
});

describe('the keyboard', () => {
  it('Space picks up, arrows move, Space drops: one change, on the drop', () => {
    render(<Harness />);
    const handle = grip('Qobuz');
    handle.focus();
    fireEvent.keyDown(handle, { key: ' ' });
    expect(handle.getAttribute('aria-pressed')).toBe('true');
    expect(live()).toContain('Picked up Qobuz, position 3 of 3');
    fireEvent.keyDown(handle, { key: 'ArrowUp' });
    expect(live()).toBe('Qobuz, moved to position 2 of 3');
    expect(document.querySelector('li[data-id="qobuz"]')?.textContent).toContain(
      'Moving. Now 2 of 3.',
    );
    fireEvent.keyDown(handle, { key: 'ArrowUp' });
    expect(changes).toEqual([]);
    fireEvent.keyDown(handle, { key: ' ' });
    expect(changes).toHaveLength(1);
    expect(changes[0].next).toEqual(['qobuz', 'soulseek', 'youtube']);
    expect(changes[0].change).toEqual({ type: 'move', id: 'qobuz', from: 2, to: 0, by: 'keys' });
    expect(live()).toBe('Dropped. Music tries Qobuz first.');
    // focus stays on the row that moved
    expect(document.activeElement).toBe(grip('Qobuz'));
  });

  it('Enter picks up and drops too, and an arrow past the end does nothing', () => {
    render(<Harness />);
    const handle = grip('Soulseek');
    handle.focus();
    fireEvent.keyDown(handle, { key: 'Enter' });
    fireEvent.keyDown(handle, { key: 'ArrowUp' });
    fireEvent.keyDown(handle, { key: 'ArrowDown' });
    fireEvent.keyDown(handle, { key: 'Enter' });
    expect(changes.map((c) => c.next)).toEqual([['youtube', 'soulseek', 'qobuz']]);
  });

  it('Escape puts a picked-up row back and saves nothing', () => {
    render(<Harness />);
    const handle = grip('Qobuz');
    handle.focus();
    fireEvent.keyDown(handle, { key: ' ' });
    fireEvent.keyDown(handle, { key: 'ArrowUp' });
    fireEvent.keyDown(handle, { key: 'Escape' });
    expect(changes).toEqual([]);
    expect(handle.getAttribute('aria-pressed')).toBe('false');
    expect(live()).toBe('Cancelled. Qobuz is back at position 3 of 3.');
  });

  it('picked up and dropped in the same place saves nothing', () => {
    render(<Harness />);
    const handle = grip('YouTube');
    handle.focus();
    fireEvent.keyDown(handle, { key: ' ' });
    fireEvent.keyDown(handle, { key: 'ArrowUp' });
    fireEvent.keyDown(handle, { key: 'ArrowDown' });
    fireEvent.keyDown(handle, { key: ' ' });
    expect(changes).toEqual([]);
  });

  it('Alt with an arrow moves one place without picking up, and keeps focus', () => {
    render(<Harness />);
    const handle = grip('Soulseek');
    handle.focus();
    fireEvent.keyDown(handle, { key: 'ArrowDown', altKey: true });
    expect(changes[0].next).toEqual(['youtube', 'soulseek', 'qobuz']);
    expect(live()).toBe('Soulseek, moved to position 2 of 3');
    expect(document.activeElement).toBe(grip('Soulseek'));
    // at the top already: nothing
    fireEvent.keyDown(grip('YouTube'), { key: 'ArrowUp', altKey: true });
    expect(changes).toHaveLength(1);
  });

  it('leaving the handle with a row picked up puts it back', async () => {
    vi.useFakeTimers();
    try {
      render(<Harness />);
      const handle = grip('Qobuz');
      handle.focus();
      fireEvent.keyDown(handle, { key: ' ' });
      fireEvent.keyDown(handle, { key: 'ArrowUp' });
      act(() => grip('Soulseek').focus());
      act(() => {
        vi.runAllTimers();
      });
      expect(changes).toEqual([]);
      expect(grip('Qobuz').getAttribute('aria-pressed')).toBe('false');
    } finally {
      vi.useRealTimers();
    }
  });

  it('names each handle with its place, for a screen reader', () => {
    render(<Harness />);
    expect(grip('YouTube').getAttribute('aria-label')).toBe('Reorder YouTube, 2 of 3');
    expect(screen.getByRole('list', { name: 'Download order for music' })).toBeTruthy();
    expect(document.querySelector('[data-live]')?.getAttribute('aria-live')).toBe('polite');
  });
});

describe('the ⋯ menu', () => {
  const open = (name: string) =>
    fireEvent.click(screen.getByRole('button', { name: `More for ${name}` }));

  it('moves up and down, and focus goes back to the row that moved', () => {
    render(<Harness />);
    open('YouTube');
    const menu = screen.getByRole('menu');
    // the first entry has focus
    expect(document.activeElement).toBe(within(menu).getByRole('menuitem', { name: /Move up/ }));
    fireEvent.click(within(menu).getByRole('menuitem', { name: /Move up/ }));
    expect(changes[0].next).toEqual(['youtube', 'soulseek', 'qobuz']);
    expect(changes[0].change).toEqual({ type: 'move', id: 'youtube', from: 1, to: 0, by: 'menu' });
    expect(screen.queryByRole('menu')).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'More for YouTube' }));
  });

  it('the first row cannot move up, the last cannot move down', () => {
    render(<Harness />);
    open('Soulseek');
    expect(screen.getByRole('menuitem', { name: /Move up/ })).toHaveProperty('disabled', true);
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'Escape' });
    open('Qobuz');
    expect(screen.getByRole('menuitem', { name: /Move down/ })).toHaveProperty('disabled', true);
  });

  it("runs the row's own entries", () => {
    render(<Harness />);
    open('YouTube');
    fireEvent.click(screen.getByRole('menuitem', { name: 'Settings' }));
    expect(changes).toEqual([{ next: ['settings', 'youtube'], change: null }]);
  });

  it('stops using a row, but never the last one, and says why', () => {
    render(<Harness start={['soulseek', 'youtube']} />);
    open('YouTube');
    fireEvent.click(screen.getByRole('menuitem', { name: 'Stop using for music' }));
    expect(changes[0].next).toEqual(['soulseek']);
    open('Soulseek');
    const last = screen.getByRole('menuitem', { name: /Stop using for music/ });
    expect(last).toHaveProperty('disabled', true);
    expect(last.textContent).toContain('Keep at least one source');
  });

  it('arrows walk the entries, Escape closes and gives focus back', () => {
    render(<Harness />);
    open('YouTube');
    const menu = screen.getByRole('menu');
    fireEvent.keyDown(menu, { key: 'ArrowDown' });
    expect(document.activeElement?.textContent).toContain('Move down');
    fireEvent.keyDown(menu, { key: 'End' });
    expect(document.activeElement?.textContent).toContain('Stop using for music');
    fireEvent.keyDown(menu, { key: 'Escape' });
    expect(screen.queryByRole('menu')).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'More for YouTube' }));
  });

  it('a press anywhere else closes it', () => {
    render(<Harness />);
    open('YouTube');
    pointer('pointerdown', document.body, 0);
    expect(screen.queryByRole('menu')).toBeNull();
  });
});

describe('what it never does', () => {
  it("ignores the browser's own drag and drop: dropped text is not a source", () => {
    render(<Harness />);
    const list = screen.getByRole('list', { name: 'Download order for music' });
    const data = { getData: () => 'amazon', types: ['text/plain'], dropEffect: 'move' };
    fireEvent.dragOver(list, { dataTransfer: data });
    fireEvent.drop(list, { dataTransfer: data });
    fireEvent.drop(document.querySelector('[data-pool]') as HTMLElement, { dataTransfer: data });
    expect(changes).toEqual([]);
    expect(order()).toEqual(['soulseek', 'youtube', 'qobuz']);
    expect(document.querySelector('[draggable="true"]')).toBeNull();
  });

  it('with one row and no pool the handle is off', () => {
    render(<Harness start={['soulseek']} pool={null} />);
    expect(grip('Soulseek')).toHaveProperty('disabled', true);
  });

  it('shows the pool empty in words', () => {
    render(
      <Harness start={['soulseek', 'tidal', 'deezer']} poolEmpty="Every source is in the order." />,
    );
    expect(screen.getByText('Every source is in the order.')).toBeTruthy();
  });
});
