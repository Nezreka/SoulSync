import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';

import { YEAR_STORY_HREF, YearReadyBanner } from './year-ready-banner';

let dismissed = 0;

function serve(state: Record<string, unknown>) {
  server.use(
    http.get('/api/stats/year/banner', () => HttpResponse.json({ success: true, ...state })),
    http.post('/api/stats/year/banner/dismiss', () => {
      dismissed += 1;
      return HttpResponse.json({ success: true });
    }),
  );
}

beforeEach(() => {
  dismissed = 0;
  window.showToast = vi.fn();
  window.SoulSyncWebRouter = { navigateToHref: vi.fn(async () => true) } as never;
});
afterEach(cleanup);

describe('your year is ready', () => {
  it('announces the year in season and opens the story', async () => {
    serve({ show: true, year: 2026 });
    render(<YearReadyBanner />);
    expect(await screen.findByText('Your 2026 is ready')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Open your year' }));
    expect(window.SoulSyncWebRouter?.navigateToHref).toHaveBeenCalledWith(YEAR_STORY_HREF);
    expect(YEAR_STORY_HREF).toBe('/stats?story=year');
  });

  it('renders nothing out of season', async () => {
    serve({ show: false, year: null });
    const { container } = render(<YearReadyBanner />);
    await new Promise((r) => setTimeout(r, 30));
    expect(container.querySelector('.year-ready')).toBeNull();
  });

  it('hides when dismissed and tells the server', async () => {
    serve({ show: true, year: 2026 });
    const { container } = render(<YearReadyBanner />);
    fireEvent.click(await screen.findByLabelText('Hide this'));
    await waitFor(() => expect(dismissed).toBe(1));
    expect(container.querySelector('.year-ready')).toBeNull();
  });
});
