/**
 * generated cover art for any playlist, mirrored or yours. the server reads
 * what the playlist sounds like and asks pollinations for one image. it comes
 * back as a preview; nothing changes on the playlist until applyCoverArt.
 */

export interface CoverArtVibe {
  name: string;
  genres: string[];
  moods: string[];
  decade: number | null;
}

export interface CoverArtPreview {
  preview_url: string;
  vibe: CoverArtVibe;
  prompt: string;
  /** a pollinations key is set: no mark on the image, no 45s wait */
  keyed: boolean;
  /** the playlist's name is drawn on the preview */
  show_name: boolean;
}

/** a failed call. retryAfter is set when waiting is the fix (the free tier). */
export class CoverArtError extends Error {
  retryAfter: number | null;
  constructor(message: string, retryAfter: number | null = null) {
    super(message);
    this.name = 'CoverArtError';
    this.retryAfter = retryAfter;
  }
}

async function call<T>(url: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, init);
  } catch {
    throw new CoverArtError("Couldn't reach SoulSync. Try again.");
  }
  let data: (T & { error?: string; retry_after?: number }) | null = null;
  try {
    data = (await response.json()) as T & { error?: string; retry_after?: number };
  } catch {
    data = null;
  }
  if (!response.ok || !data || data.error) {
    const wait = typeof data?.retry_after === 'number' ? data.retry_after : null;
    throw new CoverArtError(data?.error || `Request failed (${response.status})`, wait);
  }
  return data;
}

const base = (playlistId: number) => `/api/mirrored-playlists/${playlistId}/cover-art`;

/** make a preview. attempt rotates the art style, so try-another really differs. */
export function generateCoverArt(
  playlistId: number,
  attempt = 0,
  showName = true,
): Promise<CoverArtPreview> {
  return call<CoverArtPreview>(base(playlistId), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ attempt, show_name: showName }),
  });
}

/** the name on the preview, or off it. redraws the same image, no new one. */
export function setCoverArtName(
  playlistId: number,
  showName: boolean,
): Promise<{ preview_url: string; show_name: boolean }> {
  return call<{ preview_url: string; show_name: boolean }>(`${base(playlistId)}/name`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ show_name: showName }),
  });
}

/** the preview becomes the playlist's cover. */
export async function applyCoverArt(playlistId: number): Promise<string> {
  const data = await call<{ image_url: string }>(`${base(playlistId)}/apply`, { method: 'POST' });
  return data.image_url;
}

/** throw the unused preview away. best effort, nothing waits on it. */
export async function discardCoverArtPreview(playlistId: number): Promise<void> {
  try {
    await fetch(`${base(playlistId)}?preview_only=1`, { method: 'DELETE' });
  } catch {
    // a stray preview file is harmless, the next generate overwrites it
  }
}

/** a mirror goes back to its source's own cover. */
export async function restoreOriginalCover(playlistId: number): Promise<void> {
  await call<{ success: boolean }>(base(playlistId), { method: 'DELETE' });
}

/** "ambient, electronic, dreamy, 2020s": what the image was made from. */
export function vibeLine(vibe: CoverArtVibe | null | undefined): string {
  if (!vibe) return '';
  const parts = [...(vibe.genres ?? []).slice(0, 3), ...(vibe.moods ?? []).slice(0, 2)];
  if (vibe.decade) parts.push(`${vibe.decade}s`);
  return parts.join(', ');
}
