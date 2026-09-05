import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Tools and Operations as two tabs, not one page carrying both.
 *
 * Operations used to scroll inside its own capped box (max-height + overflow
 * auto) while the tool cards sat below it on the page. That gave the page two
 * scrollbars doing different jobs, and neither one showed you everything.
 * Boulder: "i dont like how you scroll the operations in their own container
 * while the tools exist outside of that on the page ... instead of all that we
 * just have tabs. tools and operations. defaults to operatoins".
 *
 * The cap existed for a reason worth recording: without it, a large maintenance
 * surface pushed the tool cards far down the page. Splitting them into tabs
 * removes the reason rather than working around it, which is why the cap comes
 * out in the same change.
 *
 * These read the source rather than rendering: ToolsPage mounts the whole
 * maintenance surface, which fans out into a dozen polling hooks. The contract
 * that matters here is structural.
 */

const PAGE = readFileSync(
  resolve(process.cwd(), 'src/routes/tools/-ui/tools-page.tsx'),
  'utf8',
);
const HERO = readFileSync(
  resolve(process.cwd(), 'src/routes/tools/-ui/maintenance-hero.tsx'),
  'utf8',
);
const CSS = readFileSync(resolve(process.cwd(), 'static/style.css'), 'utf8');

describe('the tab switch', () => {
  it('opens on Operations, because that is the half with work waiting in it', () => {
    expect(PAGE).toContain("useState<'operations' | 'tools'>('operations')");
  });

  it('offers exactly the two tabs asked for, Operations first', () => {
    // sliced from the tablist div to the first panel. 'tabpanel' now also
    // appears earlier in the reveal hook, so indexOf on it alone inverts the
    // range and silently compares an empty string.
    const start = PAGE.indexOf('className="tools-tabs"');
    const end = PAGE.indexOf('id="tools-panel-operations"');
    expect(start).toBeGreaterThan(-1);
    expect(end).toBeGreaterThan(start);
    const block = PAGE.slice(start, end);
    expect(block.indexOf("'operations', 'Operations'")).toBeGreaterThan(-1);
    expect(block.indexOf("'tools', 'Tools'")).toBeGreaterThan(-1);
    expect(block.indexOf("'operations', 'Operations'")).toBeLessThan(
      block.indexOf("'tools', 'Tools'"),
    );
  });

  it('is a real tablist, not a row of buttons', () => {
    expect(PAGE).toContain('role="tablist"');
    expect(PAGE).toContain('role="tab"');
    expect(PAGE).toContain('role="tabpanel"');
    expect(PAGE).toContain('aria-selected={tab === value}');
    expect(PAGE).toContain('aria-controls={`tools-panel-${value}`}');
  });

  it('hides the inactive panel instead of unmounting it', () => {
    // The maintenance surface polls job progress and holds the findings filter
    // and page you were on. Unmounting on every tab flip would restart the
    // polling and throw that away, so both panels stay mounted.
    expect(PAGE).toContain("hidden={tab !== 'operations'}");
    expect(PAGE).toContain("hidden={tab !== 'tools'}");
    expect(PAGE).not.toMatch(/tab === 'operations' \? <MaintenanceHero/);
  });

  it('puts the maintenance surface on one tab and the cards on the other', () => {
    const ops = PAGE.indexOf('id="tools-panel-operations"');
    const tools = PAGE.indexOf('id="tools-panel-tools"');
    expect(ops).toBeGreaterThan(-1);
    expect(tools).toBeGreaterThan(ops);
    // the hero belongs to Operations
    expect(PAGE.indexOf('<MaintenanceHero />')).toBeGreaterThan(ops);
    expect(PAGE.indexOf('<MaintenanceHero />')).toBeLessThan(tools);
    // and every tool card section belongs to Tools
    expect(PAGE.indexOf('<ToolsSection')).toBeGreaterThan(tools);
  });
});

describe('the inner scroll box is gone', () => {
  it('no longer caps the maintenance surface or gives it its own scrollbar', () => {
    const rule = CSS.slice(
      CSS.indexOf('.tools-maintenance-hero {', CSS.indexOf('maintenance surface')),
    ).slice(0, 400);
    expect(rule).toContain('overflow: visible');
    expect(rule).not.toContain('max-height');
    expect(rule).not.toContain('overflow: hidden auto');
  });

  it('rounds the accent line now that nothing clips it', () => {
    // the hero's own overflow:hidden used to crop the 3px top line to the
    // rounded corners; without it the line juts past them
    // anchored to the section comment: 'overflow: visible' appears all over
    // this stylesheet, and indexOf on it lands on an unrelated rule
    const section = CSS.indexOf('The maintenance surface: one scroll');
    expect(section).toBeGreaterThan(-1);
    const idx = CSS.indexOf('.tools-maintenance-hero::before {', section);
    expect(idx).toBeGreaterThan(-1);
    expect(CSS.slice(idx, idx + 200)).toContain('border-radius: 16px 16px 0 0');
  });
});

describe('the two navs do not share a name', () => {
  it('calls the inner section Jobs, since the page tab is already Operations', () => {
    // Two controls called "Operations" on one screen is a guessing game. The
    // section's own heading is "Maintenance jobs", so Jobs is its real name.
    expect(HERO).toContain("label: 'Jobs'");
    expect(HERO).not.toContain("label: 'Operations'");
  });

  it('keeps the anchor and id, so deep links and scroll targets still resolve', () => {
    expect(HERO).toContain("anchor: 'repair-section-operations'");
    expect(HERO).toContain('id="repair-section-operations"');
  });
});
