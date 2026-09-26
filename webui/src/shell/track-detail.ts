/**
 * Track Detail modal - ported from webui/static/track-detail.js.
 *
 * Opens from any track row in the download modal and shows a rich,
 * status-aware view: cover, title/artist/album, play/listen, source, quality,
 * AcoustID verdict, file location, expected-vs-downloaded, and the right
 * actions for the row's state (Accept/Search for quarantined, Search for
 * failed). Backed by /api/downloads/task/<id>/detail.
 *
 * showToast / showConfirmDialog / showCandidatesModal come from downloads.js
 * (still classic) - reached through window at call time.
 */

import type { CandidateDecision } from '../features/downloads/decisions';

import { decisionLabel, decisionPill, rejectionSummary } from '../features/downloads/decisions';
import { escapeHtml } from './html';

declare global {
  /* eslint-disable no-var */
  var showConfirmDialog:
    | ((opts: {
        title: string;
        message: string;
        confirmText?: string;
        cancelText?: string;
      }) => Promise<boolean>)
    | undefined;
  var showCandidatesModal: (taskId: string) => void;
  /* eslint-enable no-var */
}

const _TD_STATUS: Record<string, { label: string; cls: string }> = {
  completed: { label: 'Completed', cls: 'td-badge-ok' },
  quarantined: { label: 'Quarantined', cls: 'td-badge-warn' },
  failed: { label: 'Failed', cls: 'td-badge-bad' },
  not_found: { label: 'Not Found', cls: 'td-badge-muted' },
  in_progress: { label: 'In Progress', cls: 'td-badge-info' },
};

const _TD_ACOUSTID: Record<string, { label: string; cls: string }> = {
  pass: { label: 'Verified', cls: 'td-aid-ok' },
  fail: { label: 'Failed', cls: 'td-aid-bad' },
  error: { label: 'Error', cls: 'td-aid-bad' },
  skip: { label: 'No match', cls: 'td-aid-muted' },
  disabled: { label: 'Off', cls: 'td-aid-muted' },
};

interface TrackDetailPayload {
  status_kind?: string;
  title?: string;
  artist?: string;
  album?: string;
  thumb_url?: string;
  source?: string;
  quality?: string;
  file_path?: string;
  acoustid_result?: string;
  reason?: string;
  quarantine_entry_id?: string | number;
  expected?: { title?: string; artist?: string };
  downloaded?: { title?: string; artist?: string };
  decision?: DecisionBlock | null;
}

/** A stored candidate, as core/downloads/candidate_pool.py summarizes it. */
export interface DecisionCandidate {
  username?: string;
  display_name?: string;
  source_service?: string;
  quality?: string;
  quality_label?: string;
  confidence?: number;
  duration?: number;
  decision?: CandidateDecision;
}

export interface DecisionBlock {
  outcome: string;
  chosen?: DecisionCandidate | null;
  alternatives?: DecisionCandidate[];
  accepted_total?: number;
  rejected_total?: number;
  rejected_counts?: Record<string, number>;
}

function _tdEsc(s: unknown): string {
  return escapeHtml(s == null ? '' : s);
}

function _tdSetText(id: string, value: unknown, fallback = '—'): void {
  const el = document.getElementById(id);
  if (el) el.textContent = value && String(value).trim() ? String(value) : fallback;
}

// Release the preview <audio> so the OS file handle is freed before any move
// (Windows locks an open file - same reason the quarantine chooser does this).
function _tdReleaseAudio(): Promise<void> {
  const a = document.getElementById('td-audio') as HTMLAudioElement | null;
  if (!a) return Promise.resolve();
  try {
    a.pause();
    a.removeAttribute('src');
    a.load();
  } catch {
    /* ignore */
  }
  return new Promise((r) => setTimeout(r, 400));
}

export function closeTrackDetail(): void {
  const o = document.getElementById('track-detail-overlay');
  if (!o) return;
  const a = document.getElementById('td-audio') as HTMLAudioElement | null;
  if (a) {
    try {
      a.pause();
      a.removeAttribute('src');
      a.load();
    } catch {
      /* ignore */
    }
  }
  o.classList.remove('visible');
  o.setAttribute('aria-hidden', 'true');
}

