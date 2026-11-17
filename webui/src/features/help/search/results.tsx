/**
 * P3 replaces this file: the inline results for the header's search field,
 * grouped Actions, Settings, Pages, Help (plan 4.4), from the same index
 * the Ctrl K palette reads. Until then, hand the words to the docs search.
 */

import type { ViewProps } from '../view-props';

import { openDocsHome } from '../links';
import { Button, Placeholder } from '../ui';

export function SearchResultsView({ query }: ViewProps) {
  return (
    <Placeholder
      title={`Searching for “${query.trim()}”`}
      text="Results for help, settings and pages show here. For now the Help & Docs page searches every guide: press / there."
    >
      <Button kind="primary" icon="book" onClick={openDocsHome}>
        Open Help & Docs
      </Button>
    </Placeholder>
  );
}
