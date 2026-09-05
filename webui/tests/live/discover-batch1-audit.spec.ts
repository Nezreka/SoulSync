import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';

/**
 * Batch 1 live audit — the same surfaces the review captured, against a
 * running SoulSync.
 *
 * The offline probes in tests/layout prove the geometry; this proves the page
 * as a whole still works and captures the after-shots §12 asks for.
 *
 * Needs a server. Auth, if the install has any, comes from the environment:
 *
 *   SOULSYNC_PIN=1234            launch pin
 *   SOULSYNC_PROFILE_PIN=1234    per-profile pin (optional)
 *   SOULSYNC_USER / SOULSYNC_PASS  login mode
 *   SOULSYNC_PROFILE_ID=1        which profile to select (default 1)
 *
 *   npx playwright test tests/live --config=playwright.live.config.ts
 */

const SHOTS = resolve(process.cwd(), 'test-results/discover-batch1');
const PROFILE_ID = Number(process.env.SOULSYNC_PROFILE_ID || 1);

/** The dev server restarts on its own; give it a moment to come back. */
async function waitForServer(page: Page, baseURL: string) {
  const until = Date.now() + 90_000;
  for (;;) {
    try {
      const r = await page.request.get(new URL('/api/profiles', baseURL).toString(), {
        timeout: 5_000,
      });
      if (r.ok()) return;
    } catch {
      /* still down */
    }
    if (Date.now() > until) throw new Error('SoulSync did not come back on ' + baseURL);
    await page.waitForTimeout(2_000);
  }
}

async function authenticate(page: Page, baseURL: string) {
  await waitForServer(page, baseURL);
  if (process.env.SOULSYNC_PIN) {
    const r = await page.request.post(
      new URL('/api/profiles/verify-launch-pin', baseURL).toString(),
      {
        data: { pin: process.env.SOULSYNC_PIN },
      },
    );
    expect(r.ok(), 'launch pin rejected').toBe(true);
  }
  if (process.env.SOULSYNC_USER) {
    const r = await page.request.post(new URL('/api/auth/login', baseURL).toString(), {
      data: { username: process.env.SOULSYNC_USER, password: process.env.SOULSYNC_PASS },
    });
    expect(r.ok(), 'login rejected').toBe(true);
  }
  // Best effort. With more than one profile a per-profile PIN is required, but
  // the discover endpoints answer without a selected profile, so a missing PIN
  // is not a reason to skip the audit. Pass SOULSYNC_PROFILE_PIN to pin it to
  // one profile's data.
  const sel = await page.request.post(new URL('/api/profiles/select', baseURL).toString(), {
    data: { profile_id: PROFILE_ID, pin: process.env.SOULSYNC_PROFILE_PIN || '' },
  });
  if (!sel.ok() && process.env.SOULSYNC_PROFILE_PIN) {
    throw new Error(`profile select failed: ${sel.status()} ${await sel.text()}`);
  }
}

async function openMusic(page: Page, baseURL: string, w: number, h: number) {
  mkdirSync(SHOTS, { recursive: true });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width: w, height: h });
  await authenticate(page, baseURL);
  await page.goto('/discover');
  await page.waitForSelector('.discover-hero', { timeout: 30_000 });
  // The hero payload can take 20s on a cold server; wait for a real title.
  await expect
    .poll(() => page.locator('#discover-hero-title').textContent(), { timeout: 40_000 })
    .not.toBe('');
}

async function openVideo(page: Page, baseURL: string, w: number, h: number) {
  mkdirSync(SHOTS, { recursive: true });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width: w, height: h });
  await authenticate(page, baseURL);
  await page.goto('/video-discover');
  await page.waitForSelector('[data-vdsc-hero] .vdsc-hero-body', { timeout: 40_000 });
}

/** Every control rectangle on screen, for the collision check. */
async function rects(page: Page, selectors: string[]) {
  return page.evaluate((sels) => {
    const out: { sel: string; i: number; x: number; y: number; w: number; h: number }[] = [];
    for (const sel of sels) {
      document.querySelectorAll(sel).forEach((el, i) => {
        const r = el.getBoundingClientRect();
        if (r.width && r.height) out.push({ sel, i, x: r.x, y: r.y, w: r.width, h: r.height });
      });
    }
    return out;
  }, selectors);
}

type R = Awaited<ReturnType<typeof rects>>[number];
const hits = (a: R, b: R) =>
  a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

function noCollisions(found: R[]) {
  const bad: string[] = [];
  for (let i = 0; i < found.length; i++) {
    for (let j = i + 1; j < found.length; j++) {
      if (hits(found[i], found[j]))
        bad.push(`${found[i].sel}[${found[i].i}] over ${found[j].sel}[${found[j].i}]`);
    }
  }
  return bad;
}

const MUSIC_CONTROLS = [
  '#discover-hero-discography',
  '#discover-hero-add',
  '#discover-hero-watch-all',
  '#discover-hero-view-all',
  '.hero-indicator',
];

for (const [w, h] of [
  [1440, 900],
  [1024, 768],
  [390, 844],
] as const) {
  test(`M01 music hero has no overlapping controls at ${w}x${h}`, async ({ page, baseURL }) => {
    await openMusic(page, baseURL!, w, h);
    const bad = noCollisions(await rects(page, MUSIC_CONTROLS));
    await page.screenshot({ path: `${SHOTS}/music-${w}x${h}.png`, fullPage: false });
    expect(bad, bad.join('\n')).toEqual([]);
  });
}

