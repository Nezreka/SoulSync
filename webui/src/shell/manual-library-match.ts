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
  server_source?: string;
}

type MlmResultsEl = HTMLElement & {
  _mlmTracks?: MlmSourceTrack[] & MlmLibraryTrack[];
};

let _mlmOverlay: HTMLDivElement | null = null;
let _mlmSelectedSource: MlmSourceTrack | null = null;
let _mlmSelectedLibrary: MlmLibraryTrack | null = null;
let _mlmSourceTimer: ReturnType<typeof setTimeout> | null = null;
let _mlmLibraryTimer: ReturnType<typeof setTimeout> | null = null;
// #1289: pre-populated worklist of unmatched wanted tracks, loaded when the
// modal opens. Clearing the search box restores this list.
let _mlmUnmatchedCache: MlmSourceTrack[] | null = null;

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
                    <div class="mlm-direction-note" style="font-size:12px;opacity:0.7;margin-top:6px;">Links a source track to a library track &mdash; nothing is added to any playlist.</div>
                    <div class="mlm-persist-note" style="font-size:12px;opacity:0.7;margin-top:2px;">Saved as: link only. Affects next sync: yes.</div>
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
                            <input class="mlm-search" id="mlm-source-search" placeholder="Search unmatched tracks&hellip;" oninput="_mlmSourceDebounce(this.value)">
                        </div>
                        <div class="server-col-scroll" id="mlm-source-results"><p class="mlm-hint">Loading unmatched tracks&hellip;</p></div>
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
                    <label id="mlm-add-to-playlist-wrap" class="checkbox-label" style="display:none;margin-top:6px;">
                        <input type="checkbox" id="mlm-add-to-playlist">
                        <span id="mlm-add-to-playlist-label">Also add to server playlist?</span>
                    </label>
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
  _mlmUnmatchedCache = null;
  _mlmUpdateSaveBtn();
  void _mlmLoadMatches();

  if (prefill) {
    const src = document.getElementById('mlm-source-search') as HTMLInputElement | null;
    if (src) {
      src.value = prefill;
      void _mlmSourceSearch(prefill);
    }
  } else {
    // #1289: pre-populate the source panel with every unmatched wanted
    // track instead of an empty "type to search" box.
    void _mlmLoadUnmatched();
  }
}

export function _mlmClose(): void {
  if (_mlmOverlay) {
    _mlmOverlay.remove();
    _mlmOverlay = null;
  }
  _mlmSelectedSource = null;
  _mlmSelectedLibrary = null;
  _mlmUnmatchedCache = null;
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
    // #1289: clearing the box restores the pre-populated worklist.
    if (_mlmUnmatchedCache) {
      _mlmRenderSourceResults(_mlmUnmatchedCache);
    } else {
      el.innerHTML = '<p class="mlm-hint">Type to search</p>';
    }
    return;
  }
  el.innerHTML = '<p class="mlm-hint">Searching&hellip;</p>';
  try {
    const res = await fetch(
      `/api/manual-library-matches/source-search?q=${encodeURIComponent(q)}&limit=15`,
    );
    const data = (await res.json()) as { tracks?: MlmSourceTrack[] };
    _mlmRenderSourceResults(data.tracks || []);
  } catch {
    el.innerHTML = '<p class="mlm-hint mlm-error">Search failed</p>';
  }
}