export async function openTrackDetail(taskId: string): Promise<void> {
  if (!taskId) return;
  const overlay = document.getElementById('track-detail-overlay');
  if (!overlay) {
    console.warn('track-detail modal not present');
    return;
  }

  let detail: TrackDetailPayload;
  try {
    const resp = await fetch(`/api/downloads/task/${encodeURIComponent(taskId)}/detail`);
    const data = (await resp.json()) as {
      success?: boolean;
      error?: string;
      detail?: TrackDetailPayload;
    };
    if (!data.success) {
      window.showToast?.(data.error || 'Could not load track detail', 'error');
      return;
    }
    detail = data.detail!;
  } catch (err) {
    window.showToast?.(`Could not load track detail: ${(err as Error).message}`, 'error');
    return;
  }

  _tdRender(detail, taskId);
  overlay.classList.add('visible');
  overlay.setAttribute('aria-hidden', 'false');
}

function _tdRender(d: TrackDetailPayload, taskId: string): void {
  const kind = d.status_kind || 'in_progress';

  // Header
  _tdSetText('td-title', d.title, 'Unknown Track');
  _tdSetText('td-artist', d.artist, '');
  _tdSetText('td-album', d.album, '');

  const thumb = document.getElementById('td-thumb') as HTMLImageElement | null;
  const thumbPh = document.getElementById('td-thumb-ph');
  if (thumb && thumbPh) {
    if (d.thumb_url && /^https?:\/\//.test(d.thumb_url)) {
      thumb.src = d.thumb_url;
      thumb.hidden = false;
      thumbPh.hidden = true;
      thumb.onerror = () => {
        thumb.hidden = true;
        thumbPh.hidden = false;
      };
    } else {
      thumb.hidden = true;
      thumbPh.hidden = false;
    }
  }

  const badge = document.getElementById('td-status-badge');
  if (badge) {
    const s = _TD_STATUS[kind] || _TD_STATUS.in_progress;
    badge.textContent = s.label;
    badge.className = `td-status-badge ${s.cls}`;
  }

  // Info grid
  _tdSetText('td-f-source', d.source);
  _tdSetText('td-f-quality', d.quality);
  _tdSetText('td-f-location', d.file_path);
  const aidEl = document.getElementById('td-f-acoustid');
  if (aidEl) {
    const a = d.acoustid_result ? _TD_ACOUSTID[d.acoustid_result] : undefined;
    aidEl.textContent = a ? a.label : '—';
    aidEl.className = `td-value ${a ? a.cls : ''}`;
  }

  // Expected vs downloaded (only when we have provenance)
  const prov = document.getElementById('td-provenance');
  const exp = d.expected && (d.expected.title || d.expected.artist);
  const dl = d.downloaded && (d.downloaded.title || d.downloaded.artist);
  if (prov) {
    if (exp || dl) {
      prov.hidden = false;
      _tdSetText(
        'td-exp',
        exp ? `${d.expected!.title}${d.expected!.artist ? ' — ' + d.expected!.artist : ''}` : '',
        '—',
      );
      _tdSetText(
        'td-dl',
        dl
          ? `${d.downloaded!.title}${d.downloaded!.artist ? ' — ' + d.downloaded!.artist : ''}`
          : '',
        '—',
      );
    } else {
      prov.hidden = true;
    }
  }

  renderDecisionBlock(d.decision ?? null);

  // Reason banner (quarantined / failed)
  const reason = document.getElementById('td-reason');
  if (reason) {
    if ((kind === 'quarantined' || kind === 'failed') && d.reason) {
      reason.hidden = false;
      reason.innerHTML = `<strong>${kind === 'quarantined' ? 'Why it was quarantined' : 'Why it failed'}:</strong><br>${_tdEsc(d.reason)}`;
    } else {
      reason.hidden = true;
    }
  }

  // Audio: completed -> library stream; quarantined -> quarantine stream.
  const audio = document.getElementById('td-audio') as HTMLAudioElement | null;
  if (audio) {
    let src = '';
    if (kind === 'completed' && d.file_path) {
      src = `/stream/library-audio?path=${encodeURIComponent(d.file_path)}`;
    } else if (kind === 'quarantined' && d.quarantine_entry_id) {
      src = `/api/quarantine/${encodeURIComponent(d.quarantine_entry_id)}/stream`;
    }
    if (src) {
      audio.src = src;
      audio.hidden = false;
    } else {
      audio.removeAttribute('src');
      audio.hidden = true;
    }
  }

  _tdRenderActions(d, taskId, kind);
}

