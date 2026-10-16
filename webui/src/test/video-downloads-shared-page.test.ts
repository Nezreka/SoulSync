import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Downloads is a shared page: from the video side it opens in place on its
 * video tab, and the shell side never flips. #1651's hand-off flipped the
 * whole app to the music side, which stranded the sidebar side toggle —
 * the toggle returns to whichever video page is marked active, and the
 * hand-off had marked itself, so every Video click re-ran the hand-off and
 * bounced straight back to music with no way out (owner report, Oct 2026).
 * Behaviour verified end-to-end in the shell harness; these pin the lines
 * it depends on.
 */

const read = (p: string) => readFileSync(resolve(process.cwd(), p), 'utf8').replace(/\r\n/g, '\n');

const SIDE = read('static/video/video-side.js');
const SIDE_CSS = read('static/video/video-side.css');

/** The source between two markers, so a pin reads one function, not the file. */
function between(src: string, start: string, end: string): string {
  const a = src.indexOf(start);
  expect(a, `marker not found: ${start}`).toBeGreaterThan(-1);
  const b = src.indexOf(end, a);
  expect(b, `end marker not found: ${end}`).toBeGreaterThan(-1);
  return src.slice(a, b);
}

describe('video downloads is a shared page, not a side flip', () => {
  it('the hand-off still exists for sidebar and deep links', () => {
    const nav = between(SIDE, 'function navigate(', 'function applySide(');
    expect(nav).toContain(
      "if (pageId === 'video-downloads') { gotoUnifiedDownloads(mode); return; }",
    );
  });

  it('the hand-off routes to the unified page on its video tab', () => {
    const handoff = between(SIDE, 'function gotoUnifiedDownloads(', 'function navigate(');
    expect(handoff).toContain("setActiveNav('video-downloads')");
    expect(handoff).toContain("navigateToPage('active-downloads'");
    expect(handoff).toContain("query: { media: 'video' }");
  });

  it('the hand-off never flips the app side', () => {
    const handoff = between(SIDE, 'function gotoUnifiedDownloads(', 'function navigate(');
    expect(handoff).not.toContain('_switchAppSide(');
  });

  it('the hand-off sets the body page attribute that keys the reveal CSS', () => {
    // navigate() hands off before showPage() can set data-video-page, so the
    // hand-off sets it itself; without it the blanket video-side hide rule
    // keeps the React host display:none and the page renders invisibly.
    const handoff = between(SIDE, 'function gotoUnifiedDownloads(', 'function navigate(');
    expect(handoff).toContain("document.body.setAttribute('data-video-page', 'video-downloads')");
  });

  it('the video-side CSS reveals the React host for downloads and hides the video host', () => {
    expect(SIDE_CSS).toContain(
      'body[data-side="video"][data-video-page="video-downloads"] #webui-react-root',
    );
    expect(SIDE_CSS).toContain(
      'body[data-side="video"][data-video-page="video-downloads"] #video-page-host',
    );
  });

  it('the side toggle still returns to the active-marked video page', () => {
    const resume = between(SIDE, "if (side === 'video') {", '} else if (parseDetailPath(');
    expect(resume).toContain("document.querySelector('.video-nav .nav-button.active')");
    expect(resume).toContain('navigate(');
  });
});
