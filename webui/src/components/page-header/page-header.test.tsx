import { fireEvent, render, screen } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it, vi } from 'vitest';

import { PageBar, PageFrame, PageHeader, PagePills, PageSearch, PageSection } from './index';

/**
 * The page frame every page but the dashboard is drawn in. The look lives in
 * static/style.css under `.pgf-*`; these components only emit the classes, so
 * the tests pin the classes and that each one has a rule.
 */
const CSS = readFileSync(resolve(process.cwd(), 'static/style.css'), 'utf8');

describe('the page frame', () => {
  it('heads a page with its name, where things stand, and what you can do', () => {
    render(
      <PageFrame id="p" data-testid="frame">
        <PageHeader
          title="Downloads"
          subtitle={
            <>
              <b>5</b> downloading
            </>
          }
          actions={<button type="button">Add</button>}
        />
      </PageFrame>,
    );
    expect(screen.getByTestId('frame').className).toBe('pgf-page');
    const title = screen.getByRole('heading', { level: 1, name: 'Downloads' });
    expect(title.className).toBe('pgf-title');
    expect(document.querySelector('.pgf-status')!.textContent).toBe('5 downloading');
    expect(document.querySelector('.pgf-actions button')!.textContent).toBe('Add');
  });

  it('draws no icon, whatever an older caller still passes', () => {
    const { container } = render(<PageHeader title="Import" icon={<img src="/x.png" alt="" />} />);
    expect(container.querySelector('img')).toBeNull();
    // and no empty line or empty action row when there is nothing to say
    expect(container.querySelector('.pgf-status')).toBeNull();
    expect(container.querySelector('.pgf-actions')).toBeNull();
  });

  it('shows the views as tabs with their counts, and says which is open', () => {
    const onChange = vi.fn();
    render(
      <PagePills
        ariaLabel="Downloads views"
        value="active"
        onChange={onChange}
        pills={[
          { id: 'active', label: 'Active', count: 2914 },
          { id: 'failed', label: 'Failed', count: 0 },
          { id: 'clients', label: 'Clients' },
        ]}
      />,
    );
    const tabs = screen.getAllByRole('tab');
    expect(tabs.map((t) => t.textContent)).toEqual(['Active2,914', 'Failed0', 'Clients']);
    expect(tabs.map((t) => t.getAttribute('aria-selected'))).toEqual(['true', 'false', 'false']);
    fireEvent.click(tabs[2]);
    expect(onChange).toHaveBeenCalledWith('clients');
  });

  it('as filters the pills are pressed, not selected', () => {
    render(
      <PagePills
        kind="filters"
        ariaLabel="Status"
        value="all"
        onChange={() => undefined}
        pills={[
          { id: 'all', label: 'All' },
          { id: 'new', label: 'New' },
        ]}
      />,
    );
    expect(screen.queryByRole('tab')).toBeNull();
    expect(screen.getByRole('button', { name: 'All' }).getAttribute('aria-pressed')).toBe('true');
  });

  it('puts search at the end of the pill row, and names a section with its note', () => {
    const onChange = vi.fn();
    const { container } = render(
      <>
        <PageBar end={<PageSearch value="" placeholder="Search downloads" onChange={onChange} />}>
          <span>views</span>
        </PageBar>
        <PageSection title="Ready" note="2 albums">
          <p>rows</p>
        </PageSection>
      </>,
    );
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search downloads' }), {
      target: { value: 'tame' },
    });
    expect(onChange).toHaveBeenCalledWith('tame');
    expect(container.querySelector('.pgf-bar > .pgf-bar-end .pgf-search')).not.toBeNull();
    expect(screen.getByRole('heading', { level: 2, name: 'Ready' }).className).toBe(
      'pgf-section-title',
    );
    expect(container.querySelector('.pgf-section-note')!.textContent).toBe('2 albums');
  });

  it('has a rule for every class it emits, at the numbers that were agreed', () => {
    for (const cls of [
      '.pgf-page',
      '.pgf-head',
      '.pgf-head-main',
      '.pgf-title',
      '.pgf-status',
      '.pgf-actions',
      '.pgf-bar',
      '.pgf-bar-end',
      '.pgf-pills',
      '.pgf-pill',
      '.pgf-pill-count',
      '.pgf-search',
      '.pgf-section',
      '.pgf-section-head',
      '.pgf-section-title',
      '.pgf-section-note',
    ]) {
      expect(CSS, `${cls} is emitted but never styled`).toContain(`${cls} {`);
    }
    const page = CSS.slice(CSS.indexOf('.pgf-page {'), CSS.indexOf('.pgf-head {'));
    expect(page).toContain('max-width: 1440px');
    const title = CSS.slice(CSS.indexOf('.pgf-title {'), CSS.indexOf('.pgf-status {'));
    expect(title).toContain('font-size: 28px');
    expect(title).toContain('font-weight: 600');
  });
});
