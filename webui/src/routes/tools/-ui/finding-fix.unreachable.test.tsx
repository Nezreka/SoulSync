import { renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const fixFinding = vi.fn();
vi.mock('../-tools.api', async (original) => ({
  ...(await original<typeof import('../-tools.api')>()),
  fixFinding: (...args: unknown[]) => fixFinding(...args),
}));

import type { RepairFinding } from '../-tools.types';

import { TYPE_UNREACHABLE, useFindingFix } from './finding-fix';

const finding = {
  id: 9,
  finding_type: TYPE_UNREACHABLE,
  title: "30 of 30 files can't be reached",
  status: 'pending',
  details: { outage: { unreachable: 30, checked: 30 } },
} as unknown as RepairFinding;

function fixer() {
  const refresh = vi.fn();
  const { result } = renderHook(() =>
    useFindingFix({
      video: false,
      typeInfo: () => ({ label: 'Unreachable Library', verb: 'They are gone', destructive: true }),
      prompts: {} as never,
      dismiss: vi.fn(),
      refresh,
      onReidentify: vi.fn(),
      setBusy: vi.fn(),
    } as never),
  );
  return { fix: result.current, refresh };
}

describe('answering an unreachable library finding (#2005)', () => {
  const confirm = vi.fn();
  beforeEach(() => {
    fixFinding
      .mockReset()
      .mockResolvedValue({ success: true, message: '30 files marked missing.' });
    confirm.mockReset();
    window.showConfirmDialog = confirm as never;
    window.showToast = vi.fn() as never;
  });
  afterEach(() => vi.restoreAllMocks());

  it('asks first, in plain words, and does nothing on cancel', async () => {
    confirm.mockResolvedValue(false);
    await fixer().fix(finding);
    expect(confirm).toHaveBeenCalledTimes(1);
    const asked = confirm.mock.calls[0][0];
    expect(asked.title).toBe('Are these files really gone?');
    expect(asked.message).toContain('30 files');
    expect(asked.message).toContain('Dead Files');
    expect(asked.destructive).toBe(true);
    expect(fixFinding).not.toHaveBeenCalled();
  });

  it('runs the fix once confirmed', async () => {
    confirm.mockResolvedValue(true);
    await fixer().fix(finding);
    expect(fixFinding).toHaveBeenCalledTimes(1);
    expect(fixFinding.mock.calls[0][0]).toBe(9);
  });
});
