import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { AUTOMATION_ICONS, automationIcon, formatNotify } from './-automations.icons';

/**
 * Parity against the vanilla source, not against my transcription.
 *
 * AUTOMATION_ICONS was lifted from _autoIcons in stats-automations.js. The
 * values are surrogate-pair emoji carrying U+FE0F variation selectors, which
 * survive a copy/paste looking identical while differing byte-for-byte — so a
 * hand check proves nothing. This re-evaluates the original object literal and
 * demands exact equality.
 *
 * The vanilla map stays alive after the music page is ported: the VIDEO
 * automations page renders through renderAutomationCard, which uses it. If
 * that ever stops being true this test fails loudly, which is the point —
 * update it deliberately rather than letting the two silently diverge.
 */
function vanillaIcons(): Record<string, string> {
  const src = readFileSync(resolve(process.cwd(), 'static/stats-automations.js'), 'utf8');
  const PREFIX = 'const _autoIcons = ';
  const start = src.indexOf(PREFIX);
  if (start < 0) throw new Error('_autoIcons is gone from stats-automations.js');
  const end = src.indexOf('\n};', start);
  const literal = src.slice(start + PREFIX.length, end + 2);
  // eslint-disable-next-line no-eval -- evaluating a data literal from our own
  // repo is the only way to resolve JS escapes exactly as the browser does.
  return eval(`(${literal})`) as Record<string, string>;
}

describe('automation icons match the vanilla map', () => {
  it('has exactly the same keys', () => {
    expect(Object.keys(AUTOMATION_ICONS).sort()).toEqual(Object.keys(vanillaIcons()).sort());
  });

  it('has byte-identical values, variation selectors included', () => {
    expect(AUTOMATION_ICONS).toEqual(vanillaIcons());
  });

  it('carries a real number of icons, so an empty parse cannot pass', () => {
    // Both sides being {} would satisfy the equality assertions above.
    expect(Object.keys(AUTOMATION_ICONS).length).toBeGreaterThan(80);
  });
});

describe('automationIcon', () => {
  it('resolves known types', () => {
    expect(automationIcon('schedule')).toBe(AUTOMATION_ICONS.schedule);
  });

  it('falls back to the gear for unknown or missing types', () => {
    expect(automationIcon('not_a_real_type')).toBe('⚙️');
    expect(automationIcon(null)).toBe('⚙️');
  });
});

describe('formatNotify', () => {
  it('names the notification channels', () => {
    expect(formatNotify('discord_webhook')).toBe('Discord');
    expect(formatNotify('telegram')).toBe('Telegram');
  });

  it('keeps the inline icon on signal and script', () => {
    expect(formatNotify('fire_signal')).toBe('⚡ Signal');
    expect(formatNotify('run_script')).toBe('💻 Script');
  });

  it('passes through anything unmapped, and empty for missing', () => {
    expect(formatNotify('future_channel')).toBe('future_channel');
    expect(formatNotify(null)).toBe('');
  });
});
