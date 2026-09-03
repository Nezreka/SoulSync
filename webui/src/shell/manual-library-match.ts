/**
 * Manual Library Match - ported from webui/static/manual-library-match.js.
 *
 * Link source tracks (wishlist + sync history) to library tracks so they stop
 * re-downloading. Opened from still-vanilla markup (index.html sync-history +
 * tools buttons). Self-contained: module-scope _mlm* state, its own escaper
 * semantics (via the shared shell escaper), inline handlers bound by name.
 */

import { escapeHtml } from './html';

interface MlmSourceTrack {
  source?: string;
  source_track_id?: string | number;
  title?: string;
  artist?: string;
  album?: string;
  context?: string;
}

interface MlmLibraryTrack {
  id: number;
  title?: string;
  artist_name?: string;
  album_title?: string;
  file_path?: string;
  bitrate?: number;
}

type MlmResultsEl = HTMLElement & { _mlmTracks?: MlmSourceTrack[] & MlmLibraryTrack[] };

let _mlmOverlay: HTMLDivElement | null = null;
let _mlmSelectedSource: MlmSourceTrack | null = null;
let _mlmSelectedLibrary: MlmLibraryTrack | null = null;
let _mlmSourceTimer: ReturnType<typeof setTimeout> | null = null;
let _mlmLibraryTimer: ReturnType<typeof setTimeout> | null = null;

export function openManualLibraryMatchTool(prefill?: string): void {
  if (_mlmOverlay) _mlmOverlay.remove();

  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.id = 'mlm-overlay';
  overlay.onclick = (e) => {
    if (e.target === overlay) _mlmClose();
  };

  overlay.innerHTML = `
        <div class="playlist-modal mlm-modal">
            <div class="playlist-modal-header">
                <div class="playlist-header-content">
                    <h2>Manual Library Match</h2>
                    <div class="playlist-quick-info">
                        <span class="playlist-owner">Link source tracks to library tracks to stop re-downloads</span>
                    </div>
                </div>
                <span class="playlist-modal-close" onclick="_mlmClose()">&times;</span>
            </div>

            <div class="mlm-modal-body">
                <div class="mlm-panels">
                    <div class="mlm-panel source">
                        <div class="server-col-header">
                            <span class="server-col-icon">📋</span>
                            Source Track
                        </div>
                        <div class="mlm-panel-search-wrap">
                            <input class="mlm-search" id="mlm-source-search" placeholder="Search wishlist &amp; sync history&hellip;" oninput="_mlmSourceDebounce(this.value)">
                        </div>
                        <div class="server-col-scroll" id="mlm-source-results"><p class="mlm-hint">Type to search</p></div>
                    </div>
                    <div class="mlm-panel library">
                        <div class="server-col-header">
                            <span class="server-col-icon">🎵</span>
                            Library Track
                        </div>
                        <div class="mlm-panel-search-wrap">
                            <input class="mlm-search" id="mlm-library-search" placeholder="Search your library&hellip;" oninput="_mlmLibraryDebounce(this.value)">
                        </div>
                        <div class="server-col-scroll" id="mlm-library-results"><p class="mlm-hint">Type to search</p></div>
                    </div>
                </div>

                <div class="mlm-existing-section">
                    <div class="server-col-header mlm-matches-header">
                        Existing Matches
                        <span class="server-col-count" id="mlm-match-count"></span>
                    </div>
                    <div class="mlm-matches-wrap" id="mlm-matches-list"><p class="mlm-hint">Loading&hellip;</p></div>
                </div>
            </div>

            <div class="playlist-modal-footer">
                <div class="playlist-modal-footer-left">
                    <span id="mlm-status" class="mlm-status-msg"></span>
                </div>
                <div class="playlist-modal-footer-right">
                    <button class="playlist-modal-btn playlist-modal-btn-secondary" onclick="_mlmClose()">Cancel</button>
                    <button class="playlist-modal-btn playlist-modal-btn-primary" id="mlm-save-btn" disabled onclick="_mlmSaveMatch()">Save Match</button>
                </div>
            </div>
        </div>
    `;

  document.body.appendChild(overlay);
  _mlmOverlay = overlay;
  _mlmSelectedSource = null;
  _mlmSelectedLibrary = null;
  _mlmUpdateSaveBtn();
  void _mlmLoadMatches();

  if (prefill) {
    const src = document.getElementById('mlm-source-search') as HTMLInputElement | null;
    if (src) {
      src.value = prefill;
      void _mlmSourceSearch(prefill);
    }
  }
}

export function _mlmClose(): void {
  if (_mlmOverlay) {
    _mlmOverlay.remove();
    _mlmOverlay = null;
  }
  _mlmSelectedSource = null;
  _mlmSelectedLibrary = null;
}

