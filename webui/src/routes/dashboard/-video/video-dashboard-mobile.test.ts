/**
 * the video dashboard's phone fixes, asserted as text. jsdom can't lay
 * anything out, so these were measured in chromium at 360 and 390 and pinned
 * here, or a cleanup puts them back.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const read = (path: string) =>
  readFileSync(resolve(process.cwd(), path), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');

function mediaBlock(css: string, width: number): string {
  const m = new RegExp(`@media \\(max-width: ${width}px\\) \\{([\\s\\S]*?)\\n\\}`).exec(css);
  return m ? m[1] : '';
}

function rule(block: string, selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const m = new RegExp(`(?:^|[,{}\\n])\\s*${escaped}\\s*\\{([^}]*)\\}`).exec(block);
  return m ? m[1] : '';
}

describe('the next up billboard on a phone', () => {
  const phone = mediaBlock(read('src/routes/calendar/-ui/calendar.module.css'), 768);

  it('stacked panels size to their content', () => {
    // flex: 1 1 0 held each panel at its 150px floor and the bottom-anchored
    // text spilled out the top, so the first title was cut off
    expect(rule(phone, '.bbPanel:last-child')).toMatch(/flex:\s*0 0 auto/);
  });

  it('drops the left pad meant for the slanted edge', () => {
    expect(rule(phone, '.bbPanel .bbContent')).toMatch(/padding:\s*22px 20px 20px/);
  });
});

describe('the count tiles on a phone', () => {
  const css = read('src/routes/dashboard/-video/video-dashboard.module.css');
  const phone = mediaBlock(css, 460);

  it('sit side by side, beating the music rule that stacks them', () => {
    // the music rule is #dashboard-page .dash-tiles.header-quick-nav. a
    // selector with fewer classes loses to it and the tiles stay one per row
    expect(
      rule(phone, ':global(#dashboard-page) .tiles:global(.dash-tiles.header-quick-nav)'),
    ).toMatch(/grid-template-columns:\s*repeat\(auto-fit/);
  });

  it('let a label wrap instead of cutting it', () => {
    expect(
      rule(phone, ':global(#dashboard-page) .tiles :global(.dash-tile .hero-btn-label)'),
    ).toMatch(/white-space:\s*normal/);
  });

  it('keeps the studio button on one line', () => {
    expect(rule(css, '.studioCta')).toMatch(/white-space:\s*nowrap/);
  });
});
