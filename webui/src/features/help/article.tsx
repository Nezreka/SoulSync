/**
 * P3 replaces this file: a docs article read inside the sheet, with "Open in
 * Help & Docs" for the full page (plan 4.2). entry.params.id is the docs
 * page id. Until then the article opens on the Help & Docs page.
 */

import type { ViewProps } from './view-props';

import { openArticle } from './links';
import { Button, Placeholder } from './ui';

export function ArticleView({ entry }: ViewProps) {
  const id = entry.params?.id ?? '';
  return (
    <Placeholder
      title="Read it in Help & Docs"
      text="Articles open in the sheet soon. This one is a click away."
    >
      <Button kind="primary" icon="book" onClick={() => openArticle(id)}>
        Open the article
      </Button>
    </Placeholder>
  );
}
