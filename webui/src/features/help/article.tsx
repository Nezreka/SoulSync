/**
 * A docs article read inside the sheet (plan 4.2): its section, title and
 * lede, then the body, the same words the Help & Docs page shows (they come
 * from the same docs-content files, search/docs.ts). "Open in Help & Docs"
 * goes to the full page, with its contents list and screenshots at size.
 *
 * entry.params.id is the docs page id (a section id opens its first page,
 * as docs.js resolves it). A link to another article opens it here too, on
 * top, so Back returns to this one.
 */

import { useEffect, useState } from 'react';

import type { ViewProps } from './view-props';

import s from './article.module.css';
import { openArticleInDocs, openDocsHome } from './links';
import {
  findArticle,
  hiddenFromViewer,
  loadDocs,
  loadedDocs,
  type DocsArticle,
} from './search/docs';
import { Markdown } from './search/markdown';
import { useHelpStore } from './store';
import { Button, Placeholder } from './ui';

type State =
  | { status: 'loading' }
  | { status: 'ready'; article: DocsArticle }
  | { status: 'missing' }
  | { status: 'hidden' };

function lookup(id: string): State {
  // a guide that isn't for this profile is never shown (#2174), even from a
  // link someone saved
  if (hiddenFromViewer(id)) return { status: 'hidden' };
  const article = findArticle(id);
  if (article) return { status: 'ready', article };
  return loadedDocs()?.length ? { status: 'missing' } : { status: 'loading' };
}

export function ArticleView({ entry }: ViewProps) {
  const id = entry.params?.id ?? '';
  const [state, setState] = useState<State>(() => lookup(id));

  useEffect(() => {
    let live = true;
    setState(lookup(id));
    void loadDocs().then(() => {
      if (!live) return;
      const next = lookup(id);
      // still nothing after the read: the docs could not be fetched
      setState(next.status === 'loading' ? { status: 'missing' } : next);
    });
    return () => {
      live = false;
    };
  }, [id]);

  const openHere = (target: string) =>
    useHelpStore.getState().push({ view: 'article', params: { id: target } });

  if (state.status === 'loading') {
    return (
      <div className={s.loading} role="status" aria-live="polite">
        <span className={s.bar} />
        <span className={s.bar} />
        <span className={s.bar} data-short />
        <span className={s.srOnly}>Opening the guide</span>
      </div>
    );
  }

  if (state.status === 'hidden') {
    return (
      <Placeholder
        title="That guide is for your admin"
        text="It covers a part of SoulSync your profile doesn't use. Help & Docs has every guide that is yours."
      >
        <Button kind="primary" icon="book" onClick={openDocsHome}>
          Open Help & Docs
        </Button>
      </Placeholder>
    );
  }

  if (state.status === 'missing') {
    return (
      <Placeholder
        title="That guide isn't here"
        text="It may have moved. Help & Docs has every guide, with search."
      >
        <Button kind="primary" icon="book" onClick={openDocsHome}>
          Open Help & Docs
        </Button>
      </Placeholder>
    );
  }

  const { article } = state;
  return (
    <article className={s.article} aria-labelledby={`help-article-${article.id}`}>
      <header className={s.head}>
        {article.section ? <p className={s.eyebrow}>{article.section}</p> : null}
        <h3 id={`help-article-${article.id}`} className={s.title}>
          {article.title}
        </h3>
        {article.lede ? <p className={s.lede}>{article.lede}</p> : null}
        <div className={s.open}>
          <Button icon="book" onClick={() => openArticleInDocs(article.id)}>
            Open in Help & Docs
          </Button>
        </div>
      </header>
      <div className={s.body}>
        <Markdown source={article.body} onArticle={openHere} />
      </div>
      <footer className={s.foot}>
        <Button kind="ghost" icon="book" onClick={() => openArticleInDocs(article.id)}>
          Read it on the Help & Docs page
        </Button>
      </footer>
    </article>
  );
}
