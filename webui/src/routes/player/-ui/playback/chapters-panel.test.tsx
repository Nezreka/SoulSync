/**
 * the video's chapter strip: every chapter's picture, name and time, the
 * playing one marked and focused when it opens, arrows and home/end moving
 * along it, enter playing one, escape closing it, and a card with no
 * picture (yet, or a broken one) showing the chapter's number instead.
 */

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { Chapter } from '../../-engine/types';

import { ChaptersPanel, span } from './chapters-panel';

const CHAPTERS: Chapter[] = [
  { start: 0, end: 95, title: 'Cold open', thumb: '/api/player/chapter-thumb/movie/7/0?v=1' },
  { start: 95, end: 600, title: 'The heist', thumb: '/api/player/chapter-thumb/movie/7/1?v=1' },
  { start: 600, end: 3700, title: 'Getaway', thumb: null },
  { start: 3700, title: 'Credits', thumb: '/api/player/chapter-thumb/movie/7/3?v=1' },
];

function mount(position = 300, over: Partial<Parameters<typeof ChaptersPanel>[0]> = {}) {
  const onPick = vi.fn();
  const onClose = vi.fn();
  const view = render(
    <ChaptersPanel
      chapters={CHAPTERS}
      position={position}
      duration={4000}
      onPick={onPick}
      onClose={onClose}
      {...over}
    />,
  );
  const cards = () => screen.getAllByRole('button').filter((b) => b.closest('li'));
  return { ...view, onPick, onClose, cards };
}

describe('chapters panel', () => {
  it('shows each chapter with its picture, name, start and length', () => {
    const { cards, container } = mount();
    expect(cards()).toHaveLength(4);
    expect(screen.getByRole('region', { name: 'Chapters' })).toBeTruthy();
    const imgs = container.querySelectorAll('img');
    expect(Array.from(imgs).map((i) => i.getAttribute('src'))).toEqual([
      '/api/player/chapter-thumb/movie/7/0?v=1',
      '/api/player/chapter-thumb/movie/7/1?v=1',
      '/api/player/chapter-thumb/movie/7/3?v=1',
    ]);
    expect(cards()[1].textContent).toContain('The heist');
    expect(cards()[1].textContent).toContain('1:35');
    expect(cards()[1].textContent).toContain('8m');
    // the last chapter runs to the end of the file
    expect(cards()[3].textContent).toContain('5m');
  });

  it('marks the playing chapter, with how far in, and opens focused on it', () => {
    const { cards, container } = mount(347.5);
    const now = cards()[1];
    expect(now.getAttribute('aria-current')).toBe('true');
    expect(now.getAttribute('aria-label')).toBe('The heist, 1:35, playing');
    expect(document.activeElement).toBe(now);
    const bar = container.querySelector('[aria-current="true"] span[style]') as HTMLElement;
    expect(bar.style.width).toBe('50%');
    expect(cards()[0].getAttribute('aria-current')).toBeNull();
  });

  it('moves along with the arrow keys, home and end', () => {
    const { cards } = mount(300);
    fireEvent.keyDown(document.activeElement!, { key: 'ArrowRight' });
    expect(document.activeElement).toBe(cards()[2]);
    fireEvent.keyDown(document.activeElement!, { key: 'End' });
    expect(document.activeElement).toBe(cards()[3]);
    fireEvent.keyDown(document.activeElement!, { key: 'ArrowRight' });
    expect(document.activeElement).toBe(cards()[3]);
    fireEvent.keyDown(document.activeElement!, { key: 'Home' });
    expect(document.activeElement).toBe(cards()[0]);
    fireEvent.keyDown(document.activeElement!, { key: 'ArrowLeft' });
    expect(document.activeElement).toBe(cards()[0]);
  });

  it('plays from a chapter when picked, and escape closes', () => {
    const { cards, onPick, onClose } = mount();
    fireEvent.click(cards()[2]);
    expect(onPick).toHaveBeenCalledWith(600);
    fireEvent.keyDown(document.activeElement!, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: 'Close chapters' }));
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it('a chapter without a picture, or with a broken one, shows its number', () => {
    const { cards, container } = mount();
    expect(cards()[2].querySelector('img')).toBeNull();
    expect(cards()[2].textContent).toContain('3');
    const first = container.querySelector('img') as HTMLImageElement;
    fireEvent.error(first);
    expect(cards()[0].querySelector('img')).toBeNull();
  });

  it('loads the pictures near the playing chapter first', () => {
    const { container } = mount(0);
    const imgs = Array.from(container.querySelectorAll('img'));
    expect(imgs.every((i) => i.getAttribute('loading') === 'eager')).toBe(true);
    expect(imgs.every((i) => i.getAttribute('decoding') === 'async')).toBe(true);
  });

  it('says how long a chapter is in plain words', () => {
    expect(span(42)).toBe('42s');
    expect(span(505)).toBe('8m');
    expect(span(3900)).toBe('1h 5m');
    expect(span(0)).toBe('');
  });
});
