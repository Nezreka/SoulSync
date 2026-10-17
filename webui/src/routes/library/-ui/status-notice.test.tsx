import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { StatusNotice } from './status-notice';

afterEach(() => vi.useRealTimers());

describe('background activity notices', () => {
  it('renders outside the page content and removes the notice when its owner unmounts', () => {
    const view = render(
      <main>
        <StatusNotice tone="busy" text="Fetching full discography…" />
      </main>,
    );
    const notice = screen.getByRole('status');
    expect(view.container).not.toContainElement(notice);
    expect(screen.getByRole('region', { name: 'Background activity' })).toContainElement(notice);
    view.unmount();
    expect(screen.queryByText('Fetching full discography…')).not.toBeInTheDocument();
  });

  it('expires a completed update without expiring the next operation', async () => {
    vi.useFakeTimers();
    const view = render(
      <StatusNotice tone="ok" text="Discography updated from Spotify: 0 matched" />,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000);
    });
    view.rerender(<StatusNotice tone="busy" text="Searching…" />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(screen.getByText('Searching…')).toBeInTheDocument();
    view.rerender(<StatusNotice tone="ok" text="Search started" />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(screen.queryByText('Search started')).not.toBeInTheDocument();
    view.rerender(<StatusNotice tone="busy" text="Searching…" />);
    view.rerender(<StatusNotice tone="ok" text="Search started" />);
    expect(screen.getByText('Search started')).toBeInTheDocument();
  });

  it('keeps failure actions available and allows dismissing a notice', async () => {
    vi.useFakeTimers();
    const retry = vi.fn();
    render(
      <StatusNotice tone="err" text="Wishlist sync failed">
        <button onClick={retry}>Retry</button>
      </StatusNotice>,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(retry).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss notification' }));
    expect(screen.queryByText('Wishlist sync failed')).not.toBeInTheDocument();
  });
});
