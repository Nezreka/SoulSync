import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';

import { ResetTaste, RESET_TASTE_HINT } from './blacklist-modal';
import { FeedbackMenu, feedbackItems, feedbackToast, NOT_NOW_DAYS } from './feedback-menu';

/** The ⋯ on a recommendation, and the reset that forgets the answers. */

const ENTITY = { type: 'artist' as const, name: 'Soen', ids: { deezer: 'dz-soen' } };
const EXPLANATION = { kind: 'listened', seeds: [{ name: 'Tool' }], confidence: 0.5 };

let posted: Record<string, unknown>[] = [];
let toast: ReturnType<typeof vi.fn>;

beforeEach(() => {
  posted = [];
  toast = vi.fn();
  window.showToast = toast as never;
  server.use(
    http.post('*/api/discover/feedback', async ({ request }) => {
      posted.push((await request.json()) as Record<string, unknown>);
      return HttpResponse.json({ success: true, id: 7 });
    }),
  );
});

afterEach(() => {
  cleanup();
  server.resetHandlers();
  delete (window as { showToast?: unknown }).showToast;
});

function open(onHidden = vi.fn()) {
  render(<FeedbackMenu entity={ENTITY} explanation={EXPLANATION} onHidden={onHidden} />);
  fireEvent.click(screen.getByLabelText('Tell discovery about Soen'));
  return onHidden;
}

describe('the menu', () => {
  it('offers save and the four answers, block last and set apart', () => {
    const items = feedbackItems(ENTITY, vi.fn());
    expect(items.map((i) => i.label)).toEqual([
      'Save for later',
      'More like this',
      'Less like this',
      'Not now',
      'Block Soen',
    ]);
    expect(items.map((i) => Boolean(i.danger))).toEqual([false, false, false, false, true]);
    expect(feedbackToast('save', ENTITY)).toBe('Saved to your inbox');
  });

  it('blocks the ARTIST of a track, and says so', () => {
    const track = { type: 'track' as const, name: 'Lotus', artist_name: 'Soen' };
    expect(feedbackItems(track, vi.fn())[4].label).toBe('Block Soen');
    expect(feedbackToast('block', track)).toBe('Blocked Soen');
    expect(feedbackToast('not_now', track)).toBe('Hidden for 30 days');
    expect(NOT_NOW_DAYS).toBe(30);
    expect(feedbackToast('more', track)).toBe("You'll see more like Lotus");
    expect(feedbackToast('less', ENTITY)).toBe("You'll see less like Soen");
  });

  it('sends the answer with the explanation it was shown with', async () => {
    const onHidden = open();
    fireEvent.click(screen.getByText('More like this'));
    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toEqual({ action: 'more', entity: ENTITY, explanation: EXPLANATION });
    await waitFor(() => expect(toast).toHaveBeenCalledWith("You'll see more like Soen", 'success'));
    // more / less re-rank; the card stays
    expect(onHidden).not.toHaveBeenCalled();
  });

  it('drops the card at once on not now and on block', async () => {
    for (const label of ['Not now', 'Block Soen']) {
      const onHidden = open();
      fireEvent.click(screen.getByText(label));
      await waitFor(() => expect(onHidden).toHaveBeenCalledTimes(1));
      cleanup();
    }
    expect(posted.map((p) => p.action)).toEqual(['not_now', 'block']);
  });

  it('keeps the card and says so when the answer is not saved', async () => {
    server.use(
      http.post('*/api/discover/feedback', () =>
        HttpResponse.json({ success: false, error: 'no' }, { status: 400 }),
      ),
    );
    const onHidden = open();
    fireEvent.click(screen.getByText('Not now'));
    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith("Couldn't save that. Try again in a moment.", 'error'),
    );
    expect(onHidden).not.toHaveBeenCalled();
  });

  it('opens without following the link the card is', () => {
    render(
      <a href="/artist/1">
        <FeedbackMenu entity={ENTITY} />
      </a>,
    );
    const click = new MouseEvent('click', { bubbles: true, cancelable: true });
    act(() => {
      screen.getByLabelText('Tell discovery about Soen').dispatchEvent(click);
    });
    expect(click.defaultPrevented).toBe(true);
    expect(screen.getByText('More like this')).toBeTruthy();
  });
});

describe('reset taste', () => {
  it('asks once, then forgets the answers and says the blocks stay', async () => {
    let deleted = 0;
    server.use(
      http.delete('*/api/discover/feedback', () => {
        deleted += 1;
        return HttpResponse.json({ success: true, cleared: 3 });
      }),
    );
    render(<ResetTaste />);
    const button = screen.getByText('Reset taste');
    expect(button.getAttribute('title')).toBe(RESET_TASTE_HINT);
    fireEvent.click(button);
    expect(deleted).toBe(0);
    fireEvent.click(screen.getByText('Reset taste? Blocks stay'));
    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith('Reset 3 answers. Blocks kept.', 'success'),
    );
    expect(deleted).toBe(1);
  });

  it('disarms when focus leaves', () => {
    render(<ResetTaste />);
    fireEvent.click(screen.getByText('Reset taste'));
    fireEvent.blur(screen.getByText('Reset taste? Blocks stay'));
    expect(screen.getByText('Reset taste')).toBeTruthy();
  });
});
