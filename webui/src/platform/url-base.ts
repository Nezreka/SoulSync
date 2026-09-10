/** Browser-facing paths; route IDs and API keys remain deployment-independent. */
export function getURLBase(): string {
  if (typeof document === 'undefined') return '';
  return document.querySelector<HTMLMetaElement>('meta[name="soulsync-url-base"]')?.content || '';
}
export function appURL(path: string): string {
  const base = getURLBase();
  if (!base || !path.startsWith('/') || path.startsWith('//') || path === base || path.startsWith(base + '/')) return path;
  return base + path;
}
