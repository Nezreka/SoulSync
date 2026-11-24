import { act, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { NoticeHost, notify, notifyError } from './notice';

describe('the player notice', () => {
  it("says what went wrong, the server's words first, and goes away", () => {
    vi.useFakeTimers();
    render(<NoticeHost />);
    act(() => notifyError("Couldn't save that setting")(new Error('Profile is read only')));
    expect(screen.getByText('Profile is read only')).toBeTruthy();
    act(() => notifyError("Couldn't save that setting")({}));
    expect(screen.getByText("Couldn't save that setting")).toBeTruthy();
    // the same words twice show once
    act(() => notify("Couldn't save that setting", 'error'));
    expect(screen.getAllByText("Couldn't save that setting")).toHaveLength(1);
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(screen.queryByRole('status')).toBeNull();
    vi.useRealTimers();
  });
});
