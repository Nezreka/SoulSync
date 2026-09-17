import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * #1254: the Write Tags modal opened every changed track expanded and the
 * list grew past the viewport with the buttons underneath it. the body div
 * carried the id the vanilla used but not the CLASS the scroll rule is on,
 * so nothing scrolled. read from the source: the class is on the body, and
 * the stylesheet makes the modal a bounded flex column with the body as the
 * one part that scrolls. measured in chromium at 700px: footer at 648.
 */
const HERE = __dirname;
const BATCH = readFileSync(join(HERE, 'batch-tag-preview-modal.tsx'), 'utf8');
const SINGLE = readFileSync(join(HERE, 'tag-preview-modal.tsx'), 'utf8');
const CSS = readFileSync(join(HERE, '..', '..', '..', '..', 'static', 'style.css'), 'utf8');

function rule(selector: string): string {
  const re = new RegExp(selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\s*\\{([^}]*)\\}', 'g');
  return Array.from(CSS.matchAll(re), (m) => m[1]).join('\n');
}

describe('the write-tags modals scroll inside a bounded box', () => {
  it('the batch body carries the class the scroll rule is on', () => {
    expect(BATCH).toContain('className="batch-tag-preview-body"');
    expect(rule('.batch-tag-preview-body')).toMatch(/overflow-y:\s*auto/);
    expect(rule('.batch-tag-preview-body')).toMatch(/min-height:\s*0/);
    expect(rule('.batch-tag-preview-modal')).toMatch(/max-height:\s*85vh/);
    expect(rule('.batch-tag-preview-modal')).toMatch(/overflow:\s*hidden/);
  });

  it('both cards carry the base class that paints them', () => {
    // the port dropped enhanced-bulk-modal, so the card had no background and
    // rendered see-through over the page (the "very transparent" report)
    expect(BATCH).toContain('className="enhanced-bulk-modal batch-tag-preview-modal');
    expect(SINGLE).toContain('className="enhanced-bulk-modal tag-preview-modal');
    expect(rule('.enhanced-bulk-modal')).toMatch(/background:\s*linear-gradient/);
  });

  it('the single-track body does too', () => {
    expect(SINGLE).toContain('className="tag-preview-body"');
    expect(rule('.tag-preview-body')).toMatch(/overflow-y:\s*auto/);
    expect(rule('.tag-preview-modal')).toMatch(/max-height:\s*85vh/);
  });
});
