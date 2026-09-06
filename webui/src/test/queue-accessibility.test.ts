import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fireEvent, screen } from '@testing-library/dom';
import { beforeEach, expect, it, vi } from 'vitest';
import { extractFunction } from './vanilla-extract';
const source = readFileSync(resolve(process.cwd(), 'static/media-player.js'), 'utf8');
const functions = ['renderNpQueue', 'npFocusQueueAction', 'npAnnounceQueue', 'npReorderQueue', 'removeFromQueue'].map(name => extractFunction(name, source)).join('\n');
let queue: any;
let play: ReturnType<typeof vi.fn>;
beforeEach(() => {
  document.body.innerHTML = '<div id="np-queue-list"></div>';
  play = vi.fn();
  queue = new Function('document', 'playQueueItem', `
    let npQueue = [
      { title: 'A', artist: 'Artist', file_path: '/a' },
      { title: 'B', artist: 'Artist', file_path: '/b' },
      { title: 'C', artist: 'Artist', file_path: '/c' }
    ], npQueueIndex = 1;
    const npQueueStatusLabel = () => '', npUpdateUpNext = () => {}, npPersistQueue = () => {},
      npQueueDragStart = () => {}, npQueueDragOver = () => {}, npQueueDrop = () => {}, npQueueDragEnd = () => {},
      updateNpPrevNextButtons = () => {}, npScheduleQueuePrefetch = () => {}, npStopQueuePrefetchPolling = () => {};
    ${functions}
    return { render: renderNpQueue, state: () => ({ titles: npQueue.map(t => t.title), index: npQueueIndex }) };
  `)(document, play);
  queue.render();
});
it('exposes named native Play controls without clickable row ambiguity', () => {
  const button = screen.getByRole('button', { name: 'Play C by Artist' });
  expect(button.tagName).toBe('BUTTON');
  fireEvent.click(button);
  expect(play).toHaveBeenCalledWith(2);
});
it('moves a track while keeping playback identity and focus', () => {
  const move = screen.getByRole('button', { name: 'Move C earlier' });
  move.focus(); fireEvent.click(move);
  expect(queue.state()).toEqual({ titles: ['A', 'C', 'B'], index: 2 });
  expect(play).not.toHaveBeenCalled();
  expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Move C earlier' }));
  expect(screen.getByRole('status').textContent).toContain('position 2 of 3');
  queue.render();
  expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Move C earlier' }));
});
it('keeps focus reachable when a move reaches the boundary', () => {
  fireEvent.click(screen.getByRole('button', { name: 'Move B earlier' }));
  expect(screen.getByRole('button', { name: 'Move B earlier' })).toBeDisabled();
  expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Play B by Artist' }));
});
it('restores focus after removing a queued track', () => {
  fireEvent.click(screen.getByRole('button', { name: 'Remove A from queue' }));
  expect(queue.state()).toEqual({ titles: ['B', 'C'], index: 0 });
  expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Remove B from queue' }));
  expect(play).not.toHaveBeenCalled();
});
