import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import statsStyles from './stats-page.module.css';
import storyStyles from './year-story.module.css';

describe('stats page responsiveness', () => {
  const statsCss = readFileSync(resolve(import.meta.dirname, 'stats-page.module.css'), 'utf-8');
  const storyCss = readFileSync(resolve(import.meta.dirname, 'year-story.module.css'), 'utf-8');

  it('declares responsive breakpoints for desktop, tablet, mobile, and small screens', () => {
    // Desktop / Tablet landscape
    expect(statsCss).toContain('@media (max-width: 1200px)');
    // Tablet portrait
    expect(statsCss).toContain('@media (max-width: 860px)');
    // Mobile phones
    expect(statsCss).toContain('@media (max-width: 580px)');
    // Small screen phones (e.g. 320px-375px)
    expect(statsCss).toContain('@media (max-width: 380px)');
  });

  it('has no dead or mismatched selectors in stats-page responsive media queries', () => {
    // Extract selector class names from inside the responsive block
    const responsiveBlockIndex = statsCss.indexOf('Responsive Design');
    expect(responsiveBlockIndex).toBeGreaterThan(0);

    const responsiveCss = statsCss.slice(responsiveBlockIndex);
    // Find all class selectors (e.g. .statsContainer, .headerPrimaryActions)
    const matches = Array.from(responsiveCss.matchAll(/\.([a-zA-Z0-9_-]+)\s*[{,:]/g));
    const selectorClassNames = [...new Set(matches.map((m) => m[1]))];

    expect(selectorClassNames.length).toBeGreaterThan(15);

    // Every selector in responsive media queries must exist in the exported CSS module
    for (const className of selectorClassNames) {
      expect(
        className in statsStyles,
        `Selector .${className} in responsive media query should exist in stats-page.module.css exports`,
      ).toBe(true);
    }
  });

  it('stacks the paired cards and the hero as the page narrows', () => {
    expect(statsCss).toMatch(
      /@media \(max-width: 1200px\)[\s\S]*?\.two\s*\{[^}]*grid-template-columns:\s*1fr;/,
    );
    expect(statsCss).toMatch(
      /@media \(max-width: 860px\)[\s\S]*?\.hero\s*\{[^}]*grid-template-columns:\s*1fr;/,
    );
    expect(statsCss).toMatch(
      /@media \(max-width: 860px\)[\s\S]*?\.figures\s*\{[^}]*grid-template-columns:\s*repeat\(2,\s*1fr\);/,
    );
  });

  it('fits the sync line, the heatmap and the lists to a phone', () => {
    expect(statsCss).toMatch(/@media \(max-width: 580px\)[\s\S]*?\.sync\s*\{[^}]*width:\s*100%;/);
    // Listening clock heatmap rows keep their 24 hour columns
    expect(statsCss).toMatch(
      /@media \(max-width: 580px\)[\s\S]*?\.clockRow\s*\{[^}]*grid-template-columns:\s*24px\s+repeat\(24,\s*1fr\);/,
    );
    // clearing the shell's menu button is the page frame's job now (the panel
    // starts under it), so the page must not bring a padding of its own back
    expect(statsCss).not.toMatch(/\.page\s*\{[^}]*padding:/);
  });

  it('keeps a shelf to one row until it is opened', () => {
    expect(statsCss).toMatch(/\.shelf\s*\{[^}]*grid-auto-rows:\s*0;[^}]*overflow:\s*hidden;/);
    expect(statsCss).toMatch(/\.shelfAll\s*\{[^}]*grid-auto-rows:\s*auto;/);
    expect(statsStyles.shelf).toBeDefined();
  });

  it('declares responsive breakpoints and safe navigation for the Year Story', () => {
    // Tablet breakpoint
    expect(storyCss).toContain('@media (max-width: 760px)');
    // Mobile breakpoint
    expect(storyCss).toContain('@media (max-width: 640px)');
    // Small screen breakpoint
    expect(storyCss).toContain('@media (max-width: 420px)');

    // Ensure progress pips have right padding so they never overlap the close button on mobile
    expect(storyCss).toMatch(
      /@media \(max-width: 640px\)[\s\S]*?\.pips\s*\{[^}]*padding:\s*14px\s+60px\s+0\s+16px;/,
    );

    // Month columns can flex-shrink cleanly on narrow viewports
    expect(storyCss).toMatch(/\.monthColumn\s*\{[\s\S]*?min-width:\s*0;/);

    // Collage scales and reduces gap on mobile to prevent clipping
    expect(storyCss).toMatch(/@media \(max-width: 640px\)[\s\S]*?\.collage\s*\{[^}]*gap:\s*8px;/);
    expect(storyCss).toMatch(/@media \(max-width: 420px\)[\s\S]*?\.collage\s*\{[^}]*gap:\s*5px;/);
  });

  it('has no dead or mismatched selectors in year-story responsive media queries', () => {
    const responsiveMatches = Array.from(
      storyCss.matchAll(/@media\s*\(max-width:[^)]+\)\s*\{([\s\S]*?\n\})/g),
    );
    expect(responsiveMatches.length).toBeGreaterThanOrEqual(3);

    for (const match of responsiveMatches) {
      const block = match[1];
      const selectors = Array.from(block.matchAll(/\.([a-zA-Z0-9_-]+)\s*[{,:]/g)).map((m) => m[1]);
      for (const className of selectors) {
        expect(
          className in storyStyles,
          `Selector .${className} in year-story media query should exist in year-story.module.css exports`,
        ).toBe(true);
      }
    }
  });
});
