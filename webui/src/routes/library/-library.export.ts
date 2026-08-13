/**
 * Export Artists (openArtistExportModal, library.js:7372): one modal for both
 * rosters — Watchlist or Library — as JSON / CSV / text, with optional
 * external links, library counts, and the library-only whole-library M3U.
 */

export type ExportScope = 'watchlist' | 'library';
export type ExportFormat = 'json' | 'csv' | 'txt';

export function exportEndpoint(scope: ExportScope): string {
  return scope === 'library' ? '/api/library/artists/export' : '/api/watchlist/export';
}

export function exportFileName(scope: ExportScope, format: ExportFormat): string {
  return `${scope === 'library' ? 'library_artists' : 'watchlist'}_export.${format}`;
}

export function exportMime(format: ExportFormat): string {
  return format === 'json' ? 'application/json' : format === 'csv' ? 'text/csv' : 'text/plain';
}

/** GET the export; the count rides the X-Export-Count header (7449). */
export async function fetchExport(
  scope: ExportScope,
  format: ExportFormat,
  links: boolean,
  contents: boolean,
): Promise<{ content: string; count: string }> {
  const url =
    `${exportEndpoint(scope)}?format=${format}&links=${links ? '1' : '0'}` +
    (scope === 'library' && contents ? '&contents=1' : '');
  const response = await fetch(url);
  const content = await response.text();
  return { content, count: response.headers.get('X-Export-Count') || '?' };
}

/** Blob download, exactly the vanilla's anchor dance (7495-7505). */
export function downloadExport(content: string, scope: ExportScope, format: ExportFormat): void {
  const blob = new Blob([content || ''], { type: exportMime(format) });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = exportFileName(scope, format);
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  window.showToast?.(`Saved ${exportFileName(scope, format)}`, 'success');
}

/** The server builds the whole-library track playlist itself (7485-7494). */
export function downloadLibraryM3u(): void {
  const a = document.createElement('a');
  a.href = '/api/library/export/m3u';
  a.download = 'soulsync_library.m3u';
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.showToast?.('Building library M3U…', 'info');
}