const SOURCE_NAMES: Record<string, string> = {
  soulseek: 'Soulseek',
  youtube: 'YouTube',
  tidal: 'Tidal',
  qobuz: 'Qobuz',
  hifi: 'HiFi',
  deezer_dl: 'Deezer',
  lidarr: 'Lidarr',
  amazon: 'Amazon Music',
  soundcloud: 'SoundCloud',
  torrent: 'Torrent',
  usenet: 'Usenet',
};

function _el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  cls: string,
  text?: string,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  node.className = cls;
  if (text != null) node.textContent = text;
  return node;
}

function _candidateLine(c: DecisionCandidate): string {
  const bits = [SOURCE_NAMES[c.source_service || ''] || c.source_service || ''];
  if (c.source_service === 'soulseek' && c.username) bits.push(c.username);
  if (c.quality_label || c.quality) bits.push(String(c.quality_label || c.quality));
  const score = c.decision?.score ?? c.confidence;
  if (score != null) bits.push(`match ${Math.round(Number(score) * 100)}%`);
  return bits.filter(Boolean).join(' · ');
}

/**
 * "Why this file": what won and why, what came next, what got turned away.
 * Built from DOM nodes, never innerHTML: file names come from other people.
 */
export function renderDecisionBlock(decision: DecisionBlock | null): void {
  const box = document.getElementById('td-decision');
  if (!box) return;
  box.replaceChildren();
  if (!decision || !decision.outcome) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  const counts = decision.rejected_counts || {};
  const rejected = decision.rejected_total || 0;
  const accepted = decision.accepted_total || 0;

  if (decision.outcome === 'chosen') {
    box.append(_el('h3', 'td-decision-title', 'Why this file'));
    const chosen = decision.chosen;
    if (chosen) {
      const win = _el('div', 'td-decision-win');
      win.append(
        _el('span', 'td-decision-name', chosen.display_name || ''),
        _el('span', 'td-decision-meta', _candidateLine(chosen)),
      );
      box.append(win);
    }
    const runnersUp = Math.max(0, accepted - 1);
    const parts = [];
    if (runnersUp) parts.push(`${runnersUp} other${runnersUp === 1 ? '' : 's'} also passed`);
    if (rejected) parts.push(`${rejected} passed over: ${rejectionSummary(counts)}`);
    box.append(
      _el(
        'p',
        'td-decision-summary',
        parts.length ? `${parts.join('. ')}.` : 'It was the only match.',
      ),
    );
  } else {
    box.append(
      _el(
        'h3',
        'td-decision-title',
        decision.outcome === 'nothing_passed' ? 'Why nothing was downloaded' : 'Why it stopped',
      ),
    );
    const text =
      decision.outcome === 'nothing_passed'
        ? rejected
          ? `Nothing passed: ${rejectionSummary(counts)}.`
          : 'The search came back empty.'
        : `${accepted} passed the checks, but none of them would start downloading.` +
          (rejected ? ` Passed over: ${rejectionSummary(counts)}.` : '');
    box.append(_el('p', 'td-decision-summary', text));
  }

  const alternatives = (decision.alternatives || []).slice(0, 5);
  if (alternatives.length) {
    box.append(
      _el('div', 'td-decision-subtitle', decision.chosen ? 'Next in line' : 'Closest results'),
    );
    const list = _el('ul', 'td-decision-list');
    for (const alt of alternatives) {
      const item = _el('li', 'td-decision-item');
      const text = _el('div', 'td-decision-text');
      text.append(
        _el('span', 'td-decision-name', alt.display_name || ''),
        _el('span', 'td-decision-meta', _candidateLine(alt)),
      );
      const d = alt.decision;
      const pill = _el(
        'span',
        `td-decision-pill stage-${d && !d.accepted ? d.stage || 'decision' : 'ok'}`,
        d && !d.accepted ? decisionPill(alt) || decisionLabel(d.code) : 'passed',
      );
      if (d?.detail) pill.title = d.detail;
      item.append(text, pill);
      list.append(item);
    }
    box.append(list);
  }
}