export function _mlmSourceDebounce(q: string): void {
  if (_mlmSourceTimer) clearTimeout(_mlmSourceTimer);
  _mlmSourceTimer = setTimeout(() => void _mlmSourceSearch(q), 300);
}
export function _mlmLibraryDebounce(q: string): void {
  if (_mlmLibraryTimer) clearTimeout(_mlmLibraryTimer);
  _mlmLibraryTimer = setTimeout(() => void _mlmLibrarySearch(q), 300);
}

async function _mlmSourceSearch(q: string): Promise<void> {
  const el = document.getElementById('mlm-source-results') as MlmResultsEl | null;
  if (!el) return;
  if (!q.trim()) {
    el.innerHTML = '<p class="mlm-hint">Type to search</p>';
    return;
  }
  el.innerHTML = '<p class="mlm-hint">Searching&hellip;</p>';
  try {
    const res = await fetch(`/api/manual-library-matches/source-search?q=${encodeURIComponent(q)}&limit=15`);
    const data = (await res.json()) as { tracks?: MlmSourceTrack[] };
    _mlmRenderSourceResults(data.tracks || []);
  } catch {
    el.innerHTML = '<p class="mlm-hint mlm-error">Search failed</p>';
  }
}

async function _mlmLibrarySearch(q: string): Promise<void> {
  const el = document.getElementById('mlm-library-results') as MlmResultsEl | null;
  if (!el) return;
  if (!q.trim()) {
    el.innerHTML = '<p class="mlm-hint">Type to search</p>';
    return;
  }
  el.innerHTML = '<p class="mlm-hint">Searching&hellip;</p>';
  try {
    const res = await fetch(`/api/manual-library-matches/library-search?q=${encodeURIComponent(q)}&limit=15`);
    const data = (await res.json()) as { tracks?: MlmLibraryTrack[] };
    _mlmRenderLibraryResults(data.tracks || []);
  } catch {
    el.innerHTML = '<p class="mlm-hint mlm-error">Search failed</p>';
  }
}

const _mlmEsc = (str: unknown): string => escapeHtml(str || '');

function _mlmRenderSourceResults(tracks: MlmSourceTrack[]): void {
  const el = document.getElementById('mlm-source-results') as MlmResultsEl | null;
  if (!el) return;
  if (!tracks.length) {
    el.innerHTML = '<p class="mlm-hint">No results</p>';
    return;
  }
  el.innerHTML = tracks.map((t, i) => {
    const sel = _mlmSelectedSource && _mlmSelectedSource.source_track_id === t.source_track_id ? 'mlm-row-selected' : '';
    return `<div class="mlm-result-row ${sel}" data-idx="${i}" onclick="_mlmSelectSource(${i})">
            <div class="mlm-row-title">${_mlmEsc(t.title || '—')}</div>
            <div class="mlm-row-sub">${_mlmEsc(t.artist || '')}${t.album ? ' · ' + _mlmEsc(t.album) : ''}</div>
            <div class="mlm-row-ctx">${_mlmEsc(t.context || t.source || '')}</div>
        </div>`;
  }).join('');
  el._mlmTracks = tracks as MlmResultsEl['_mlmTracks'];
}

function _mlmRenderLibraryResults(tracks: MlmLibraryTrack[]): void {
  const el = document.getElementById('mlm-library-results') as MlmResultsEl | null;
  if (!el) return;
  if (!tracks.length) {
    el.innerHTML = '<p class="mlm-hint">No results</p>';
    return;
  }
  el.innerHTML = tracks.map((t, i) => {
    const sel = _mlmSelectedLibrary && _mlmSelectedLibrary.id === t.id ? 'mlm-row-selected' : '';
    const path = t.file_path ? t.file_path.split(/[/\\]/).pop() : '';
    return `<div class="mlm-result-row ${sel}" data-idx="${i}" onclick="_mlmSelectLibrary(${i})">
            <div class="mlm-row-title">${_mlmEsc(t.title || '—')}</div>
            <div class="mlm-row-sub">${_mlmEsc(t.artist_name || '')}${t.album_title ? ' · ' + _mlmEsc(t.album_title) : ''}</div>
            <div class="mlm-row-ctx">${_mlmEsc(path)}${t.bitrate ? ' · ' + t.bitrate + 'kbps' : ''}</div>
        </div>`;
  }).join('');
  el._mlmTracks = tracks as MlmResultsEl['_mlmTracks'];
}