test('M01 the first mobile screen offers a real music choice', async ({ page, baseURL }) => {
  await openMusic(page, baseURL!, 390, 844);
  const firstCardTop = await page.evaluate(() => {
    const card = document.querySelector('.discover-mix-card, .ya-card, .discover-card');
    return card ? card.getBoundingClientRect().top : Infinity;
  });
  await page.screenshot({ path: `${SHOTS}/music-mobile-fold.png` });
  // Recorded, not asserted: the opening recomposition is batch 3, so this is
  // the baseline the batch-3 work has to improve on.
  console.log(`first music card starts at y=${firstCardTop} on a 844px screen`);
});

test('M03 the mix dialog is a real dialog', async ({ page, baseURL }) => {
  await openMusic(page, baseURL!, 1440, 900);
  const opener = page.locator('.mix-card-open').first();
  await opener.waitFor({ timeout: 30_000 });
  await opener.focus();
  await opener.click();

  const dialog = page.locator('.mix-modal');
  await dialog.waitFor();
  await expect(dialog).toHaveAttribute('role', 'dialog');
  await expect(dialog).toHaveAttribute('aria-modal', 'true');
  // Focus went inside.
  expect(
    await page.evaluate(() =>
      document.querySelector('.mix-modal')!.contains(document.activeElement),
    ),
  ).toBe(true);
  expect(await page.evaluate(() => document.body.style.overflow)).toBe('hidden');
  await page.screenshot({ path: `${SHOTS}/music-mix-modal.png` });

  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  // And back to the card that opened it.
  expect(await page.evaluate(() => document.activeElement?.className || '')).toContain(
    'mix-card-open',
  );
});

test('M02 the row play button is wired to something', async ({ page, baseURL }) => {
  await openMusic(page, baseURL!, 1440, 900);
  await page.locator('.mix-card-open').first().click();
  const row = page.locator('.track-compact-play').first();
  await row.waitFor({ timeout: 20_000 });
  await expect(row).toHaveAttribute('aria-label', /^Play /);

  const calls: string[] = [];
  page.on('request', (r) => {
    if (r.url().includes('/api/discover/resolve-playable')) calls.push(r.url());
  });
  await row.click();
  await expect.poll(() => calls.length, { timeout: 10_000 }).toBeGreaterThan(0);
});

test('M05 the taste dial is a real slider', async ({ page, baseURL }) => {
  await openMusic(page, baseURL!, 1440, 900);
  const input = page.locator('.adv-wave-input');
  await input.scrollIntoViewIfNeeded();
  await expect(input).toHaveAttribute('type', 'range');
  const before = await input.inputValue();
  await input.focus();
  await page.keyboard.press('ArrowRight');
  await page.keyboard.press('ArrowRight');
  expect(await input.inputValue()).not.toBe(before);
  await page.screenshot({ path: `${SHOTS}/music-dial.png` });
});

const VIDEO_CONTROLS = ['.vdsc-hero-cta', '.vdsc-hero-trailer', '.vdsc-hero-add', '.vdsc-dot'];

for (const [w, h] of [
  [1440, 900],
  [390, 844],
] as const) {
  test(`V01 video hero has no overlapping controls at ${w}x${h}`, async ({ page, baseURL }) => {
    await openVideo(page, baseURL!, w, h);
    const bad = noCollisions(await rects(page, VIDEO_CONTROLS));
    await page.screenshot({ path: `${SHOTS}/video-${w}x${h}.png` });
    expect(bad, bad.join('\n')).toEqual([]);
  });
}

test('V16 an arrow key on a poster does not move the hero', async ({ page, baseURL }) => {
  await openVideo(page, baseURL!, 1440, 900);
  const eyebrow = () => page.locator('.vdsc-hero-eyebrow').textContent();
  const before = await eyebrow();
  const poster = page.locator('.vdsc-shelf .vsr-card').first();
  await poster.waitFor({ timeout: 30_000 });
  await poster.focus();
  await page.keyboard.press('ArrowRight');
  await page.waitForTimeout(300);
  expect(await eyebrow()).toBe(before);
});

test('S01 resizing a desktop window down does not leave the drawer over the page', async ({
  page,
  baseURL,
}) => {
  await openMusic(page, baseURL!, 1440, 900);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(600);
  const geometry = await page.evaluate(() => {
    const sb = document.querySelector('.sidebar')!.getBoundingClientRect();
    const opener = document.getElementById('hamburger-btn')!.getBoundingClientRect();
    return { sidebarRight: sb.right, openerW: opener.width, openerH: opener.height };
  });
  await page.screenshot({ path: `${SHOTS}/music-resized-mobile.png` });
  // Off-screen, not merely narrow.
  expect(geometry.sidebarRight).toBeLessThanOrEqual(0);
  // And the way back in is visible.
  expect(geometry.openerW).toBeGreaterThan(0);
  expect(geometry.openerH).toBeGreaterThan(0);
});

test('S01 the drawer opens, closes on Escape, and gives focus back', async ({ page, baseURL }) => {
  await openMusic(page, baseURL!, 390, 844);
  const opener = page.locator('#hamburger-btn');
  await expect(opener).toBeVisible();
  await opener.click();
  await expect(opener).toHaveAttribute('aria-expanded', 'true');
  // Poll: the drawer slides in over 0.3s, so an immediate read catches it
  // part-way (-62px). And it lands via a transform, so "0" is 0 give or take
  // float residue (-4e-8 in Chromium).
  await expect
    .poll(
      () => page.evaluate(() => document.querySelector('.sidebar')!.getBoundingClientRect().left),
      { timeout: 5_000 },
    )
    .toBeGreaterThan(-0.5);
  await page.keyboard.press('Escape');
  await expect(opener).toHaveAttribute('aria-expanded', 'false');
  expect(await page.evaluate(() => document.activeElement?.id)).toBe('hamburger-btn');
});
