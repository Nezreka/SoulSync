import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ActionMenu } from './action-menu';

afterEach(() => cleanup());

function renderMenu(extra: Partial<React.ComponentProps<typeof ActionMenu>> = {}) {
  const onSelect = vi.fn();
  const onDanger = vi.fn();
  const onOuter = vi.fn();
  render(
    <div onClick={onOuter}>
      <ActionMenu
        heading="Heading"
        items={[
          { key: 'a', label: 'First', onSelect },
          { key: 'b', label: 'Second', onSelect: vi.fn(), disabled: true },
          { key: 'c', label: 'Delete', danger: true, onSelect: onDanger },
        ]}
        trigger={(t) => (
          <button type="button" className="trigger" {...t}>
            More
          </button>
        )}
        {...extra}
      />
    </div>,
  );
  return { onSelect, onDanger, onOuter };
}

describe('ActionMenu', () => {
  it('renders nothing until the trigger is clicked, then a body-level menu', () => {
    renderMenu();
    expect(document.querySelector('[role="menu"]')).toBeNull();
    expect(screen.getByText('More').getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(screen.getByText('More'));
    const menu = document.querySelector('[role="menu"]') as HTMLElement;
    expect(menu.parentElement).toBe(document.body);
    expect(screen.getByText('More').getAttribute('aria-expanded')).toBe('true');
    expect(menu.querySelector('.lib-menu-heading')?.textContent).toBe('Heading');
  });

  it('runs the item, closes, and never lets the click reach the page', () => {
    const { onSelect, onOuter } = renderMenu();
    fireEvent.click(screen.getByText('More'));
    fireEvent.click(screen.getByText('First'));
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(document.querySelector('[role="menu"]')).toBeNull();
    // the trigger click and the item click both stop: the album row under
    // them is a toggle
    expect(onOuter).not.toHaveBeenCalled();
  });

  it('separates the danger item and disables what it is told to', () => {
    renderMenu();
    fireEvent.click(screen.getByText('More'));
    expect(document.querySelector('.lib-menu-sep')).not.toBeNull();
    expect(screen.getByText('Delete').closest('button')?.className).toContain('danger');
    expect((screen.getByText('Second').closest('button') as HTMLButtonElement).disabled).toBe(true);
  });

  it('closes on Escape and on a click outside', () => {
    renderMenu();
    fireEvent.click(screen.getByText('More'));
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(document.querySelector('[role="menu"]')).toBeNull();

    fireEvent.click(screen.getByText('More'));
    fireEvent.mouseDown(document.body);
    expect(document.querySelector('[role="menu"]')).toBeNull();
  });

  it('carries the hook class and data attributes onto the item', () => {
    render(
      <ActionMenu
        items={[
          {
            key: 'r',
            label: 'Reorganize',
            className: 'enhanced-reorganize-album-btn',
            data: { 'album-id': '7' },
            onSelect: vi.fn(),
          },
        ]}
        trigger={(t) => (
          <button type="button" {...t}>
            Open
          </button>
        )}
      />,
    );
    fireEvent.click(screen.getByText('Open'));
    const item = document.querySelector('.enhanced-reorganize-album-btn') as HTMLElement;
    expect(item.dataset.albumId).toBe('7');
    expect(item.getAttribute('role')).toBe('menuitem');
  });
});
