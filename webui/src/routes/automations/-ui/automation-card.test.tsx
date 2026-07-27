import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { Automation } from '../-automations.types';

import { AutomationCard } from './automation-card';

function auto(over: Partial<Automation> & { id: number }): Automation {
  return { name: `auto-${over.id}`, ...over };
}

const card = () => document.querySelector('.automation-card')!;

describe('AutomationCard markup', () => {
  it('carries the classes and data attributes the CSS and vanilla filter rely on', () => {
    render(
      <AutomationCard
        automation={auto({
          id: 7,
          name: 'Nightly',
          enabled: 1,
          trigger_type: 'schedule',
          action_type: 'process_wishlist',
        })}
      />,
    );
    const el = card();
    expect(el.className).toBe('automation-card');
    expect(el.getAttribute('data-id')).toBe('7');
    // _filterAutomations reads these two to apply the dropdowns.
    expect(el.getAttribute('data-trigger-type')).toBe('schedule');
    expect(el.getAttribute('data-action-type')).toBe('process_wishlist');
    expect(document.querySelector('.automation-status')?.className).toContain('enabled');
  });

  it('marks disabled and system rows with their modifier classes', () => {
    render(<AutomationCard automation={auto({ id: 1, enabled: 0, is_system: 1 })} />);
    expect(card().className).toBe('automation-card disabled system');
    expect(document.querySelector('.automation-status')?.className).toContain('disabled');
  });

  it('renders the trigger → action flow with icons', () => {
    render(
      <AutomationCard
        automation={auto({
          id: 1,
          trigger_type: 'schedule',
          trigger_config: { interval: 6, unit: 'hours' },
          action_type: 'process_wishlist',
        })}
      />,
    );
    expect(document.querySelector('.flow-trigger')?.textContent).toContain('Every 6 hours');
    expect(document.querySelector('.flow-action')?.textContent).toContain('Process Wishlist');
  });

  it('shows the delay chip only when a delay is configured', () => {
    const { unmount } = render(
      <AutomationCard automation={auto({ id: 1, action_config: { delay: 5 } })} />,
    );
    expect(document.querySelector('.flow-delay')?.textContent).toContain('5m');
    unmount();
    render(<AutomationCard automation={auto({ id: 2, action_config: { delay: 0 } })} />);
    expect(document.querySelector('.flow-delay')).toBeNull();
  });

  it('renders one notify chip per then_action', () => {
    render(
      <AutomationCard
        automation={auto({
          id: 1,
          then_actions: [{ type: 'discord_webhook' }, { type: 'telegram' }],
        })}
      />,
    );
    const chips = [...document.querySelectorAll('.flow-notify')].map((n) => n.textContent);
    expect(chips).toEqual(['Discord', 'Telegram']);
  });

  it('hides duplicate/group/delete on system automations', () => {
    const { unmount } = render(<AutomationCard automation={auto({ id: 1, is_system: 1 })} />);
    expect(document.querySelector('.automation-dupe-btn')).toBeNull();
    expect(document.querySelector('.automation-group-btn')).toBeNull();
    expect(document.querySelector('.automation-delete-btn')).toBeNull();
    // Run / toggle / edit stay: system automations are still runnable.
    expect(document.querySelector('.automation-run-btn')).not.toBeNull();
    expect(document.querySelector('.automation-edit-btn')).not.toBeNull();
    unmount();

    render(<AutomationCard automation={auto({ id: 2 })} />);
    expect(document.querySelector('.automation-dupe-btn')).not.toBeNull();
    expect(document.querySelector('.automation-delete-btn')).not.toBeNull();
  });

  it('omits data-next so the vanilla 1s interval cannot fight React over the node', () => {
    // stats-automations.js rewrites `.auto-next-run[data-next]` document-wide
    // every second. Keeping the class but not the attribute leaves this node
    // outside that selector.
    render(
      <AutomationCard
        automation={auto({
          id: 1,
          enabled: 1,
          trigger_type: 'schedule',
          next_run: new Date(Date.now() + 3_600_000).toISOString(),
        })}
      />,
    );
    const next = document.querySelector('.auto-next-run');
    expect(next).not.toBeNull();
    expect(next!.hasAttribute('data-next')).toBe(false);
  });
});

describe('AutomationCard actions', () => {
  it('fires each handler with the automation', () => {
    const onRun = vi.fn();
    const onToggle = vi.fn();
    const onDelete = vi.fn();
    const a = auto({ id: 3, name: 'Nightly', enabled: 1 });
    render(<AutomationCard automation={a} onRun={onRun} onToggle={onToggle} onDelete={onDelete} />);

    fireEvent.click(document.querySelector('.automation-run-btn')!);
    expect(onRun).toHaveBeenCalledWith(a);

    fireEvent.click(screen.getByLabelText('Disable Nightly'));
    expect(onToggle).toHaveBeenCalledWith(a);

    fireEvent.click(document.querySelector('.automation-delete-btn')!);
    expect(onDelete).toHaveBeenCalledWith(a);
  });

  it('offers the run-history link only once the automation has run', () => {
    const { unmount } = render(<AutomationCard automation={auto({ id: 1, run_count: 0 })} />);
    expect(document.querySelector('.auto-runs-link')).toBeNull();
    unmount();
    render(<AutomationCard automation={auto({ id: 2, run_count: 4 })} />);
    expect(document.querySelector('.auto-runs-link')?.textContent).toBe('Runs: 4');
  });
});
