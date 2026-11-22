/**
 * The Sources tab went into Downloads (settings-bic): the order, the sources
 * not used and each source's sheet are tabs/downloads/sources-view.tsx. This
 * keeps the name the source panels' tests open a panel through.
 */

import { settingsStore } from '../settings-store';
import { SourceSheet } from './downloads/sources-view';
import { type SourcesModel, sourcesModel } from './sources-model';

/** one source's settings, in the side sheet */
export function SourceModal({ model = sourcesModel }: { model?: SourcesModel }) {
  return <SourceSheet store={settingsStore} model={model} />;
}
