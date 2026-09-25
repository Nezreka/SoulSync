import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Overlay Studio can share its work now.
 *
 * Collection Studio has had Export/Import since it shipped; Overlay Studio never
 * did, which is backwards — a template is the artifact people actually share
 * (Kometa's whole ecosystem is shared configs), and until now an hour of design
 * work could not be backed up or handed to anyone.
 *
 * Three places have to agree for a feature like this to exist: the button, the
 * handler, and the wiring between them. A pair present in only two of the three
 * looks finished until you click it.
 */

const JS = readFileSync(
  resolve(process.cwd(), 'static/video/video-overlay-editor.js'),
  'utf8',
);
const COLLECTIONS = readFileSync(
  resolve(process.cwd(), 'static/video/video-collection-editor.js'),
  'utf8',
);

describe('the studio offers both halves', () => {
  it('puts them in the top bar, beside Apply', () => {
    // Not buried in a per-card menu: this acts on the whole library of designs.
    const topbar = JS.slice(JS.indexOf("'<div class=\"voe-topbar\">'"), JS.indexOf('voe-gallery-head'));
    expect(topbar).toContain('data-voe-export');
    expect(topbar).toContain('data-voe-import');
  });

  it('wires each button to its handler', () => {
    expect(JS).toContain("overlay.querySelector('[data-voe-export]').addEventListener('click', exportTemplates)");
    expect(JS).toContain("overlay.querySelector('[data-voe-import]').addEventListener('click', importTemplates)");
  });

  it('names the file so it is recognisable a year later', () => {
    expect(JS).toContain("a.download = 'soulsync-overlay-templates.json'");
  });

  it('releases the object url instead of leaking it', () => {
    expect(JS).toContain('URL.revokeObjectURL');
  });
});

describe('import feedback', () => {
  it('says how many were skipped, not just how many landed', () => {
    // An import that skips everything is the SAFETY working. Silence would read
    // as a failure and send someone hunting for a bug.
    expect(JS).toContain("' · ' + sk + ' already existed'");
    expect(JS).toContain("'Already had all ' + sk + ' of those'");
  });

  it('distinguishes a bad file from a failed request', () => {
    expect(JS).toContain('That file is not valid JSON');
    expect(JS).toContain('No templates in that file');
    expect(JS).toContain("toast((d && d.error) || 'Import failed', 'error')");
  });

  it('refreshes the gallery so the new cards appear without a reload', () => {
    const imp = JS.slice(JS.indexOf('function importTemplates'), JS.indexOf('function openStarterPicker'));
    expect(imp).toContain('loadGallery()');
  });

  it('accepts a bare array as well as the wrapped shape', () => {
    // Someone hand-editing a shared file should not be defeated by punctuation.
    expect(JS).toContain('data.templates || (Array.isArray(data) ? data : null)');
  });
});

describe('it matches the studio that already had this', () => {
  it('uses the same download-a-blob approach as Collection Studio', () => {
    for (const marker of ['new Blob(', 'URL.createObjectURL', "inp.accept = '.json,application/json'"]) {
      expect(JS, `overlay studio is missing ${marker}`).toContain(marker);
      expect(COLLECTIONS, `collection studio is missing ${marker}`).toContain(marker);
    }
  });
});