export function _mlmSelectSource(idx: number): void {
  const el = document.getElementById('mlm-source-results') as MlmResultsEl | null;
  if (!el || !el._mlmTracks) return;
  _mlmSelectedSource = el._mlmTracks[idx];
  el.querySelectorAll('.mlm-result-row').forEach((r, i) => r.classList.toggle('mlm-row-selected', i === idx));
  _mlmUpdateSaveBtn();
}

export function _mlmSelectLibrary(idx: number): void {
  const el = document.getElementById('mlm-library-results') as MlmResultsEl | null;
  if (!el || !el._mlmTracks) return;
  _mlmSelectedLibrary = el._mlmTracks[idx] as MlmLibraryTrack;
  el.querySelectorAll('.mlm-result-row').forEach((r, i) => r.classList.toggle('mlm-row-selected', i === idx));
  _mlmUpdateSaveBtn();
}

function _mlmUpdateSaveBtn(): void {
  const btn = document.getElementById('mlm-save-btn') as HTMLButtonElement | null;
  if (btn) btn.disabled = !(_mlmSelectedSource && _mlmSelectedLibrary);
}

export async function _mlmSaveMatch(): Promise<void> {
  if (!_mlmSelectedSource || !_mlmSelectedLibrary) return;
  const status = document.getElementById('mlm-status');
  if (status) status.textContent = 'Saving…';
  try {
    const body = {
      source: _mlmSelectedSource.source,
      source_track_id: _mlmSelectedSource.source_track_id,
      library_track_id: _mlmSelectedLibrary.id,
      source_title: _mlmSelectedSource.title || '',
      source_artist: _mlmSelectedSource.artist || '',
      source_album: _mlmSelectedSource.album || '',
      source_context_json: '',
      server_source: '',
    };
    const res = await fetch('/api/manual-library-matches', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = (await res.json()) as { success?: boolean; error?: string };
    if (data.success) {
      if (status) status.textContent = 'Saved!';
      _mlmSelectedSource = null;
      _mlmSelectedLibrary = null;
      _mlmUpdateSaveBtn();
      await _mlmLoadMatches();
      setTimeout(() => {
        if (status) status.textContent = '';
      }, 2000);
    } else {
      if (status) status.textContent = 'Error: ' + (data.error || 'unknown');
    }
  } catch {
    if (status) status.textContent = 'Network error';
  }
}

async function _mlmLoadMatches(): Promise<void> {
  const el = document.getElementById('mlm-matches-list');
  if (!el) return;
  try {
    const res = await fetch('/api/manual-library-matches');
    const data = (await res.json()) as {
      matches?: Array<{
        id: number; source: string; source_track_id: string | number;
        source_title?: string; source_artist?: string;
        library_track_id: number; library_title?: string; library_artist?: string;
      }>;
    };
    const matches = data.matches || [];
    const countEl = document.getElementById('mlm-match-count');
    if (countEl) countEl.textContent = String(matches.length);
    if (!matches.length) {
      el.innerHTML = '<p class="mlm-hint">No matches saved yet</p>';
      return;
    }
    el.innerHTML = `<table class="mlm-matches-table">
            <thead><tr><th>Source Track</th><th>Library Track</th><th>Source</th><th></th></tr></thead>
            <tbody>${matches.map((m) => `<tr>
                <td><div class="mlm-row-title">${_mlmEsc(m.source_title || m.source_track_id)}</div><div class="mlm-row-sub">${_mlmEsc(m.source_artist || '')}</div></td>
                <td><div class="mlm-row-title">${_mlmEsc(m.library_title || String(m.library_track_id))}</div><div class="mlm-row-sub">${_mlmEsc(m.library_artist || '')}</div></td>
                <td><span class="mlm-source-badge">${_mlmEsc(m.source)}</span></td>
                <td><button class="mlm-remove-btn" onclick="_mlmDeleteMatch(${m.id})" title="Remove match">&#x2715;</button></td>
            </tr>`).join('')}</tbody>
        </table>`;
  } catch {
    el.innerHTML = '<p class="mlm-hint mlm-error">Failed to load matches</p>';
  }
}

export async function _mlmDeleteMatch(id: number): Promise<void> {
  // #1138: the server now says whether a row was actually removed. Silently
  // reloading either way is what made a failed delete look like a UI that
  // simply refused to work.
  try {
    const res = await fetch(`/api/manual-library-matches/${id}`, { method: 'DELETE' });
    let data: { success?: boolean; error?: string } = {};
    try {
      data = (await res.json()) as typeof data;
    } catch {
      /* non-JSON error page */
    }
    if (!res.ok || data.success === false) {
      window.showToast?.(data.error || 'Could not remove that match', 'error');
    }
    await _mlmLoadMatches();
  } catch {
    window.showToast?.('Failed to remove match', 'error');
  }
}
