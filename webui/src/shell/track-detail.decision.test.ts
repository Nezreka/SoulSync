import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { renderDecisionBlock } from './track-detail';

/** The track-detail "why this file" block (download_decisions). */

let box: HTMLElement;

beforeEach(() => {
  document.body.innerHTML = '<section id="td-decision" hidden></section>';
  box = document.getElementById('td-decision')!;
});

afterEach(() => {
  document.body.innerHTML = '';
});

const reject = (code: string, stage: string, detail = '') => ({
  accepted: false,
  code,
  stage,
  detail,
  score: 0.4,
});

describe('renderDecisionBlock', () => {
  it('stays hidden with nothing recorded', () => {
    renderDecisionBlock(null);
    expect(box.hidden).toBe(true);
    renderDecisionBlock({ outcome: '' });
    expect(box.hidden).toBe(true);
  });

  it('names the winner and what it beat', () => {
    renderDecisionBlock({
      outcome: 'chosen',
      chosen: {
        display_name: '04 - Fade Into You.flac',
        source_service: 'soulseek',
        username: 'vinylhead',
        quality_label: 'FLAC 16-bit',
        decision: { accepted: true, code: 'accepted', stage: 'decision', detail: '', score: 0.97 },
      },
      alternatives: [
        {
          display_name: 'Fade Into You.mp3',
          source_service: 'soulseek',
          decision: {
            accepted: true,
            code: 'accepted',
            stage: 'decision',
            detail: '',
            score: 0.88,
          },
        },
        {
          display_name: 'Fade Into You (Live).flac',
          source_service: 'tidal',
          decision: reject('version_conflict', 'version', 'live version, asked for the original'),
        },
      ],
      accepted_total: 2,
      rejected_total: 7,
      rejected_counts: { match_weak: 5, version_conflict: 2 },
    });
    expect(box.hidden).toBe(false);
    expect(box.querySelector('h3')?.textContent).toBe('Why this file');
    expect(box.querySelector('.td-decision-win')?.textContent).toContain('04 - Fade Into You.flac');
    expect(box.querySelector('.td-decision-win')?.textContent).toContain(
      'Soulseek · vinylhead · FLAC 16-bit · match 97%',
    );
    expect(box.querySelector('.td-decision-summary')?.textContent).toBe(
      '1 other also passed. 7 passed over: 5 weak match · 2 wrong version.',
    );
    const pills = [...box.querySelectorAll('.td-decision-pill')].map((p) => p.textContent);
    expect(pills).toEqual(['passed', 'version: live']);
    expect(box.querySelectorAll('.td-decision-pill')[1].getAttribute('title')).toBe(
      'live version, asked for the original',
    );
  });

  it('answers "why did nothing download" in one line', () => {
    renderDecisionBlock({
      outcome: 'nothing_passed',
      alternatives: [
        {
          display_name: 'x.flac',
          source_service: 'qobuz',
          decision: reject('duration_mismatch', 'duration'),
        },
      ],
      rejected_total: 15,
      rejected_counts: { duration_mismatch: 9, version_conflict: 4, below_profile: 2 },
    });
    expect(box.querySelector('h3')?.textContent).toBe('Why nothing was downloaded');
    expect(box.querySelector('.td-decision-summary')?.textContent).toBe(
      'Nothing passed: 9 wrong length · 4 wrong version · 2 below your profile.',
    );
    expect(box.querySelector('.td-decision-subtitle')?.textContent).toBe('Closest results');
  });

  it('says so when the search was empty or nothing would start', () => {
    renderDecisionBlock({ outcome: 'nothing_passed', rejected_total: 0 });
    expect(box.querySelector('.td-decision-summary')?.textContent).toBe(
      'The search came back empty.',
    );
    renderDecisionBlock({ outcome: 'download_failed', accepted_total: 3, rejected_total: 0 });
    expect(box.querySelector('.td-decision-summary')?.textContent).toBe(
      '3 passed the checks, but none of them would start downloading.',
    );
    expect(box.querySelectorAll('h3')).toHaveLength(1);
  });

  it('treats file names from other people as text', () => {
    renderDecisionBlock({
      outcome: 'chosen',
      chosen: {
        display_name: '<img src=x onerror="window.pwned=1">.flac',
        source_service: 'soulseek',
      },
    });
    expect(box.querySelector('img')).toBeNull();
    expect(box.textContent).toContain('<img src=x');
    expect(box.querySelector('.td-decision-summary')?.textContent).toBe('It was the only match.');
  });
});
