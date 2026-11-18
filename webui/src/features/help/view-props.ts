/** what the outlet hands every view */

import type { ViewEntry } from './views';

export interface ViewProps {
  entry: ViewEntry;
  /** the header field's text; the results view reads it */
  query: string;
  isAdmin: boolean;
  page: string;
}
