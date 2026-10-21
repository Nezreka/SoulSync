/**
 * The setup flow's draft: everything the user has told it so far. Nothing in
 * here is saved until the ready step applies it, which is what lets the
 * developer preview run the whole flow without touching the install.
 */

export type Purpose = 'music' | 'video' | 'youtube' | 'podcasts' | 'audiobooks';
export type LockMode = 'open' | 'pin' | 'password';
export type ServerKind = 'plex' | 'jellyfin' | 'navidrome' | 'soulsync';
export type SourceId =
  | 'soulseek'
  | 'hifi'
  | 'youtube'
  | 'tidal'
  | 'qobuz'
  | 'deezer_dl'
  | 'soundcloud';
export type QualityPreset = 'audiophile' | 'balanced' | 'space_saver';
export type Catalogue = 'deezer' | 'itunes' | 'spotify';
export type VideoClient = 'qbittorrent' | 'sabnzbd';
export type FolderKey =
  | 'download'
  | 'library'
  | 'staging'
  | 'movies'
  | 'tv'
  | 'youtube'
  | 'podcasts'
  | 'audiobooks';

export type StepId =
  | 'welcome'
  | 'account'
  | 'purpose'
  | 'server'
  | 'folders'
  | 'sources'
  | 'metadata'
  | 'video'
  | 'library'
  | 'ready';

export interface ServerDraft {
  kind: ServerKind | null;
  url: string;
  token: string;
  apiKey: string;
  username: string;
  password: string;
  /** what the server said its name is, once connected */
  serverName: string;
  connected: boolean;
  musicLibrary: string | null;
  moviesLibrary: string | null;
  tvLibrary: string | null;
  /** library title -> the folders the server reads it from */
  libraryPaths: Record<string, string[]>;
}

export interface FollowPick {
  name: string;
  id?: string;
  image?: string;
}

export interface Draft {
  account: { name: string; color: string; lock: LockMode; pin: string; password: string };
  purposes: Purpose[];
  server: ServerDraft;
  folders: Record<FolderKey, string>;
  chain: SourceId[];
  slskd: { url: string; apiKey: string; connected: boolean };
  deezerArl: string;
  /** set once the source's own sign-in finished (it saves its own session) */
  signedIn: Partial<Record<'tidal' | 'qobuz', boolean>>;
  preset: QualityPreset;
  catalogue: Catalogue;
  spotify: { clientId: string; clientSecret: string };
  listenbrainz: { token: string; user: string };
  lastfm: { apiKey: string; username: string };
  video: {
    tmdbKey: string;
    prowlarr: { url: string; apiKey: string };
    client: { kind: VideoClient; url: string; username: string; password: string; apiKey: string };
  };
  follows: FollowPick[];
  playlistUrl: string;
}

export interface Environment {
  docker: boolean;
  platform: string;
  folders: Record<FolderKey, string>;
}

export interface ProbeResult {
  ok: boolean;
  message: string;
  detail?: Record<string, unknown>;
}

export interface LibraryInfo {
  id: string;
  title: string;
  count: number | null;
  locations: string[];
}

export interface ServerLibraries {
  music: LibraryInfo[];
  movies: LibraryInfo[];
  tv: LibraryInfo[];
}

export interface PathCheck {
  path: string;
  resolved: string;
  state: 'ok' | 'create' | 'problem';
  message: string;
  free_bytes: number | null;
}

export interface OpenOptions {
  /** run the flow without saving anything */
  preview?: boolean;
  startAt?: StepId;
  /** pretend environment for the preview */
  pretend?: 'real' | 'docker' | 'bare' | 'empty';
}