function _tdRenderActions(d: TrackDetailPayload, taskId: string, kind: string): void {
  const el = document.getElementById('td-actions');
  if (!el) return;
  el.innerHTML = '';
  const add = (label: string, cls: string, onClick: (e: MouseEvent) => void) => {
    const b = document.createElement('button');
    b.className = `td-action-btn ${cls}`;
    b.textContent = label;
    b.addEventListener('click', onClick);
    el.appendChild(b);
    return b;
  };

  if (kind === 'quarantined') {
    add(
      '✓ Accept & Import',
      'td-action-primary',
      (e) => void _tdAccept(e.currentTarget as HTMLButtonElement, d.quarantine_entry_id, taskId),
    );
    add('🔍 Search for a different result', 'td-action-secondary', () => {
      closeTrackDetail();
      if (taskId) showCandidatesModal(taskId);
    });
  } else if (kind === 'failed' || kind === 'not_found') {
    // The candidate inspector: every source searched for this track, with why
    // each hit would or wouldn't be taken. Lives in the React app.
    if (window.openDownloadTaskInspector) {
      add('🧭 See what every source has', 'td-action-secondary', () => {
        closeTrackDetail();
        window.openDownloadTaskInspector?.(taskId, {
          name: d.title,
          artist: d.artist,
          album: d.album,
        });
      });
    }
    add('🔍 Search for a different result', 'td-action-secondary', () => {
      closeTrackDetail();
      if (taskId) showCandidatesModal(taskId);
    });
  }
  // completed / in_progress: no destructive actions - the player + info is it.
}

async function _tdAccept(
  button: HTMLButtonElement,
  entryId: string | number | undefined,
  taskId: string,
): Promise<void> {
  if (!entryId) {
    window.showToast?.('Cannot accept — missing quarantine id.', 'error');
    return;
  }
  const confirmed = await showConfirmDialog?.({
    title: 'Accept Quarantined File',
    message: 'Import this file and skip the quarantine checks for this approved pass?',
    confirmText: 'Accept & Import',
    cancelText: 'Cancel',
  });
  if (!confirmed) return;
  const original = button.textContent;
  button.disabled = true;
  button.textContent = 'Importing…';
  await _tdReleaseAudio(); // free the file handle before the move (Windows lock)
  try {
    const resp = await fetch(`/api/quarantine/${encodeURIComponent(entryId)}/approve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task_id: taskId || '' }),
    });
    const data = (await resp.json()) as { success?: boolean; error?: string };
    if (data.success) {
      window.showToast?.('Accepted. Re-running post-processing.', 'success');
      closeTrackDetail();
      return;
    }
    const needsRecover =
      /thin sidecar|recover to staging|embedded context|missing file or sidecar/i.test(
        data.error || '',
      );
    if (needsRecover) {
      button.textContent = 'Recovering…';
      const rec = await fetch(`/api/quarantine/${encodeURIComponent(entryId)}/recover`, {
        method: 'POST',
      });
      const recData = (await rec.json()) as { success?: boolean; error?: string };
      if (recData.success) {
        window.showToast?.(
          'Older entry — moved to Staging. Finish it from the Import page.',
          'success',
        );
        closeTrackDetail();
        return;
      }
      window.showToast?.(`Recover failed: ${recData.error || 'Unknown error'}`, 'error');
    } else {
      window.showToast?.(`Accept failed: ${data.error || 'Unknown error'}`, 'error');
    }
  } catch (err) {
    window.showToast?.(`Accept failed: ${(err as Error).message}`, 'error');
  }
  button.disabled = false;
  button.textContent = original;
}

// Close on backdrop click + Escape.
document.addEventListener('DOMContentLoaded', () => {
  const overlay = document.getElementById('track-detail-overlay');
  if (overlay) {
    overlay.addEventListener('mousedown', (e) => {
      if (e.target === overlay) closeTrackDetail();
    });
  }
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    const o = document.getElementById('track-detail-overlay');
    if (o && o.classList.contains('visible')) closeTrackDetail();
  }
});
