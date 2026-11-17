/**
 * View id to component. Each view is its own file, owned by one phase
 * (README.md); replacing a view means replacing its file, not this map.
 */

import type { ComponentType } from 'react';

import type { ViewProps } from './view-props';
import type { ViewId } from './views';

import { ArticleView } from './article';
import { HealthView } from './health';
import { HomeView } from './home';
import { LogsView } from './logs';
import { ReportView } from './report';
import { SearchResultsView } from './search/results';
import { ShortcutsView } from './shortcuts';
import { WhatsNewView } from './whats-new';

export const VIEWS: Record<ViewId, ComponentType<ViewProps>> = {
  home: HomeView,
  search: SearchResultsView,
  article: ArticleView,
  health: HealthView,
  report: ReportView,
  logs: LogsView,
  'whats-new': WhatsNewView,
  shortcuts: ShortcutsView,
};
