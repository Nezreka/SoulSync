/**
 * Loads every page's help. base.ts first, then one line per page file that
 * adds live answers (P5), e.g. `import './active-downloads';`. Later files
 * merge over earlier ones (registerPageHelp).
 */

import './base';
import './active-downloads';
import './video-downloads';
import './requests';
import './wishlist';
import './import';
import './settings';
import './library';
import './search';
