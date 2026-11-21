/**
 * One release notes file, two readers: What's new and the version modal.
 * These run the real helper.js and the real showVersionInfo from
 * downloads.js against it, so a reader that drifts back to its own copy
 * fails here.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { extractFunction } from '@/test/vanilla-extract';

import './pages/register';
import { registeredPageIds } from './pages';
import {
  RELEASES,
  RELEASE_NOTES_READY,
  type ReleaseNotesBridge,
  compareVersions,
  currentRelease,
  installReleaseNotes,
  modalSections,
  releasedNotes,
  type Release,
} from './release-notes';
import { ANCHORS } from './tours/anchors';

const DOWNLOADS = readFileSync(resolve(process.cwd(), 'static/downloads.js'), 'utf8');

afterEach(() => {
  document.body.innerHTML = '';
  localStorage.removeItem('soulsync_helper_version_seen');
  installReleaseNotes();
});

const release = RELEASES[0];

function allCopy(r: Release): string[] {
  return [
    r.headline,
    r.summary,
    ...r.features,
    r.usage_note ?? '',
    ...r.items.flatMap((i) => [i.title, i.desc]),
    r.earlier?.desc ?? '',
    r.earlier?.summary ?? '',
  ];
}

describe('the notes themselves', () => {
  it('keep the current release only, with the ones before it in a line (the What’s New convention)', () => {
    expect(RELEASES).toHaveLength(1);
    expect(release.items.length).toBeGreaterThan(3);
    expect(release.earlier?.desc).toBeTruthy();
    expect(release.earlier?.version).toMatch(/^\d+\.\d+\.\d+$/);
    expect(compareVersions(release.earlier!.version, release.version)).toBeLessThan(0);
  });

  it('have no em or en dashes anywhere', () => {
    for (const r of RELEASES) {
      for (const text of allCopy(r)) expect(text, text).not.toMatch(/[–—]/);
    }
  });

  it('only send Show me to pages that exist and anchors a tour test checks', () => {
    const pages = new Set(registeredPageIds());
    for (const item of release.items) {
      if (item.page) expect(pages.has(item.page), item.page).toBe(true);
      if (item.anchor) expect(item.anchor in ANCHORS, item.anchor).toBe(true);
    }
  });

  it('hide a release still above the running build', () => {
    RELEASES.push({ ...release, version: '99.0.0', items: [] });
    try {
      expect(currentRelease('3.6.9')?.version).toBe('3.6.9');
      expect(releasedNotes('3.6.9').map((r) => r.version)).not.toContain('99.0.0');
      expect(currentRelease('99.0.0')?.version).toBe('99.0.0');
    } finally {
      RELEASES.pop();
    }
  });

  it('compare versions by number, not by string', () => {
    expect(compareVersions('3.6.10', '3.6.9')).toBeGreaterThan(0);
    expect(compareVersions('v3.6.9', '3.6.9+abc')).toBe(0);
    expect(compareVersions('2.4', '2.4.0')).toBe(0);
    expect(compareVersions(null, '0.0.1')).toBeLessThan(0);
  });
});

describe('the version modal’s sections', () => {
  it('lead with the release in the modal’s style, then one Earlier in section', () => {
    const sections = modalSections('3.6.9');
    expect(sections).toHaveLength(2);
    expect(sections[0].title).toBe('3.6.9: the player, settings rebuilt and music apps');
    expect(sections[0].description).toBe(release.summary);
    expect(sections[0].features).toEqual(release.features);
    expect(sections[1]).toEqual({
      title: 'Earlier in 3.6.8',
      description: release.earlier!.summary,
      features: [],
    });
  });

  it('showVersionInfo builds the modal from window.SoulSyncReleaseNotes and marks the version seen', async () => {
    document.body.innerHTML = '<button class="version-button">v3.6.9</button>';
    installReleaseNotes();
    const populate = vi.fn();
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    const fn = new Function(
      'populateVersionModal',
      'showToast',
      `${extractFunction('showVersionInfo', DOWNLOADS)}\nreturn showVersionInfo;`,
    )(populate, vi.fn()) as () => Promise<void>;
    await fn();
    expect(populate).toHaveBeenCalledTimes(1);
    const [data] = populate.mock.calls[0];
    expect(data.version).toBe('3.6.9');
    expect(data.sections).toEqual(modalSections('3.6.9'));
    expect(data.subtitle).not.toMatch(/[–—]/);
    expect(localStorage.getItem('soulsync_helper_version_seen')).toBe('3.6.9');
  });

  it('showVersionInfo has no notes of its own to fall back on', () => {
    const body = extractFunction('showVersionInfo', DOWNLOADS);
    expect(body).toContain('window.SoulSyncReleaseNotes');
    expect(body).not.toContain('VERSION_MODAL_SECTIONS');
  });
});

describe('the window bridge', () => {
  it('installReleaseNotes publishes the notes and says so', () => {
    delete window.SoulSyncReleaseNotes;
    document.body.innerHTML = '<button class="version-button">v3.6.9</button>';
    const ready = vi.fn();
    window.addEventListener(RELEASE_NOTES_READY, ready, { once: true });
    installReleaseNotes();
    expect(ready).toHaveBeenCalledTimes(1);
    // typed again: tsc narrows the property to never after the delete
    const bridge = window.SoulSyncReleaseNotes as ReleaseNotesBridge | undefined;
    expect(bridge?.latestVersion()).toBe('3.6.9');
    expect(bridge?.modalSections()).toEqual(modalSections('3.6.9'));
  });

  it('importing the module publishes nothing: mountHelpHost installs it', () => {
    const src = readFileSync(resolve(process.cwd(), 'src/features/help/release-notes.ts'), 'utf8');
    expect(src).not.toMatch(/^installReleaseNotes\(\);/m);
    const host = readFileSync(resolve(process.cwd(), 'src/features/help/host.tsx'), 'utf8');
    const mount = host.slice(host.indexOf('export function mountHelpHost'));
    expect(mount).toContain('installReleaseNotes();');
    expect(mount).toContain('installTours();');
  });
});
