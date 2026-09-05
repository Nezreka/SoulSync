import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * "Cleanup recommended" is a verdict, not an answer.
 *
 * Boulder: "i see on tools page it said my cache health was not healthy so i
 * click it and it opened the cache health modal where it says cleanup
 * recommeneded. but how lol. we need to make it clear to the user hwo to manage
 * this and resolve it"
 *
 * The awkward part of the answer is that the user is usually meant to do
 * NOTHING. The Cache Maintenance job (cache_evictor) runs every six hours by
 * default and calls exactly the routines that clear the two counters the
 * verdict is computed from - clean_junk_entities and
 * clean_stale_musicbrainz_nulls. So the panel's job is to say that, and to say
 * loudly when the job is switched off, which is the one case that really does
 * need a human.
 *
 * The modal is deliberately vanilla (findings-surface.tsx says so: it is shared
 * with the Metadata Cache card and opens onward into the failed-MB manager), so
 * this reads enrichment.js rather than a component.
 */

const JS = readFileSync(resolve(process.cwd(), 'static/enrichment.js'), 'utf8');
const CSS = readFileSync(resolve(process.cwd(), 'static/style.css'), 'utf8');

const MODAL = JS.slice(
  JS.indexOf('async function openCacheHealthModal'),
  JS.indexOf('function _cacheAgoText'),
);

describe('the modal explains the verdict', () => {
  it('says what a junk entry actually is', () => {
    expect(MODAL).toContain('junk entries');
    expect(MODAL).toContain('Unknown Artist');
  });

  it('says what a failed MusicBrainz lookup is and that it expires', () => {
    expect(MODAL).toContain('failed MusicBrainz lookups');
    expect(MODAL).toContain('older than 30 days');
  });

  it('actually RENDERS the panel, not just builds it', () => {
    // Caught by negative-checking: deleting ${explain} from the rendered
    // template left every other assertion here passing, because they all check
    // that the HTML is constructed. Built-and-discarded looks identical to
    // built-and-shown unless you pin the render site.
    const render = MODAL.slice(MODAL.indexOf('body.innerHTML = `'));
    expect(render).toContain('${explain}');
    expect(render.indexOf('${explain}')).toBeLessThan(render.indexOf('cache-health-cards'));
  });

  it('only explains when there is something wrong', () => {
    // A healthy cache does not need a paragraph about problems it does not have.
    expect(MODAL).toContain("healthScore === 'healthy' ? '' :");
  });

  it('lists a problem only when its count is above zero', () => {
    expect(MODAL).toContain('if (s.junk_entities > 0)');
    expect(MODAL).toContain('if (s.stale_mb_nulls > 0)');
  });
});

describe('it names the thing that fixes this', () => {
  it('tells the user it is automatic, which is the real answer', () => {
    expect(MODAL).toContain('Cache Maintenance');
    expect(MODAL).toContain('automatically');
    expect(MODAL).toContain('Nothing is required from you');
  });

  it('reads the job so it can report the schedule and last run', () => {
    expect(MODAL).toContain("/api/repair/jobs");
    expect(MODAL).toContain("j.job_id === 'cache_evictor'");
    expect(MODAL).toContain('last run');
  });

  it('survives that lookup failing, since the stats are the point', () => {
    expect(MODAL).toContain('the panel still works without it');
  });

  it('shouts when the job is switched off, the one case needing a human', () => {
    expect(MODAL).toContain('cleanupJob.enabled === false');
    expect(MODAL).toContain('Cache Maintenance is switched off');
    expect(CSS).toContain('.cache-health-explain.warn');
  });
});

describe('and gives a way to act on it', () => {
  it('offers a button that runs the cleanup job', () => {
    expect(MODAL).toContain('id="cache-cleanup-now"');
    expect(JS).toContain("/api/repair/jobs/cache_evictor/run");
  });

  it('does not refuse the click just because the schedule is disabled', () => {
    // Someone who turned the schedule off and then explicitly asked for a
    // cleanup should get one.
    expect(JS).toContain('respect_enabled is deliberately NOT sent');
  });

  it('reports failure instead of leaving a dead button', () => {
    expect(JS).toContain('Could not start it:');
    expect(JS).toContain("cleanupBtn.textContent = 'Clean up now'");
  });
});

describe('the last-run text', () => {
  it('says how long ago rather than printing a timestamp', () => {
    // A bare timestamp makes the reader do the subtraction, which is the whole
    // question they are asking.
    expect(JS).toContain('function _cacheAgoText');
    for (const phrase of ['just now', 'minutes ago', 'hours ago', 'yesterday']) {
      expect(JS).toContain(phrase);
    }
  });

  it('never renders a negative age from a clock skew', () => {
    expect(JS).toContain("mins < 0) return 'recently'");
  });
});
