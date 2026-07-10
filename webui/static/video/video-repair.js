/*
 * SoulSync — Video Library Maintenance (Tools page).
 *
 * The music repair UI, video-scoped: master toggle, Jobs / Findings / History
 * tabs, finding cards with approve/dismiss + bulk actions, live per-job
 * progress from the 'video:repair:progress' socket event. Reuses the music
 * hero's .repair-* CSS classes (shared stylesheet) against /api/video/repair.
 * Exposes window.updateVideoRepairProgressFromData for core.js's socket hook.
 */
(function () {
    'use strict';

    var API = '/api/video/repair';
    var state = { tab: 'jobs', page: 1, selected: {}, jobs: [], loadedOnce: false };

    function $(id) { return document.getElementById(id); }
    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function toast(msg, type) { if (typeof showToast === 'function') showToast(msg, type); }
    function confirmDlg(o) {
        return (typeof showConfirmDialog === 'function') ? showConfirmDialog(o) : Promise.resolve(true);
    }
    function jget(url) { return fetch(url).then(function (r) { return r.ok ? r.json() : null; }); }
    function jpost(url, body, method) {
        return fetch(url, { method: method || 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body || {}) })
            .then(function (r) { return r.json().then(function (b) { return { ok: r.ok, body: b }; }); });
    }

    // Per-finding-type presentation (label, approve-button text) — the video
    // sibling of the music typeLabels/fixableTypes maps.
    var TYPES = {
        missing_episodes: { label: 'Missing episodes', fixText: 'Send to Wishlist' },
    };

    // ── master toggle + tabs ─────────────────────────────────────────────────
    function loadStatus() {
        jget(API + '/status').then(function (s) {
            if (!s) return;
            var t = $('video-repair-master-toggle');
            if (t) t.checked = !!s.enabled;
            var l = $('video-repair-master-label');
            if (l) l.textContent = s.enabled ? 'Enabled' : 'Disabled';
            badge(s.findings_pending);
        });
    }

    function badge(n) {
        var b = $('video-repair-findings-tab-badge');
        if (!b) return;
        b.textContent = n;
        b.style.display = n > 0 ? '' : 'none';
    }

    function switchTab(tab) {
        state.tab = tab;
        var tabs = document.querySelectorAll('[data-video-repair-tab]');
        for (var i = 0; i < tabs.length; i++) {
            tabs[i].classList.toggle('active', tabs[i].getAttribute('data-video-repair-tab') === tab);
        }
        ['jobs', 'findings', 'history'].forEach(function (t) {
            var el = $('video-repair-tab-' + t);
            if (el) el.style.display = (t === tab) ? '' : 'none';
        });
        if (tab === 'jobs') loadJobs();
        else if (tab === 'findings') { state.page = 1; loadFindings(); }
        else loadHistory();
    }

    // ── jobs ─────────────────────────────────────────────────────────────────
    function loadJobs() {
        jget(API + '/jobs').then(function (d) {
            var host = $('video-repair-jobs-list');
            if (!host || !d) return;
            state.jobs = d.jobs || [];
            host.innerHTML = state.jobs.map(jobCardHTML).join('') ||
                '<div class="repair-loading">No jobs registered</div>';
            fillJobFilter();
        });
    }

    function jobCardHTML(j) {
        var last = j.last_run;
        var lastTxt = 'Never';
        if (last && last.finished_at) {
            lastTxt = esc(String(last.finished_at).replace('T', ' ')) +
                ' · ' + (last.items_scanned || 0) + ' scanned, ' +
                (last.findings_created || 0) + ' findings';
        }
        var settings = Object.keys(j.setting_options || {}).map(function (k) {
            var opts = j.setting_options[k].map(function (o) {
                var sel = String(j.settings[k]) === String(o) ? ' selected' : '';
                return '<option value="' + esc(o) + '"' + sel + '>' +
                    esc(o === true ? 'Yes' : o === false ? 'No' : o) + '</option>';
            }).join('');
            return '<label class="repair-job-setting"><span>' + esc(k.replace(/_/g, ' ')) +
                '</span><select data-vjr-setting="' + esc(k) + '">' + opts + '</select></label>';
        }).join('');
        return '' +
            '<div class="repair-job-card" data-vjr-job="' + esc(j.job_id) + '">' +
                '<div class="repair-job-head">' +
                    '<span class="repair-job-dot' + (j.is_running ? ' repair-job-dot--on' : '') + '"></span>' +
                    '<span class="repair-job-icon">' + esc(j.icon) + '</span>' +
                    '<div class="repair-job-titles">' +
                        '<div class="repair-job-name">' + esc(j.display_name) +
                            (j.pending_findings_count ? ' <span class="repair-tab-badge">' +
                                j.pending_findings_count + '</span>' : '') + '</div>' +
                        '<div class="repair-job-desc" title="' + esc(j.help_text) + '">' +
                            esc(j.description) + '</div>' +
                    '</div>' +
                    '<label class="repair-master-toggle repair-master-toggle--sm" title="Run on a schedule">' +
                        '<input type="checkbox" data-vjr-enable' + (j.enabled ? ' checked' : '') + '>' +
                        '<span class="repair-toggle-slider"></span>' +
                    '</label>' +
                '</div>' +
                '<div class="repair-job-meta">' +
                    '<span>Last: ' + lastTxt + '</span>' +
                    '<label>Every <input type="number" min="1" max="720" data-vjr-interval value="' +
                        (j.interval_hours || 24) + '"> h</label>' +
                    settings +
                    '<span class="repair-job-actions">' +
                        (j.is_running
                            ? '<button class="btn btn--sm btn--secondary" type="button" data-vjr-stop>Stop</button>'
                            : '<button class="btn btn--sm btn--primary" type="button" data-vjr-run>Run Now</button>') +
                    '</span>' +
                '</div>' +
                '<div class="repair-job-progress" data-vjr-progress style="display:none">' +
                    '<div class="progress-bar-container"><div class="progress-bar-fill" data-vjr-bar style="width:0%"></div></div>' +
                    '<p class="progress-details-label" data-vjr-phase></p>' +
                '</div>' +
            '</div>';
    }

    function fillJobFilter() {
        var sel = $('video-repair-findings-job-filter');
        if (!sel) return;
        var cur = sel.value;
        sel.innerHTML = '<option value="">All Jobs</option>' + state.jobs.map(function (j) {
            return '<option value="' + esc(j.job_id) + '">' + esc(j.display_name) + '</option>';
        }).join('');
        sel.value = cur;
    }

    // ── live progress (socket 'video:repair:progress' via core.js) ──────────
    function updateProgress(data) {
        if (!data) return;
        var running = false;
        Object.keys(data).forEach(function (jobId) {
            var st = data[jobId];
            if (st.status === 'running') running = true;
            var card = document.querySelector('[data-vjr-job="' + jobId + '"]');
            if (!card) return;
            var dot = card.querySelector('.repair-job-dot');
            if (dot) dot.classList.toggle('repair-job-dot--on', st.status === 'running');
            var panel = card.querySelector('[data-vjr-progress]');
            if (!panel) return;
            if (st.status === 'running') {
                panel.style.display = '';
                var bar = panel.querySelector('[data-vjr-bar]');
                if (bar) bar.style.width = (st.progress || 0) + '%';
                var ph = panel.querySelector('[data-vjr-phase]');
                if (ph) {
                    ph.textContent = (st.phase || '') +
                        (st.total ? ' · ' + st.processed + '/' + st.total : '') +
                        (st.current_item ? ' · ' + st.current_item : '');
                }
            } else if (panel.style.display !== 'none') {
                panel.style.display = 'none';
                loadJobs();            // repaint the finished card (last-run line)
                loadStatus();          // pending badge may have grown
                if (state.tab === 'findings') loadFindings();
            }
        });
        return running;
    }
    window.updateVideoRepairProgressFromData = updateProgress;

    // ── findings ─────────────────────────────────────────────────────────────
    function filters() {
        return {
            job_id: ($('video-repair-findings-job-filter') || {}).value || '',
            severity: ($('video-repair-findings-severity-filter') || {}).value || '',
            status: ($('video-repair-findings-status-filter') || {}).value || '',
        };
    }

    function loadFindings() {
        var f = filters();
        var q = new URLSearchParams({ page: state.page, limit: 25 });
        if (f.job_id) q.set('job_id', f.job_id);
        if (f.severity) q.set('severity', f.severity);
        if (f.status) q.set('status', f.status);
        jget(API + '/findings?' + q.toString()).then(function (d) {
            var host = $('video-repair-findings-list');
            if (!host || !d) return;
            state.selected = {};
            paintBulk();
            var cb = $('video-repair-select-all-cb');
            if (cb) cb.checked = false;
            host.innerHTML = (d.items || []).map(findingCardHTML).join('') ||
                '<div class="repair-loading">Nothing here — run a job from the Jobs tab.</div>';
            paginate(d);
        });
        jget(API + '/findings/counts').then(function (c) { if (c) badge(c.pending); });
    }

    function sevIcon(sev) {
        return sev === 'critical' ? '🔴' : sev === 'warning' ? '🟠' : '🔵';
    }

    function findingCardHTML(f) {
        var t = TYPES[f.finding_type] || { label: f.finding_type, fixText: 'Approve' };
        var pending = f.status === 'pending';
        var statusBadge = pending ? '' :
            '<span class="repair-finding-status repair-finding-status--' + esc(f.status) + '">' +
                esc(f.user_action || f.status) + '</span>';
        return '' +
            '<div class="repair-finding-card" data-vjr-finding="' + f.id + '">' +
                '<div class="repair-finding-row">' +
                    (pending ? '<input type="checkbox" class="repair-finding-cb" data-vjr-check>' : '') +
                    '<span class="repair-finding-sev" title="' + esc(f.severity) + '">' +
                        sevIcon(f.severity) + '</span>' +
                    '<div class="repair-finding-main">' +
                        '<div class="repair-finding-title">' + esc(f.title) +
                            ' <span class="repair-finding-type">' + esc(t.label) + '</span>' +
                            statusBadge + '</div>' +
                        (f.description ? '<div class="repair-finding-desc">' + esc(f.description) + '</div>' : '') +
                    '</div>' +
                    '<span class="repair-finding-actions">' +
                        (pending ? '<button class="btn btn--sm btn--primary" type="button" data-vjr-fix>' +
                            esc(t.fixText) + '</button>' +
                            '<button class="btn btn--sm btn--secondary" type="button" data-vjr-dismiss title="Dismiss">×</button>'
                            : '') +
                        '<button class="btn btn--sm btn--secondary" type="button" data-vjr-expand title="Details">▾</button>' +
                    '</span>' +
                '</div>' +
                '<div class="repair-finding-detail" data-vjr-detail style="display:none">' +
                    detailHTML(f) + '</div>' +
            '</div>';
    }

    function detailHTML(f) {
        var d = f.details || {};
        if (f.finding_type === 'missing_episodes' && d.episodes) {
            var rows = d.episodes.map(function (e) {
                var code = 'S' + String(e.season_number).padStart(2, '0') +
                    'E' + String(e.episode_number).padStart(2, '0');
                return '<div class="repair-ep-row"><span class="repair-ep-code">' + esc(code) +
                    '</span><span class="repair-ep-title">' + esc(e.title || '—') + '</span>' +
                    '<span class="repair-ep-date">' + esc(e.air_date || '') + '</span></div>';
            }).join('');
            return '<div class="repair-ep-list">' + rows + '</div>' +
                '<p class="repair-finding-hint">Approving sends these to the wishlist — the ' +
                'auto-downloader takes it from there.</p>';
        }
        return '<pre class="repair-finding-json">' + esc(JSON.stringify(d, null, 2)) + '</pre>';
    }

    function paginate(d) {
        var host = $('video-repair-findings-pagination');
        if (!host) return;
        var pages = Math.max(1, Math.ceil((d.total || 0) / (d.limit || 25)));
        host.innerHTML = pages <= 1 ? '' :
            '<button class="pagination-btn" type="button" data-vjr-page="-1"' +
                (d.page <= 1 ? ' disabled' : '') + '>←</button>' +
            '<span class="pagination-info">Page ' + d.page + ' of ' + pages + '</span>' +
            '<button class="pagination-btn" type="button" data-vjr-page="1"' +
                (d.page >= pages ? ' disabled' : '') + '>→</button>';
    }

    function paintBulk() {
        var n = Object.keys(state.selected).length;
        var bar = $('video-repair-findings-bulk');
        if (bar) bar.style.display = n ? '' : 'none';
        var c = $('video-repair-bulk-count');
        if (c) c.textContent = n + ' selected';
    }

    // ── actions ──────────────────────────────────────────────────────────────
    function fixFinding(id, card) {
        jpost(API + '/findings/' + id + '/fix').then(function (r) {
            if (r.ok && r.body.success) {
                toast(r.body.message || 'Approved', 'success');
                loadFindings();
                loadStatus();
            } else {
                toast((r.body && r.body.error) || 'Fix failed', 'error');
            }
        });
    }

    function wire() {
        var root = document.querySelector('[data-video-repair]');
        if (!root || root.getAttribute('data-wired')) return;
        root.setAttribute('data-wired', '1');

        var master = $('video-repair-master-toggle');
        if (master) {
            master.addEventListener('change', function () {
                jpost(API + '/toggle', { enabled: master.checked }).then(function (r) {
                    var l = $('video-repair-master-label');
                    if (l && r.ok) l.textContent = r.body.enabled ? 'Enabled' : 'Disabled';
                });
            });
        }
        root.addEventListener('click', function (e) {
            var tab = e.target.closest('[data-video-repair-tab]');
            if (tab) { switchTab(tab.getAttribute('data-video-repair-tab')); return; }

            var jobCard = e.target.closest('[data-vjr-job]');
            if (jobCard) {
                var jobId = jobCard.getAttribute('data-vjr-job');
                if (e.target.closest('[data-vjr-run]')) {
                    jpost(API + '/jobs/' + jobId + '/run').then(function (r) {
                        toast(r.ok ? 'Job started' : 'Could not start job', r.ok ? 'success' : 'error');
                        setTimeout(loadJobs, 400);
                    });
                    return;
                }
                if (e.target.closest('[data-vjr-stop]')) {
                    jpost(API + '/jobs/' + jobId + '/stop').then(function () { setTimeout(loadJobs, 400); });
                    return;
                }
            }

            var page = e.target.closest('[data-vjr-page]');
            if (page && !page.disabled) {
                state.page += parseInt(page.getAttribute('data-vjr-page'), 10);
                loadFindings();
                return;
            }
            var card = e.target.closest('[data-vjr-finding]');
            if (card) {
                var fid = card.getAttribute('data-vjr-finding');
                if (e.target.closest('[data-vjr-fix]')) { fixFinding(fid, card); return; }
                if (e.target.closest('[data-vjr-dismiss]')) {
                    jpost(API + '/findings/' + fid + '/dismiss').then(function (r) {
                        if (r.ok) { loadFindings(); loadStatus(); }
                    });
                    return;
                }
                if (e.target.closest('[data-vjr-expand]')) {
                    var det = card.querySelector('[data-vjr-detail]');
                    if (det) det.style.display = det.style.display === 'none' ? '' : 'none';
                    return;
                }
            }
            var bulk = e.target.closest('[data-video-repair-bulk]');
            if (bulk) {
                var ids = Object.keys(state.selected).map(Number);
                if (!ids.length) return;
                if (bulk.getAttribute('data-video-repair-bulk') === 'fix') {
                    jpost(API + '/findings/bulk-fix', { ids: ids }).then(function (r) {
                        var b = r.body || {};
                        toast('Approved ' + (b.fixed || 0) + (b.failed ? ', ' + b.failed + ' failed' : ''),
                            b.failed ? 'warning' : 'success');
                        loadFindings(); loadStatus();
                    });
                } else {
                    jpost(API + '/findings/bulk', { ids: ids, action: 'dismiss' }).then(function () {
                        loadFindings(); loadStatus();
                    });
                }
                return;
            }
            if (e.target.closest('[data-video-repair-clear]')) {
                var f = filters();
                confirmDlg({
                    title: 'Clear findings?',
                    message: 'Deletes the findings matching the current filters. Cleared findings can be re-found by the next scan.',
                    confirmText: 'Clear', destructive: true,
                }).then(function (yes) {
                    if (!yes) return;
                    jpost(API + '/findings/clear',
                          { job_id: f.job_id || null, status: f.status || null })
                        .then(function (r) {
                            toast('Cleared ' + ((r.body || {}).deleted || 0) + ' findings', 'info');
                            loadFindings(); loadStatus();
                        });
                });
            }
        });
        root.addEventListener('change', function (e) {
            var jobCard = e.target.closest('[data-vjr-job]');
            if (jobCard) {
                var jobId = jobCard.getAttribute('data-vjr-job');
                if (e.target.hasAttribute('data-vjr-enable')) {
                    jpost(API + '/jobs/' + jobId + '/toggle', { enabled: e.target.checked });
                    return;
                }
                if (e.target.hasAttribute('data-vjr-interval')) {
                    jpost(API + '/jobs/' + jobId + '/settings',
                          { interval_hours: parseInt(e.target.value, 10) || 24 }, 'PUT');
                    return;
                }
                if (e.target.hasAttribute('data-vjr-setting')) {
                    var key = e.target.getAttribute('data-vjr-setting');
                    var raw = e.target.value;
                    var val = raw === 'true' ? true : raw === 'false' ? false : raw;
                    var settings = {};
                    settings[key] = val;
                    jpost(API + '/jobs/' + jobId + '/settings', { settings: settings }, 'PUT');
                    return;
                }
            }
            if (e.target.id === 'video-repair-select-all-cb') {
                var boxes = document.querySelectorAll('#video-repair-findings-list [data-vjr-check]');
                state.selected = {};
                for (var i = 0; i < boxes.length; i++) {
                    boxes[i].checked = e.target.checked;
                    if (e.target.checked) {
                        state.selected[boxes[i].closest('[data-vjr-finding]')
                            .getAttribute('data-vjr-finding')] = true;
                    }
                }
                paintBulk();
                return;
            }
            if (e.target.hasAttribute && e.target.hasAttribute('data-vjr-check')) {
                var fid2 = e.target.closest('[data-vjr-finding]').getAttribute('data-vjr-finding');
                if (e.target.checked) state.selected[fid2] = true;
                else delete state.selected[fid2];
                paintBulk();
                return;
            }
            if (e.target.id === 'video-repair-findings-job-filter' ||
                    e.target.id === 'video-repair-findings-severity-filter' ||
                    e.target.id === 'video-repair-findings-status-filter') {
                state.page = 1;
                loadFindings();
            }
        });
    }

    // ── history ──────────────────────────────────────────────────────────────
    function loadHistory() {
        jget(API + '/history?limit=50').then(function (d) {
            var host = $('video-repair-history-list');
            if (!host || !d) return;
            var names = {};
            state.jobs.forEach(function (j) { names[j.job_id] = j.display_name; });
            host.innerHTML = (d.runs || []).map(function (r) {
                return '<div class="repair-history-row">' +
                    '<span class="repair-history-job">' + esc(names[r.job_id] || r.job_id) + '</span>' +
                    '<span class="repair-history-when">' +
                        esc(String(r.started_at || '').replace('T', ' ')) + '</span>' +
                    '<span class="repair-history-stats">' + (r.items_scanned || 0) + ' scanned · ' +
                        (r.findings_created || 0) + ' findings' +
                        (r.errors ? ' · ' + r.errors + ' errors' : '') + '</span>' +
                    '<span class="repair-history-dur">' +
                        (r.duration_seconds != null ? r.duration_seconds.toFixed(1) + 's' : '…') + '</span>' +
                '</div>';
            }).join('') || '<div class="repair-loading">No runs yet</div>';
        });
    }

    // ── boot ─────────────────────────────────────────────────────────────────
    function onPageShown(e) {
        if (!e || e.detail !== 'video-tools') return;
        // Studios are admin-only — hide the section for non-admin profiles.
        var sec = document.querySelector('[data-video-studios-section]');
        if (sec && typeof currentProfile !== 'undefined' && currentProfile && !currentProfile.is_admin) {
            sec.style.display = 'none';
        }
        wire();
        loadStatus();
        loadJobs();
        // Seed live progress for a job already running before this page opened.
        jget(API + '/progress').then(function (p) { if (p) updateProgress(p); });
    }

    document.addEventListener('soulsync:video-page-shown', onPageShown);
})();