// #1289: fetch every unmatched wanted track once and render it into the
// source panel with the existing row renderer. Never clobbers a search the
// user started while the fetch was in flight.
async function _mlmLoadUnmatched(): Promise<void> {
  const el = document.getElementById('mlm-source-results') as MlmResultsEl | null;
  if (!el || !_mlmOverlay) return;
  const input = document.getElementById('mlm-source-search') as HTMLInputElement | null;
  if (input && input.value.trim()) return;
  el.innerHTML = '<p class="mlm-hint">Loading unmatched tracks&hellip;</p>';
  try {
    const res = await fetch('/api/manual-library-matches/unmatched?limit=200');
    const data = (await res.json()) as { tracks?: MlmSourceTrack[] };
    _mlmUnmatchedCache = data.tracks || [];
    if (!_mlmOverlay) return;
    const cur = document.getElementById('mlm-source-search') as HTMLInputElement | null;
    if (cur && cur.value.trim()) return;
    _mlmRenderSourceResults(_mlmUnmatchedCache);
  } catch {
    if (!_mlmOverlay) return;
    const cur = document.getElementById('mlm-source-search') as HTMLInputElement | null;
    if (cur && cur.value.trim()) return;
    el.innerHTML = '<p class="mlm-hint mlm-error">Could not load unmatched tracks</p>';
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
    const res = await fetch(
      `/api/manual-library-matches/library-search?q=${encodeURIComponent(q)}&limit=15`,
    );
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
  el.innerHTML = tracks
    .map((t, i) => {
      const sel =
        _mlmSelectedSource && _mlmSelectedSource.source_track_id === t.source_track_id
          ? 'mlm-row-selected'
          : '';
      return `<div class="mlm-result-row ${sel}" data-idx="${i}" onclick="_mlmSelectSource(${i})">
            <div class="mlm-row-title">${_mlmEsc(t.title || '—')}</div>
            <div class="mlm-row-sub">${_mlmEsc(t.artist || '')}${t.album ? ' · ' + _mlmEsc(t.album) : ''}</div>
            <div class="mlm-row-ctx">${_mlmEsc(t.context || t.source || '')}</div>
        </div>`;
    })
    .join('');
  el._mlmTracks = tracks as MlmResultsEl['_mlmTracks'];
}

function _mlmRenderLibraryResults(tracks: MlmLibraryTrack[]): void {
  const el = document.getElementById('mlm-library-results') as MlmResultsEl | null;
  if (!el) return;
  if (!tracks.length) {
    el.innerHTML = '<p class="mlm-hint">No results</p>';
    return;
  }
  el.innerHTML = tracks
    .map((t, i) => {
      const sel = _mlmSelectedLibrary && _mlmSelectedLibrary.id === t.id ? 'mlm-row-selected' : '';
      const path = t.file_path ? t.file_path.split(/[/\\]/).pop() : '';
      return `<div class="mlm-result-row ${sel}" data-idx="${i}" onclick="_mlmSelectLibrary(${i})">
            <div class="mlm-row-title">${_mlmEsc(t.title || '—')}</div>
            <div class="mlm-row-sub">${_mlmEsc(t.artist_name || '')}${t.album_title ? ' · ' + _mlmEsc(t.album_title) : ''}</div>
            <div class="mlm-row-ctx">${_mlmEsc(path)}${t.bitrate ? ' · ' + t.bitrate + 'kbps' : ''}</div>
        </div>`;
    })
    .join('');
  el._mlmTracks = tracks as MlmResultsEl['_mlmTracks'];
}

export function _mlmSelectSource(idx: number): void {
  const el = document.getElementById('mlm-source-results') as MlmResultsEl | null;
  if (!el || !el._mlmTracks) return;
  _mlmSelectedSource = el._mlmTracks[idx];
  el.querySelectorAll('.mlm-result-row').forEach((r, i) =>
    r.classList.toggle('mlm-row-selected', i === idx),
  );
  _mlmUpdateSaveBtn();
  _mlmUpdatePlaylistCheckbox();
}

// #1289: show "also add to playlist" when the source came from a mirrored
// playlist (context is a playlist name, not "Wishlist"). The backend resolves
// the name through the stored server link (item 6) when available, so renames
// don't break it.
//
// The checkbox is only shown when the SELECTED LIBRARY TRACK exists on a
// media server (has a server_source). A local download's DB id is an
// auto-increment integer, not a server ratingKey — sending it to the
// server's add-track endpoint would resolve to an unrelated item.
function _mlmUpdatePlaylistCheckbox(): void {
  const wrap = document.getElementById('mlm-add-to-playlist-wrap');
  const label = document.getElementById('mlm-add-to-playlist-label');
  const box = document.getElementById('mlm-add-to-playlist') as HTMLInputElement | null;
  if (!wrap || !label || !box) return;
  const ctx = (_mlmSelectedSource?.context || '').trim();
  const isPlaylist = ctx !== '' && ctx.toLowerCase() !== 'wishlist';
  const isServerTrack = !!_mlmSelectedLibrary?.server_source;
  if (isPlaylist && isServerTrack) {
    label.textContent = `Also add to server playlist "${ctx}"?`;
    wrap.style.display = '';
  } else {
    wrap.style.display = 'none';
    box.checked = false;
  }
}

export function _mlmSelectLibrary(idx: number): void {
  const el = document.getElementById('mlm-library-results') as MlmResultsEl | null;
  if (!el || !el._mlmTracks) return;
  _mlmSelectedLibrary = el._mlmTracks[idx] as MlmLibraryTrack;
  el.querySelectorAll('.mlm-result-row').forEach((r, i) =>
    r.classList.toggle('mlm-row-selected', i === idx),
  );
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
      // #1289: "also add to playlist" — push the library track into the
      // server playlist the source came from (by name; the match above is
      // the durable link, this is the visible playlist edit).
      const _addBox = document.getElementById('mlm-add-to-playlist') as HTMLInputElement | null;
      const _plName = (_mlmSelectedSource.context || '').trim();
      if (_addBox?.checked && _plName && _plName.toLowerCase() !== 'wishlist') {
        try {
          const _addRes = await fetch('/api/server/playlist/0/add-track', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              // a catalogue id: the server translates it to its own id
              track_id: String(_mlmSelectedLibrary.id),
              catalogue_track_id: String(_mlmSelectedLibrary.id),
              playlist_name: _plName,
              source_track_id: String(_mlmSelectedSource.source_track_id || ''),
              source_title: _mlmSelectedSource.title || '',
              source_artist: _mlmSelectedSource.artist || '',
              source: _mlmSelectedSource.source || 'spotify',
            }),
          });
          const _addData = (await _addRes.json()) as {
            success?: boolean;
            error?: string;
          };
          if (!_addData.success) {
            if (status)
              status.textContent =
                'Match saved, but playlist add failed: ' + (_addData.error || 'unknown');
          } else if (status) {
            status.textContent = `Saved + added to "${_plName}"!`;
          }
        } catch {
          if (status) status.textContent = 'Match saved, but playlist add failed (network)';
        }
      }
      if (
        status &&
        !status.textContent.startsWith('Match saved') &&
        !status.textContent.includes('added to')
      )
        status.textContent = 'Saved!';
      _mlmSelectedSource = null;
      _mlmSelectedLibrary = null;
      _mlmUpdateSaveBtn();
      await _mlmLoadMatches();
      // The just-matched track is no longer unmatched: refresh the worklist
      // (no-op while the user is mid-search).
      void _mlmLoadUnmatched();
      // #1289: the save stamped mirrored in_library flags server-side; tell
      // the sync page to refetch its card counts. No-op when the sync page
      // isn't mounted (tool opened from elsewhere).
      window.reloadMirroredTab?.();
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
        id: number;
        source: string;
        source_track_id: string | number;
        source_title?: string;
        source_artist?: string;
        library_track_id: number;
        library_title?: string;
        library_artist?: string;
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
            <tbody>${matches
              .map(
                (m) => `<tr>
                <td><div class="mlm-row-title">${_mlmEsc(m.source_title || m.source_track_id)}</div><div class="mlm-row-sub">${_mlmEsc(m.source_artist || '')}</div></td>
                <td><div class="mlm-row-title">${_mlmEsc(m.library_title || String(m.library_track_id))}</div><div class="mlm-row-sub">${_mlmEsc(m.library_artist || '')}</div></td>
                <td><span class="mlm-source-badge">${_mlmEsc(m.source)}</span></td>
                <td><button class="mlm-remove-btn" onclick="_mlmDeleteMatch(${m.id})" title="Remove match">&#x2715;</button></td>
            </tr>`,
              )
              .join('')}</tbody>
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
    const res = await fetch(`/api/manual-library-matches/${id}`, {
      method: 'DELETE',
    });
    let data: { success?: boolean; error?: string } = {};
    try {
      data = (await res.json()) as typeof data;
    } catch {
      /* non-JSON error page */
    }
    const deleted = res.ok && data.success !== false;
    if (!deleted) {
      window.showToast?.(data.error || 'Could not remove that match', 'error');
    }
    await _mlmLoadMatches();
    // #1289: a successful delete reset mirrored in_library flags server-side;
    // tell the sync page to refetch its card counts. No-op when the sync page
    // isn't mounted (tool opened from elsewhere). Skipped on failure — nothing
    // changed, so a refetch would only flash the list for no reason (#1138).
    if (deleted) window.reloadMirroredTab?.();
    // The un-matched track may belong in the worklist again (no-op mid-search).
    if (deleted) void _mlmLoadUnmatched();
  } catch {
    window.showToast?.('Failed to remove match', 'error');
  }
}
