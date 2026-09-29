import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { activeZone, DiscoverNav, type DiscoverNavItem } from './discover-nav';

const ITEMS: DiscoverNavItem[] = [
  { id: 'zone-a', label: 'For You' },
  { id: 'zone-b', label: 'New & Missing' },
  { id: 'zone-c', label: 'Explore & Build' },
];

describe('activeZone', () => {
  it('is the first zone before anything has scrolled past the read line', () => {
    expect(
      activeZone([
        { id: 'a', top: 400 },
        { id: 'b', top: 1400 },
      ]),
    ).toBe('a');
  });

  it('is the LAST zone whose top has passed the read line', () => {
    expect(
      activeZone([
        { id: 'a', top: -900 },
        { id: 'b', top: 120 },
        { id: 'c', top: 700 },
      ]),
    ).toBe('b');
  });

  it('uses 160px as the read line by default', () => {
    expect(
      activeZone([
        { id: 'a', top: 0 },
        { id: 'b', top: 160 },
      ]),
    ).toBe('b');
    expect(
      activeZone([
        { id: 'a', top: 0 },
        { id: 'b', top: 161 },
      ]),
    ).toBe('a');
  });

  it('is null with no zones', () => {
    expect(activeZone([])).toBeNull();
  });
});

describe('DiscoverNav', () => {
  it('renders one chip per zone it is given, and nothing invented', () => {
    render(<DiscoverNav items={ITEMS} onOpenLayout={() => {}} />);
    const chips = document.querySelectorAll('.dsc-nav-chip');
    expect([...chips].map((c) => c.textContent)).toEqual([
      'For You',
      'New & Missing',
      'Explore & Build',
    ]);
  });

  it('scrolls to the zone a chip names and lights that chip', () => {
    const target = document.createElement('section');
    target.id = 'zone-b';
    target.scrollIntoView = vi.fn();
    document.body.appendChild(target);
    try {
      render(<DiscoverNav items={ITEMS} onOpenLayout={() => {}} />);
      fireEvent.click(screen.getByText('New & Missing'));
      expect(target.scrollIntoView).toHaveBeenCalledWith({ behavior: 'smooth', block: 'start' });
      expect(screen.getByText('New & Missing').getAttribute('aria-current')).toBe('true');
      expect(screen.getByText('For You').getAttribute('aria-current')).toBeNull();
    } finally {
      target.remove();
    }
  });

  it('opens the layout customizer', () => {
    const onOpenLayout = vi.fn();
    render(<DiscoverNav items={ITEMS} onOpenLayout={onOpenLayout} />);
    fireEvent.click(screen.getByText('Customize'));
    expect(onOpenLayout).toHaveBeenCalledTimes(1);
  });

  it('renders nothing with no zones', () => {
    const { container } = render(<DiscoverNav items={[]} onOpenLayout={() => {}} />);
    expect(container.innerHTML).toBe('');
  });
});
