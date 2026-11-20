/**
 * #2180: a worker's status could not be read on a phone. The status is the
 * worker's hover tooltip, and a tap went straight to the worker's toggle
 * (or did nothing for the display-only ones). On a phone a tap now opens
 * the worker's sheet; the toggle is a button in it.
 */

import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { useRef } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { orbSheetAction } from '../-dash.header';
import { videoWorkerAction } from '../-video/video-hero';
import { PHONE_QUERY, readWorker, useWorkerSheet, WorkerSheet } from './worker-sheet';

let phone = true;
beforeEach(() => {
  phone = true;
  vi.stubGlobal(
    'matchMedia',
    vi.fn((query: string) => ({ matches: query === PHONE_QUERY && phone, media: query })),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

/** a music orb as dashboard-header.tsx renders it */
function Stage({
  onToggle,
  describe = () => ({ label: 'Pause' }),
  status = 'Running',
}: {
  onToggle: () => void;
  describe?: (b: HTMLElement) => { label: string; closes?: boolean } | null;
  status?: string;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  const sheet = useWorkerSheet(ref);
  return (
    <div className="orb-stage" ref={ref}>
      <div className="enrich-worker lastfm-enrich-button-container">
        <button
          className="enrich-worker-btn lastfm-enrich-button active"
          id="lastfm-enrich-button"
          title="Last.fm"
          onClick={onToggle}
        >
          <img src="/static/img/brands/lastfm.svg" alt="Last.fm" />
          <div className="enrich-worker-spinner"></div>
        </button>
        <div className="enrich-worker-tooltip">
          <div className="enrich-worker-tooltip-content">
            <div className="enrich-worker-tooltip-header">Last.fm Enrichment</div>
            <div className="enrich-worker-tooltip-body">
              <div className="tooltip-status">
                Status: <span id="lastfm-status">{status}</span>
              </div>
              <div className="tooltip-current">Now: Radiohead</div>
              <div className="tooltip-progress">Progress: 40 / 120</div>
            </div>
          </div>
        </div>
      </div>
      <WorkerSheet
        button={sheet.button}
        describe={describe}
        onClose={sheet.close}
        onManage={() => {}}
      />
    </div>
  );
}

const orb = () => document.getElementById('lastfm-enrich-button')!;

describe('a worker on a phone', () => {
  it('a tap opens its status instead of toggling it', () => {
    const onToggle = vi.fn();
    render(<Stage onToggle={onToggle} />);
    fireEvent.click(orb());
    expect(onToggle).not.toHaveBeenCalled();
    const dialog = screen.getByRole('dialog');
    expect(dialog.textContent).toContain('Last.fm Enrichment');
    expect(dialog.textContent).toContain('Status: Running');
    expect(dialog.textContent).toContain('Now: Radiohead');
    expect(dialog.textContent).toContain('Progress: 40 / 120');
  });

  it("the sheet's button is the tap's old action, and the sheet follows the worker", async () => {
    const onToggle = vi.fn();
    render(<Stage onToggle={onToggle} />);
    fireEvent.click(orb());
    fireEvent.click(screen.getByRole('button', { name: 'Pause' }));
    expect(onToggle).toHaveBeenCalledOnce();
    expect(screen.getByRole('dialog')).toBeTruthy();
    // the worker's own markup changes (a socket push): the sheet reads it again
    await act(async () => {
      document.getElementById('lastfm-status')!.textContent = 'Paused';
      await Promise.resolve();
    });
    expect(screen.getByRole('dialog').textContent).toContain('Status: Paused');
  });

  it('an action that leaves the page closes the sheet', () => {
    const onToggle = vi.fn();
    render(
      <Stage
        onToggle={onToggle}
        describe={() => ({ label: 'Open Library Maintenance', closes: true })}
      />,
    );
    fireEvent.click(orb());
    fireEvent.click(screen.getByRole('button', { name: 'Open Library Maintenance' }));
    expect(onToggle).toHaveBeenCalledOnce();
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('no action, no button: the status alone', () => {
    render(<Stage onToggle={vi.fn()} describe={() => null} />);
    fireEvent.click(orb());
    expect(
      screen.getAllByRole('button', { hidden: false }).map((b) => b.textContent),
    ).not.toContain('Pause');
    expect(screen.getByRole('button', { name: 'Manage workers' })).toBeTruthy();
  });

  it('with a mouse and room, a click still toggles and opens nothing', () => {
    phone = false;
    const onToggle = vi.fn();
    render(<Stage onToggle={onToggle} />);
    fireEvent.click(orb());
    expect(onToggle).toHaveBeenCalledOnce();
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});

describe('what the sheet reads', () => {
  it("reads the worker's tooltip and logo", () => {
    render(<Stage onToggle={vi.fn()} />);
    expect(readWorker(orb())).toEqual({
      name: 'Last.fm Enrichment',
      status: 'Status: Running',
      current: 'Now: Radiohead',
      progress: 'Progress: 40 / 120',
      logo: { src: '/static/img/brands/lastfm.svg', alt: 'Last.fm' },
    });
  });
});

describe("the music orbs' actions (onOrbClick, read for the state)", () => {
  it.each([
    ['lastfm', 'active', 'Pause'],
    ['lastfm', 'paused', 'Resume'],
    ['lastfm', 'complete', null],
    ['lastfm', null, null],
    ['spotify', 'active', 'Pause'],
    ['bandcamp', 'no-auth', null],
    ['discogs', 'complete', 'Resume'],
    ['discogs', 'active', 'Pause'],
    ['hydrabase', 'active', 'Stop'],
    ['hydrabase', null, 'Start'],
    ['soulid', 'active', null],
    ['repair', null, 'Open Library Maintenance'],
  ] as const)('%s %s -> %s', (id, state, label) => {
    expect(orbSheetAction(id, state)?.label ?? null).toBe(label);
  });

  it('only the repair orb leaves the page', () => {
    expect(orbSheetAction('repair', null)?.closes).toBe(true);
    expect(orbSheetAction('lastfm', 'active')?.closes).toBeUndefined();
  });
});

describe("the video workers' action (video-enrichment.js toggle)", () => {
  const button = (cls: string) => {
    const b = document.createElement('button');
    b.className = `video-enrich-button ${cls}`;
    return b;
  };
  it('paused resumes, anything else pauses, switched off does nothing', () => {
    expect(videoWorkerAction(button('paused'))?.label).toBe('Resume');
    expect(videoWorkerAction(button('active'))?.label).toBe('Pause');
    expect(videoWorkerAction(button('complete'))?.label).toBe('Pause');
    expect(videoWorkerAction(button('disabled'))).toBeNull();
  });
});
