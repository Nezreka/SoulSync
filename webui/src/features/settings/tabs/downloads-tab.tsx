/**
 * The Downloads tab of Settings. It is one page now (settings-bic): Sources,
 * Behaviour and Filtering, each a pill (tabs/downloads/). The page renders it
 * as the tab's one slot, "page".
 */

import type { SettingsTabProps } from '../host';

import { DownloadsPage } from './downloads/downloads-page';

export { resetGenres } from './downloads/filtering-view';

export function DownloadsTab(props: SettingsTabProps) {
  return props.part === 'page' ? <DownloadsPage {...props} /> : null;
}
