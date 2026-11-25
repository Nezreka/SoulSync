/**
 * A filled Discover for a new profile (core/discovery/cold_start.py).
 *
 * While a profile has fewer taste seeds than the server's threshold, the top
 * of Discover offers ways to tell SoulSync what it likes: connect
 * ListenBrainz or Last.fm, import Spotify history (only once that import
 * exists on this server), or pick artists by hand. Picks are saved as the
 * profile's own seeds and its first recommendations are built right away.
 */

export interface StarterPick {
  name: string;
  deezer_id: string | null;
  image_url: string | null;
  popularity?: number;
}

export interface StarterBuild {
  state: 'idle' | 'running' | 'done' | 'failed';
  done: number;
  total: number;
  stored: number;
}

export interface StarterStatus {
  success?: boolean;
  seeds: number;
  threshold: number;
  needs_seeds: boolean;
  dismissed: boolean;
  show_banner: boolean;
  picked: StarterPick[];
  connections: { listenbrainz: boolean; lastfm: boolean };
  spotify_import: { available: boolean; href: string | null };
  build: StarterBuild;
}

export interface PickerGenre {
  id: number;
  name: string;
}

export interface PickerPayload {
  success?: boolean;
  artists: StarterPick[];
  genres: PickerGenre[];
}

/** The picker asks for this many; the server keeps more if you pick more. */
export const MIN_PICKS = 5;

export const STARTER_KEY = ['discover', 'starter'] as const;

export async function fetchStarter(): Promise<StarterStatus | null> {
  try {
    const res = await fetch('/api/discover/starter');
    if (!res.ok) return null;
    const data = (await res.json()) as StarterStatus;
    return data && data.success !== false ? data : null;
  } catch {
    return null;
  }
}

export async function dismissStarter(): Promise<boolean> {
  try {
    const res = await fetch('/api/discover/starter/dismiss', { method: 'POST' });
    return res.ok;
  } catch {
    return false;
  }
}

export async function fetchPickerArtists(params: {
  genre?: number;
  query?: string;
}): Promise<PickerPayload> {
  const qs = new URLSearchParams();
  if (params.query) qs.set('q', params.query);
  else qs.set('genre', String(params.genre ?? 0));
  const res = await fetch(`/api/discover/starter/artists?${qs.toString()}`);
  if (!res.ok) throw new Error('Could not load artists');
  const data = (await res.json()) as PickerPayload;
  return { artists: data.artists ?? [], genres: data.genres ?? [] };
}

export interface SavePicksResult {
  success: boolean;
  build?: StarterBuild;
  seeds?: number;
  error?: string;
}

export async function savePicks(artists: StarterPick[]): Promise<SavePicksResult> {
  const res = await fetch('/api/discover/starter/picks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ artists }),
  });
  const data = (await res.json().catch(() => null)) as SavePicksResult | null;
  if (!res.ok || !data || !data.success) {
    return { success: false, error: data?.error || 'Could not save your picks' };
  }
  return data;
}

export type StarterOfferId = 'pick' | 'listenbrainz' | 'lastfm' | 'spotify';

export interface StarterOffer {
  id: StarterOfferId;
  title: string;
  detail: string;
  href?: string | null;
}

/**
 * What the banner offers, in order. A connected account is not offered again,
 * and the Spotify import only appears when the server has it.
 */
export function starterOffers(status: StarterStatus): StarterOffer[] {
  const offers: StarterOffer[] = [
    {
      id: 'pick',
      title: 'Pick artists you love',
      detail: `Choose ${MIN_PICKS} or more and get picks right away`,
    },
  ];
  if (!status.connections?.listenbrainz) {
    offers.push({
      id: 'listenbrainz',
      title: 'Connect ListenBrainz',
      detail: 'Bring your listening history',
    });
  }
  if (!status.connections?.lastfm) {
    offers.push({ id: 'lastfm', title: 'Connect Last.fm', detail: 'Bring your scrobbles' });
  }
  if (status.spotify_import?.available) {
    offers.push({
      id: 'spotify',
      title: 'Import Spotify history',
      detail: 'From your Spotify data download',
      href: status.spotify_import.href,
    });
  }
  return offers;
}

/** "3 more to go" / "Ready" for the picker footer. */
export function pickProgressLabel(count: number): string {
  if (count >= MIN_PICKS) return `${count} picked`;
  const left = MIN_PICKS - count;
  return `${count} of ${MIN_PICKS} · ${left} more to go`;
}

export function canSavePicks(count: number): boolean {
  return count >= MIN_PICKS;
}

/** A stable key per artist: the Deezer id, else the folded name. */
export function pickKey(pick: Pick<StarterPick, 'name' | 'deezer_id'>): string {
  return pick.deezer_id ? `dz:${pick.deezer_id}` : `nm:${pick.name.trim().toLowerCase()}`;
}

/** Select or unselect one artist, keeping the order they were picked in. */
export function togglePick(selected: StarterPick[], pick: StarterPick): StarterPick[] {
  const key = pickKey(pick);
  return selected.some((p) => pickKey(p) === key)
    ? selected.filter((p) => pickKey(p) !== key)
    : [...selected, pick];
}

/** The banner's line while the first picks are being built, or null. */
export function buildLine(build: StarterBuild | null | undefined): string | null {
  if (!build || build.state !== 'running') return null;
  if (!build.total) return 'Building your first picks…';
  return `Building your first picks · ${Math.min(build.done, build.total)} of ${build.total} artists`;
}

/** How full the seed meter is, 0..1. */
export function seedFill(status: Pick<StarterStatus, 'seeds' | 'threshold'>): number {
  if (!status.threshold) return 1;
  return Math.max(0, Math.min(1, status.seeds / status.threshold));
}
