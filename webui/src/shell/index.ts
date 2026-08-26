/**
 * The shell bundle entry - the typed home for what used to be classic
 * scripts in webui/static. Built as a synchronous IIFE (vite.shell.config.ts
 * -> static/dist/shell.js) and loaded as a CLASSIC script tag in the same
 * slot the vanilla files occupied, because the modules ported here are
 * consumed by the remaining classic scripts and by inline onclick handlers -
 * both need the names on window before later scripts run, which a deferred
 * `type="module"` bundle cannot provide.
 *
 * Every port adds its module here and assigns its public names onto window.
 * The assignment list IS the compatibility contract - the census test pins it.
 */

import {
  blockFromSearch,
  closeBlocklistModal,
  onBlocklistSearchInput,
  openBlocklistModal,
  switchBlocklistTab,
  unblockEntry,
} from './blocklist';

/** every name the rest of the app may reach through window. */
export const SHELL_WINDOW_EXPORTS = {
  // blocklist.js (ported aug 26)
  openBlocklistModal,
  closeBlocklistModal,
  switchBlocklistTab,
  onBlocklistSearchInput,
  blockFromSearch,
  unblockEntry,
} as const;

Object.assign(window, SHELL_WINDOW_EXPORTS);
