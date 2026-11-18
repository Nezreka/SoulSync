/**
 * The live log viewer's logic, moved from settings.js (SETTINGS_PORT.md,
 * step 3). The level a line is shown with, and the stream it arrives on.
 */

export const LOG_MAX_LINES = 2000;
export const LOG_TAIL_LINES = 300;
export const LOG_SEARCH_DELAY_MS = 300;

export const LOG_SOURCES = [
  { value: 'app', label: 'app.log' },
  { value: 'post_processing', label: 'post_processing.log' },
  { value: 'acoustid', label: 'acoustid.log' },
  { value: 'source_reuse', label: 'source_reuse.log' },
] as const;

export const LOG_LEVELS = [
  { level: '', label: 'All', className: '' },
  { level: 'DEBUG', label: 'Debug', className: 'lvl-debug' },
  { level: 'INFO', label: 'Info', className: 'lvl-info' },
  { level: 'WARNING', label: 'Warn', className: 'lvl-warning' },
  { level: 'ERROR', label: 'Error', className: 'lvl-error' },
] as const;

export type LogLevel = 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR';

/** the level a line is shown with: the logger's own format first, then a
 *  guess for lines that came from print() */
export function classifyLogLine(line: string): LogLevel {
  if (line.includes(' - DEBUG - ')) return 'DEBUG';
  if (line.includes(' - INFO - ')) return 'INFO';
  if (line.includes(' - WARNING - ')) return 'WARNING';
  if (line.includes(' - ERROR - ') || line.includes(' - CRITICAL - ')) return 'ERROR';
  const lower = line.toLowerCase();
  if (
    lower.includes('error') ||
    lower.includes('traceback') ||
    lower.includes('exception') ||
    lower.includes('failed')
  ) {
    return 'ERROR';
  }
  if (lower.includes('warning') || lower.includes('warn')) return 'WARNING';
  if (lower.includes('debug')) return 'DEBUG';
  return 'INFO';
}

export function logLevelClass(level: string): string {
  return (
    (
      {
        DEBUG: 'log-debug',
        INFO: 'log-info',
        WARNING: 'log-warning',
        ERROR: 'log-error',
      } as Record<string, string>
    )[level] ?? 'log-plain'
  );
}

/** live lines are filtered here, the tail is filtered by the server */
export function keepLiveLines(lines: string[], level: string, search: string): string[] {
  let kept = lines;
  if (level) kept = kept.filter((line) => classifyLogLine(line) === level);
  if (search) {
    const needle = search.toLowerCase();
    kept = kept.filter((line) => line.toLowerCase().includes(needle));
  }
  return kept;
}

export interface LogLiveFrame {
  source?: string;
  lines?: string[];
}

/** core.js's socket is a script-scoped `let`, which this module cannot
 *  reach; core.js lends it through these two calls */
export interface LogStreamBridge {
  /** false when the socket is not connected: the tail shows, nothing live */
  subscribe: (source: string, onLive: (frame: LogLiveFrame) => void) => boolean;
  unsubscribe: (onLive: (frame: LogLiveFrame) => void) => void;
}

declare global {
  interface Window {
    soulsyncLogStream?: LogStreamBridge;
  }
}
