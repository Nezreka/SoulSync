/**
 * The System tab's words (settings-bic phase 6): the log levels, as the
 * level picker and the log viewer say them.
 */

/** the four log levels, as the server names them, in words */
export const LOG_LEVEL_WORDS: Record<string, { label: string; shows: string }> = {
  DEBUG: { label: 'Debug', shows: 'every detail, for chasing a problem' },
  INFO: { label: 'Info', shows: 'what SoulSync does and every problem' },
  WARNING: { label: 'Warning', shows: 'warnings and errors only' },
  ERROR: { label: 'Error', shows: 'errors only' },
};
