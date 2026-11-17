/**
 * Loads every page's help. base.ts first, then one line per page file that
 * adds live answers (P5), e.g. `import './active-downloads';`. Later files
 * merge over earlier ones (registerPageHelp).
 */

import './base';
