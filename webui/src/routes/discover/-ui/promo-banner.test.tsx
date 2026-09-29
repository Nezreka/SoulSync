import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { PromoBanner } from './promo-banner';

function props(over: Partial<Parameters<typeof PromoBanner>[0]> = {}) {
  return {
    kind: 'artist',
    eyebrow: 'An artist you should know',
    title: 'Kavinsky',
    subtitle: 'Because you have Daft Punk',
    art: '/k.jpg',
    actions: <button type="button">View artist</button>,
    videoId: null,
    playing: false,
    ...over,
  };
}

describe('PromoBanner', () => {
  it('says what it advertises, with its cover and its action', () => {
    const { container } = render(<PromoBanner {...props({ round: true })} />);
    expect(
      screen.getByRole('region', { name: 'An artist you should know: Kavinsky' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('heading').textContent).toBe('Kavinsky');
    expect(screen.getByText('Because you have Daft Punk')).toBeInTheDocument();
    expect(screen.getByText('View artist')).toBeInTheDocument();
    expect(container.querySelector('.dsc-promo-cover')).toHaveClass('round');
    expect(container.querySelector('.dsc-promo')).toHaveClass('dsc-promo--artist');
  });

  it('is always moving: the artwork drifts even with no video', () => {
    const { container } = render(<PromoBanner {...props()} />);
    const drift = container.querySelector('.dsc-promo-drift') as HTMLElement;
    expect(drift.style.backgroundImage).toContain('/k.jpg');
    expect(container.querySelector('.dsc-backdrop-video')).toBeNull();
  });

  it('glows the colour of its artwork', () => {
    const { container } = render(<PromoBanner {...props({ glowRgb: '10, 20, 30' })} />);
    expect(
      (container.querySelector('.dsc-promo') as HTMLElement).style.getPropertyValue('--promo-rgb'),
    ).toBe('10, 20, 30');
  });

  it('switches video backgrounds and says which way', () => {
    const onToggle = vi.fn();
    render(<PromoBanner {...props({ videoToggle: { on: true, onToggle } })} />);
    fireEvent.click(screen.getByLabelText('Turn video backgrounds off'));
    expect(onToggle).toHaveBeenCalledTimes(1);
  });
});
