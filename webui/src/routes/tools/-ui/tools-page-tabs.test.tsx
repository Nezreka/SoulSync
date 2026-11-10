import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * The Tools page is four pills: Findings, Jobs, History, Tools.
 *
 * It was two tabs (Operations, Tools), and Operations was itself two modes
 * (Simple, Advanced) with a jump nav inside the second. The findings list only
 * existed in Advanced, so "review these" in Simple switched modes under you.
 * Boulder: "how findings work, how the page is laid out, the advanced mode. it
 * all isn't very intuitive". Each thing now has one home.
 *
 * Before that, Operations scrolled inside its own capped box while the tool
 * cards sat below it, which gave the page two scrollbars. The cap came out
 * when the cards got their own tab and must stay out.
 *
 * These read the source rather than rendering: the page mounts the whole
 * maintenance surface, which fans out into a dozen polling hooks. The contract
 * that matters here is structural. maintenance-hero.test.tsx renders it.
 */

const PAGE = readFileSync(resolve(process.cwd(), 'src/routes/tools/-ui/tools-page.tsx'), 'utf8');
const HERO = readFileSync(
  resolve(process.cwd(), 'src/routes/tools/-ui/maintenance-hero.tsx'),
  'utf8',
);
const OPERATIONS = readFileSync(
  resolve(process.cwd(), 'src/routes/tools/-ui/operations.tsx'),
  'utf8',
);
const CSS = readFileSync(resolve(process.cwd(), 'static/style.css'), 'utf8').replace(
  /\/\*[\s\S]*?\*\//g,
  '',
);

describe('the pills', () => {
  it('opens on Findings, because that is the one with work waiting in it', () => {
    expect(PAGE).toContain("useState<ToolsTab>(maintenance ? 'findings' : 'tools')");
  });

  it('offers Findings, Jobs, History, then Tools', () => {
    const order = ["['findings', 'Findings']", "['jobs', 'Jobs']", "['history', 'History']"];
    const at = order.map((entry) => PAGE.indexOf(entry));
    expect(at.every((index) => index > -1)).toBe(true);
    expect([...at].sort((a, b) => a - b)).toEqual(at);
    expect(PAGE).toContain("[...MAINTENANCE_TABS, ['tools', 'Tools']]");
  });

  it('is a real tablist, not a row of buttons', () => {
    expect(PAGE).toContain('role="tablist"');
    expect(PAGE).toContain('role="tab"');
    expect(PAGE).toContain('role="tabpanel"');
    expect(PAGE).toContain('aria-selected={tab === value}');
    expect(PAGE).toContain('aria-controls={`tools-panel-${value}`}');
  });

  it('hides the inactive panels instead of unmounting them', () => {
    // The maintenance surface polls job progress and holds the findings filter
    // and page you were on. Unmounting on every pill would restart the polling
    // and throw that away, so every panel stays mounted.
    for (const panel of ['findings', 'jobs', 'history']) {
      expect(HERO).toContain(`id="tools-panel-${panel}"`);
      expect(HERO).toContain(`hidden={tab !== '${panel}'}`);
    }
    expect(PAGE).toContain("hidden={tab !== 'tools'}");
    expect(HERO).not.toMatch(/tab === '\w+' \? </);
  });

  it('gives each thing one home', () => {
    const findings = HERO.indexOf('id="tools-panel-findings"');
    const jobs = HERO.indexOf('id="tools-panel-jobs"');
    const history = HERO.indexOf('id="tools-panel-history"');
    expect(findings).toBeGreaterThan(-1);
    expect(jobs).toBeGreaterThan(findings);
    expect(history).toBeGreaterThan(jobs);
    const between = (name: string, from: number, to: number) => {
      const at = HERO.indexOf(name, from);
      return at > -1 && at < to;
    };
    expect(between('<FindingsSurface', findings, jobs)).toBe(true);
    expect(between('<OperationsStudio', jobs, history)).toBe(true);
    expect(between('<Operations\n', jobs, history)).toBe(true);
    expect(HERO.indexOf('<RunHistory', history)).toBeGreaterThan(history);
    // each of them appears once
    for (const name of ['<FindingsSurface', '<OperationsStudio', '<RunHistory']) {
      expect(HERO.split(name).length - 1).toBe(1);
    }
    // and every tool card section belongs to Tools
    expect(PAGE.indexOf('<ToolsSection')).toBeGreaterThan(PAGE.indexOf('id="tools-panel-tools"'));
  });
});

describe('there is no Simple or Advanced', () => {
  it('no mode switch, no saved mode, nothing that flips one', () => {
    for (const source of [PAGE, HERO, OPERATIONS]) {
      expect(source).not.toContain('soulsync_operations_mode');
      expect(source).not.toContain('Advanced Mode');
      expect(source).not.toContain("'simple' | 'advanced'");
    }
  });

  it("a job's findings open the Findings pill", () => {
    expect(HERO).toContain("onTab('findings')");
  });

  it('a /tools?job= link opens the Jobs pill, where the row is', () => {
    const at = HERO.indexOf('linkedJobId(');
    expect(at).toBeGreaterThan(-1);
    expect(HERO.slice(at, at + 400)).toContain("onTab('jobs')");
  });
});

describe('the inner scroll box is gone', () => {
  it('gives the maintenance surface no box, cap or scrollbar of its own', () => {
    const at = CSS.indexOf('#tools-page .tools-maintenance-hero {');
    expect(at).toBeGreaterThan(-1);
    const rule = CSS.slice(at, CSS.indexOf('}', at));
    expect(rule).toContain('display: contents');
    expect(CSS).not.toMatch(/\.tools-maintenance-hero[^{]*\{[^}]*(max-height|overflow)/);
  });
});

describe('scroll targets still resolve', () => {
  it('keeps the section ids links and the dashboard jump to', () => {
    expect(HERO).toContain('id="repair-section-operations"');
    expect(HERO).toContain('id="repair-section-history"');
    expect(HERO).toContain('className="tools-maintenance-hero"');
  });
});
