/** a docs article read in the sheet: the real content, links, the way out to Help & Docs */

import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { ArticleView } from './article';
import { loadDocs } from './search/docs';
import { installRealDocs } from './search/docs.test-utils';
import { Markdown } from './search/markdown';
import { useHelpStore } from './store';

beforeAll(async () => {
  installRealDocs({ readAs: 'admin' });
  await loadDocs();
});

beforeEach(() => useHelpStore.setState(useHelpStore.getInitialState(), true));
afterEach(() => {
  delete window.navigateToDocsSection;
  vi.restoreAllMocks();
});

const view = (id: string) =>
  render(
    <ArticleView entry={{ view: 'article', params: { id } }} query="" isAdmin page="dashboard" />,
  );

describe('reading an article', () => {
  it('shows the section, title, lede and body of the real docs page', () => {
    view('gs-docker');
    expect(
      screen.getByRole('heading', { level: 3, name: 'Docker & Deployment' }),
    ).toBeInTheDocument();
    expect(screen.getByText('Getting Started')).toBeInTheDocument();
    expect(screen.getAllByRole('heading', { level: 4 }).length).toBeGreaterThan(1);
  });

  it('a section id opens its first page', () => {
    view('getting-started');
    expect(screen.getByRole('heading', { level: 3, name: 'Overview' })).toBeInTheDocument();
  });

  it('"Open in Help & Docs" goes to the full page', () => {
    const go = vi.fn();
    window.navigateToDocsSection = go;
    view('gs-docker');
    fireEvent.click(screen.getByRole('button', { name: 'Open in Help & Docs' }));
    expect(go).toHaveBeenCalledWith('gs-docker');
  });

  it('a link to another article opens it in the sheet, on top', () => {
    view('gs-overview');
    const link = screen
      .getAllByRole('link')
      .find((a) => a.getAttribute('href') === '#docs/gs-first-setup')!;
    fireEvent.click(link);
    expect(useHelpStore.getState().stack.at(-1)).toEqual({
      view: 'article',
      params: { id: 'gs-first-setup' },
    });
  });

  it('an id that is gone says so and offers Help & Docs', () => {
    view('no-such-article');
    expect(screen.getByText("That guide isn't here")).toBeInTheDocument();
  });
});

describe('the markdown', () => {
  const md = (source: string) => render(<Markdown source={source} onArticle={vi.fn()} />);

  it('renders callouts with a word, not colour alone, and never raw html', () => {
    md('> [!WARNING] Back up first\n\n<img src=x onerror=alert(1)> **bold** `code`');
    expect(screen.getByText('Warning')).toBeInTheDocument();
    expect(document.querySelector('img[src="x"]')).toBeNull();
    expect(screen.getByText('bold').tagName).toBe('STRONG');
    expect(screen.getByText('code').tagName).toBe('CODE');
  });

  it('renders steps, cards, tables and code', () => {
    md(
      '::: steps\n1. One\n2. Two\n:::\n\n::: cards\n### 🎵 Music\nPlays it\n:::\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n```bash\necho hi\n```',
    );
    expect(screen.getAllByRole('listitem').map((li) => li.textContent)).toEqual(
      expect.arrayContaining(['One', 'Two', 'MusicPlays it']),
    );
    expect(screen.getByRole('table')).toBeInTheDocument();
    expect(screen.getByText('echo hi')).toBeInTheDocument();
  });

  it('keeps unsafe link targets as plain text', () => {
    md('[click](javascript:alert(1)) and [site](https://example.com)');
    expect(screen.getAllByRole('link')).toHaveLength(1);
    expect(screen.getByRole('link')).toHaveAttribute('rel', 'noopener noreferrer');
  });
});
