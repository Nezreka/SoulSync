/**
 * What each pill is for, in one short line under the pills (the maintainer's
 * call on the hero sentences: a plain description, not the settings read
 * back). Keyed `tab/view`, or `tab/view/kind` where a pill's kinds differ
 * (Library). What needs a look is said on the row or card it is about, not
 * here.
 */

export const ABOUT: Record<string, string> = {
  'connections/services': 'Your media server and the services SoulSync connects to.',
  'connections/metasync': 'Connect to MetaSync and join a pool to share metadata.',
  'connections/notifications': 'Where SoulSync sends a message when something happens.',

  'downloads/sources': 'Where downloads come from, and in what order.',
  'downloads/behaviour': 'How downloads run, retry and reach your library.',
  'downloads/filtering': 'Which files a download skips, and which one wins.',

  'quality/music': 'Which music formats SoulSync takes, and when it upgrades.',
  'quality/audiobooks': 'Which audiobook formats and bitrates SoulSync takes.',
  'quality/video': 'Which movie and TV qualities SoulSync grabs and upgrades to.',
  'quality/youtube': 'How videos from followed YouTube channels are downloaded.',

  'library/folders/music': 'Where music downloads land and where your library lives.',
  'library/folders/audiobooks':
    'Where finished audiobooks go, and the other folders SoulSync reads.',
  'library/folders/podcasts': 'The folder podcast episodes are saved in.',
  'library/folders/video': 'Where video downloads land and where movies and shows live.',
  'library/naming/music': 'How music files and folders are named.',
  'library/naming/audiobooks': 'How audiobook files and folders are named.',
  'library/naming/podcasts': 'How podcast episode files and folders are named.',
  'library/naming/video': 'How movie and episode files and folders are named.',
  'library/tags/music': 'What is written into music files: tags, artwork and lyrics.',
  'library/tags/audiobooks': 'What is written into audiobooks: tags, covers and sidecars.',
  'library/tags/video': 'What goes with a finished video: subtitles, artwork and NFO files.',
  'library/import/music': 'How the Import page and library scans treat music files.',
  'library/import/audiobooks': 'When a book counts as done, and how its files are joined.',
  'library/import/podcasts': 'Whether podcasts are kept as audio, video or both.',
  'library/import/video': 'What happens to video files on import, upgrade and delete.',
  'library/subtitles': 'Which subtitles SoulSync fetches, in what languages and from where.',

  'discovery/recommendations': 'How new music is picked for you, and what it learns from.',
  'discovery/playlists': 'How playlists sync to your server, and the M3U files.',
  'discovery/import-lists': 'Lists SoulSync watches for new movies and shows to add.',

  'appearance/all': 'How SoulSync looks, on every device and on this one.',

  'advanced/general': 'How settings save, database workers and the server log level.',
  'advanced/security': 'Who can get into SoulSync, and how they sign in.',
  'advanced/api': 'Keys that let other apps talk to SoulSync.',
  'advanced/maintenance': 'Keep the database and artwork cache tidy, and reset things.',
  'advanced/activity': 'The play log, and what Now playing shows about streams.',
  'advanced/developer': 'Developer mode, preview tools and experimental features.',
  'advanced/logs': 'What the server writes to its log, and the live viewer.',
};

/** a pill's line: its kind's own first, else the pill's */
export function aboutOf(tab: string, view: string, kind?: string): string | null {
  return (kind ? ABOUT[`${tab}/${view}/${kind}`] : undefined) ?? ABOUT[`${tab}/${view}`] ?? null;
}
