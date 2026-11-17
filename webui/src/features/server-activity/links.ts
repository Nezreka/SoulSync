/**
 * "Open in SoulSync": a stream, a history row or a shelf item opens the
 * SoulSync page for it. Only kinds the contract links to (SessionLink).
 */

import type { SessionLink } from '@/shell/server-activity-types';

export function linkLabel(link: SessionLink): string {
  if ((link.kind === 'movie' || link.kind === 'show') && link.source === 'tmdb') {
    return 'Preview in SoulSync';
  }
  return 'Open in SoulSync';
}

export async function openLink(link: SessionLink, name = ''): Promise<void> {
  if (link.kind === 'movie' || link.kind === 'show') {
    const id = typeof link.id === 'string' && /^\d+$/.test(link.id) ? Number(link.id) : link.id;
    window.SoulSyncVideo?.openDetail?.({ kind: link.kind, id, source: link.source || 'library' });
    return;
  }
  if (link.kind === 'music') {
    await window.navigateToPage?.('artist-detail', {
      artistId: link.artist_id,
      artistName: name,
      artistSource: 'library',
      focusAlbumId: link.album_id || undefined,
    });
    return;
  }
  if (link.kind === 'audiobook') {
    await window.navigateToPage?.('audiobooks');
    await window.SoulSyncWebRouter?.navigateToHref?.(`/audiobooks/${encodeURIComponent(link.id)}`);
  }
}
