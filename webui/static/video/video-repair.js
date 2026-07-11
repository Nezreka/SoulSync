/*
 * SoulSync — Video Library Maintenance (Tools page).
 *
 * The music repair UI, video-scoped — SAME card DOM as enrichment.js's
 * loadRepairJobs/loadRepairFindings/loadRepairHistory so the shared .repair-*
 * stylesheet renders both sides identically. Ids are video-prefixed (both
 * Tools pages share this document) and all queries are scoped to the
 * [data-video-repair] root so the two heroes never cross-talk.
 * Exposes window.updateVideoRepairProgressFromData for core.js's
 * 'video:repair:progress' socket hook.
 */
(function () {
    'use strict';

    var API = '/api/video/repair';
    var state = { page: 0, selected: {}, jobs: [] };
    var hideTimers = {};
    var logCounts = {};

    function root() { return document.querySelector('[data-video-repair]'); }
    function $(id) { return document.getElementById(id); }
    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function toast(msg, type) { if (typeof showToast === 'function') showToast(msg, type); }
    function confirmDlg(o) {
        return (typeof showConfirmDialog === 'function') ? showConfirmDialog(o) : Promise.resolve(true);
    }
    function age(ts) {
        return (typeof formatCacheAge === 'function' && ts) ? formatCacheAge(ts) : (ts || 'Never');
    }
    function pretty(key) {
        if (typeof _prettifyRepairSettingKey === 'function') return _prettifyRepairSettingKey(key);
        return String(key).replace(/^_+/, '').split('_').map(function (w) {
            return w.charAt(0).toUpperCase() + w.slice(1);
        }).join(' ');
    }
    function jget(url) { return fetch(url).then(function (r) { return r.ok ? r.json() : null; }); }
    function jsend(url, body, method) {
        return fetch(url, { method: method || 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body || {}) })
            .then(function (r) { return r.json().then(function (b) { return { ok: r.ok, body: b }; }); });
    }

    var PAGE_SIZE = 25;
    var SEVERITY_ICONS = { info: 'ℹ️', warning: '⚠️', critical: '🔴' };
    // Per-finding-type presentation — the video siblings of the music
    // typeLabels / fixableTypes / actionLabels maps (keep in sync with jobs).
    var TYPE_LABELS = {
        missing_episodes: 'Missing Episodes',
        incomplete_collection: 'Incomplete Collection',
        quality_upgrade: 'Quality Upgrade',
        broken_file: 'Broken File',
        metadata_gap: 'Metadata Gap',
        duplicate_movie: 'Duplicate',
        stale_wishlist: 'Stale Wishlist',
    };
    // Types absent here are report-only: no approve button, dismiss + details only.
    var FIXABLE_TYPES = {
        missing_episodes: 'Send to Wishlist',
        incomplete_collection: 'Add to Wishlist',
        quality_upgrade: 'Grab Upgrade',
        broken_file: 'Re-download',
        metadata_gap: 'Re-enrich',
        stale_wishlist: 'Remove',
    };
    var ACTION_LABELS = { wishlisted: 'Wishlisted', grabbed: 'Grabbed', refreshed: 'Refreshed',
        removed: 'Removed', resolved: 'Resolved' };
    var GAP_LABELS = { unmatched: 'not TMDB-matched', overview: 'no summary',
        genres: 'no genres', poster: 'no poster', backdrop: 'no backdrop' };

    // ── status + master toggle ───────────────────────────────────────────────
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
        var tabs = document.querySelectorAll('[data-video-repair-tab]');
        for (var i = 0; i < tabs.length; i++) {
            tabs[i].classList.toggle('active', tabs[i].getAttribute('data-video-repair-tab') === tab);
        }
        ['jobs', 'findings', 'history'].forEach(function (t) {
            var el = $('video-repair-tab-' + t);
            if (el) el.style.display = (t === tab) ? '' : 'none';
        });
        if (tab === 'jobs') loadJobs();
        else if (tab === 'findings') { state.page = 0; loadFindings(); }
        else loadHistory();
    }

    // ── jobs (the music card DOM, verbatim) ──────────────────────────────────
    function loadJobs() {
        var container = $('video-repair-jobs-list');
        if (!container) return;
        jget(API + '/jobs').then(function (data) {
            if (!data) { container.innerHTML = '<div class="repair-empty">Error loading jobs</div>'; return; }
            var jobs = data.jobs || [];
            state.jobs = jobs;
            if (!jobs.length) {
                container.innerHTML = '<div class="repair-empty-state">' +
                    '<div class="repair-empty-icon">🔧</div>' +
                    '<div class="repair-empty-title">No Maintenance Jobs</div>' +
                    '<div class="repair-empty-text">Library maintenance jobs will appear here once available.</div></div>';
                return;
            }
            fillJobFilter(jobs);
            container.innerHTML = jobs.map(jobCardHTML).join('');
        });
    }

    function jobCardHTML(job) {
        var lastRunText = job.last_run ? age(job.last_run.finished_at) : 'Never';
        var nextRunText = job.next_run ? esc(job.next_run) : (job.enabled ? 'Pending' : '-');
        var dotClass = job.is_running ? 'running' : (job.enabled ? 'enabled' : 'disabled');
        var cardClass = job.is_running ? 'running' : (!job.enabled ? 'disabled' : '');

        var flowParts = ['<span class="repair-flow-badge scan">' +
            (job.is_running ? '&#9654; Running' : 'Scan') + '</span>'];
        if (job.auto_fix) {
            flowParts.push('<span class="repair-flow-arrow">&rarr;</span>');
            flowParts.push('<span class="repair-flow-badge autofix">Auto-fix</span>');
        }
        var pendingCount = job.pending_findings_count || 0;
        var lastScanCount = job.last_run ? (job.last_run.findings_created || 0) : 0;
        if (pendingCount > 0) {
            flowParts.push('<span class="repair-flow-arrow">&rarr;</span>');
            flowParts.push('<span class="repair-flow-badge findings">' + pendingCount + ' pending</span>');
        } else if (lastScanCount > 0) {
            flowParts.push('<span class="repair-flow-arrow">&rarr;</span>');
            flowParts.push('<span class="repair-flow-badge findings findings-historical">' +
                lastScanCount + ' found in last scan</span>');
        }

        var metaParts = ['Last: ' + lastRunText, 'Next: ' + nextRunText];
        if (job.last_run) {
            metaParts.push('Scanned: ' + (job.last_run.items_scanned || 0).toLocaleString());
            if (job.last_run.auto_fixed) metaParts.push('Fixed: ' + job.last_run.auto_fixed);
            if (job.last_run.duration_seconds) {
                metaParts.push(job.last_run.duration_seconds.toFixed(1) + 's');
            }
        }

        // The settings panel ALWAYS exists — the interval row lives in it, so
        // even a job with no settings of its own needs the gear.
        var settingKeys = Object.keys(job.settings || {});
        var settingsRows = settingKeys.map(function (key) {
                var val = job.settings[key];
                var label = pretty(key);
                var opts = job.setting_options && job.setting_options[key];
                if (Array.isArray(opts) && opts.length) {
                    var optionsHtml = opts.map(function (o) {
                        return '<option value="' + esc(o) + '"' + (o === val ? ' selected' : '') + '>' +
                            esc(pretty(String(o))) + '</option>';
                    }).join('');
                    return '<div class="repair-setting-row"><label>' + esc(label) + '</label>' +
                        '<select class="repair-setting-input" data-job="' + esc(job.job_id) +
                        '" data-key="' + esc(key) + '">' + optionsHtml + '</select></div>';
                }
                var inputType = typeof val === 'boolean' ? 'checkbox' :
                    typeof val === 'number' ? 'number' : 'text';
                var inputVal = inputType === 'checkbox' ? (val ? ' checked' : '') :
                    ' value="' + esc(val) + '"';
                return '<div class="repair-setting-row"><label>' + esc(label) + '</label>' +
                    '<input type="' + inputType + '" class="repair-setting-input" data-job="' +
                    esc(job.job_id) + '" data-key="' + esc(key) + '"' + inputVal +
                    (inputType === 'number' ? ' step="0.01" min="0"' : '') + '></div>';
            }).join('');
            settingsHtml =
                '<div class="repair-job-settings" id="video-repair-settings-' + esc(job.job_id) +
                    '" style="display:none;">' +
                    '<div class="repair-setting-row"><label>Interval (hours)</label>' +
                        '<input type="number" class="repair-setting-input" data-job="' + esc(job.job_id) +
                        '" data-key="_interval_hours" value="' + job.interval_hours + '" min="1" step="1"></div>' +
                    settingsRows +
                    '<button class="repair-save-settings-btn" type="button" data-vjr-save="' +
                        esc(job.job_id) + '">Save Settings</button>' +
                '</div>';

        return '<div class="repair-job-card ' + cardClass + '" data-job-id="' + esc(job.job_id) + '">' +
            '<div class="repair-job-main">' +
                '<div class="repair-job-status ' + dotClass + '"></div>' +
                '<div class="repair-job-info">' +
                    '<div class="repair-job-name">' + esc(job.display_name) + '</div>' +
                    '<div class="repair-job-desc">' + esc(job.description || '') + '</div>' +
                    '<div class="repair-job-flow">' + flowParts.join('') + '</div>' +
                    '<div class="repair-job-meta">' + metaParts.join(' &middot; ') + '</div>' +
                '</div>' +
                '<div class="repair-job-actions">' +
                    '<label class="repair-job-toggle">' +
                        '<input type="checkbox" data-vjr-enable' + (job.enabled ? ' checked' : '') + '>' +
                        '<span class="repair-toggle-slider small"></span>' +
                    '</label>' +
                    (job.is_running
                        ? '<button class="repair-stop-btn" type="button" data-vjr-stop title="Stop this run">&#9209;</button>'
                        : '<button class="repair-run-btn" type="button" data-vjr-run title="Run now">&#9654;</button>') +
                    '<button class="repair-settings-btn" type="button" data-vjr-settings title="Settings">&#9881;</button>' +
                    '<button class="repair-help-btn" type="button" data-vjr-help title="About this job">?</button>' +
                '</div>' +
            '</div>' +
            settingsHtml +
        '</div>';
    }

    function fillJobFilter(jobs) {
        var sel = $('video-repair-findings-job-filter');
        if (!sel || sel.options.length > 1) return;
        jobs.forEach(function (job) {
            var opt = document.createElement('option');
            opt.value = job.job_id;
            opt.textContent = job.display_name;
            sel.appendChild(opt);
        });
    }

    function saveJobSettings(jobId) {
        var panel = $('video-repair-settings-' + jobId);
        if (!panel) return;
        var body = { settings: {} };
        panel.querySelectorAll('.repair-setting-input').forEach(function (el) {
            var key = el.getAttribute('data-key');
            var val;
            if (el.type === 'checkbox') val = el.checked;
            else if (el.type === 'number') val = parseFloat(el.value) || 0;
            else val = el.value === 'true' ? true : el.value === 'false' ? false : el.value;
            if (key === '_interval_hours') body.interval_hours = parseInt(el.value, 10) || 24;
            else body.settings[key] = val;
        });
        jsend(API + '/jobs/' + jobId + '/settings', body, 'PUT').then(function (r) {
            toast(r.ok ? 'Settings saved' : 'Could not save settings', r.ok ? 'success' : 'error');
            if (r.ok) loadJobs();
        });
    }

    // ── live progress (music's updateRepairJobProgressFromData, scoped) ──────
    function updateProgress(data) {
        if (!data || !root()) return;
        Object.keys(data).forEach(function (jobId) {
            var st = data[jobId];
            var card = root().querySelector('.repair-job-card[data-job-id="' + jobId + '"]');
            if (!card) return;

            var statusDot = card.querySelector('.repair-job-status');
            if (statusDot) {
                statusDot.className = 'repair-job-status ' +
                    (st.status === 'running' ? 'running' : 'enabled');
            }
            var firstBadge = card.querySelector('.repair-flow-badge.scan');
            if (firstBadge) {
                if (st.status === 'running') firstBadge.innerHTML = '&#9654; Running';
                else if (st.status === 'finished') firstBadge.innerHTML = '&#10003; Complete';
                else if (st.status === 'error') firstBadge.innerHTML = '&#10007; Error';
                else if (st.status === 'cancelled') firstBadge.innerHTML = 'Stopped';
            }
            card.classList.toggle('running', st.status === 'running');
            card.classList.remove('disabled');

            // The Run button IS the Stop button while a run is live (music UX).
            var runBtn = card.querySelector('[data-vjr-run]');
            var stopBtn = card.querySelector('[data-vjr-stop]');
            if (st.status === 'running' && runBtn) {
                runBtn.outerHTML = '<button class="repair-stop-btn" type="button" data-vjr-stop title="Stop this run">&#9209;</button>';
            } else if (st.status !== 'running' && stopBtn) {
                stopBtn.outerHTML = '<button class="repair-run-btn" type="button" data-vjr-run title="Run now">&#9654;</button>';
            }

            var panel = card.querySelector('.repair-job-progress');
            if (!panel) {
                panel = document.createElement('div');
                panel.className = 'repair-job-progress';
                panel.innerHTML =
                    '<div class="repair-progress-bar-wrap"><div class="repair-progress-bar" style="width:0%"></div></div>' +
                    '<div class="repair-progress-phase"></div>' +
                    '<div class="repair-progress-log"></div>';
                card.appendChild(panel);
            }
            panel.classList.add('visible');
            panel.classList.toggle('finished', st.status === 'finished');
            panel.classList.toggle('error', st.status === 'error');
            if (st.status === 'running') {
                panel.classList.remove('finished', 'error');
                if (hideTimers[jobId]) { clearTimeout(hideTimers[jobId]); delete hideTimers[jobId]; }
                if (logCounts[jobId] > 0 && st.log && st.log.length < logCounts[jobId]) {
                    var el = panel.querySelector('.repair-progress-log');
                    if (el) el.innerHTML = '';
                    logCounts[jobId] = 0;
                }
            }
            var bar = panel.querySelector('.repair-progress-bar');
            if (bar) bar.style.width = (st.progress || 0) + '%';
            var phaseEl = panel.querySelector('.repair-progress-phase');
            if (phaseEl && st.phase) {
                phaseEl.textContent = st.phase +
                    (st.total ? ' — ' + st.processed + '/' + st.total : '') +
                    (st.current_item ? ' · ' + st.current_item : '');
            }
            var logEl = panel.querySelector('.repair-progress-log');
            if (logEl && st.log) {
                var seen = logCounts[jobId] || 0;
                st.log.slice(seen).forEach(function (line) {
                    var div = document.createElement('div');
                    div.className = 'repair-log-line ' + (line.type || 'info');
                    div.textContent = line.text || '';
                    logEl.appendChild(div);
                });
                logCounts[jobId] = st.log.length;
                logEl.scrollTop = logEl.scrollHeight;
            }
            if (st.status !== 'running' && !hideTimers[jobId]) {
                loadStatus();   // the pending badge may have grown
                hideTimers[jobId] = setTimeout(function () {
                    panel.classList.remove('visible');
                    card.classList.remove('running');
                    delete hideTimers[jobId];
                    delete logCounts[jobId];
                    loadJobs();
                }, 30000);
            }
        });
    }
    window.updateVideoRepairProgressFromData = updateProgress;

    // ── findings (the music card DOM, verbatim) ──────────────────────────────
    function filters() {
        return {
            job_id: ($('video-repair-findings-job-filter') || {}).value || '',
            severity: ($('video-repair-findings-severity-filter') || {}).value || '',
            status: ($('video-repair-findings-status-filter') || {}).value || '',
        };
    }

    function loadFindings() {
        var container = $('video-repair-findings-list');
        if (!container) return;
        var f = filters();
        var q = new URLSearchParams({ page: state.page + 1, limit: PAGE_SIZE });
        if (f.job_id) q.set('job_id', f.job_id);
        if (f.severity) q.set('severity', f.severity);
        if (f.status) q.set('status', f.status);
        jget(API + '/findings?' + q.toString()).then(function (data) {
            if (!data) { container.innerHTML = '<div class="repair-empty">Error loading findings</div>'; return; }
            state.selected = {};
            state.findingsById = {};
            (data.items || []).forEach(function (it) { state.findingsById[it.id] = it; });
            paintBulk();
            var cb = $('video-repair-select-all-cb');
            if (cb) cb.checked = false;
            var items = data.items || [];
            if (!items.length) {
                container.innerHTML = '<div class="repair-empty-state">' +
                    '<div class="repair-empty-icon">✨</div>' +
                    '<div class="repair-empty-title">No Findings</div>' +
                    '<div class="repair-empty-text">Run a job from the Jobs tab — anything it finds lands here for review.</div></div>';
            } else {
                container.innerHTML = items.map(findingCardHTML).join('');
            }
            renderPagination(data.total || 0, state.page);
        });
        jget(API + '/findings/counts').then(function (c) { if (c) badge(c.pending); });
    }

    function findingCardHTML(f) {
        var icon = SEVERITY_ICONS[f.severity] || 'ℹ️';
        var typeLabel = TYPE_LABELS[f.finding_type] || f.finding_type.replace(/_/g, ' ');
        var fixLabel = FIXABLE_TYPES[f.finding_type];
        var statusBadge = '';
        if (f.status !== 'pending') {
            var actionText = ACTION_LABELS[f.user_action] || f.user_action || f.status;
            statusBadge = '<span class="repair-finding-status-badge ' + esc(f.status) + '">' +
                esc(actionText) + '</span>';
        }
        return '<div class="repair-finding-card ' + esc(f.severity) + '" data-id="' + f.id + '">' +
            '<div class="repair-finding-main" data-vjr-expandrow>' +
                '<div class="repair-finding-select" data-vjr-noexpand>' +
                    (f.status === 'pending' ? '<input type="checkbox" data-vjr-check>' : '') +
                '</div>' +
                '<div class="repair-finding-content">' +
                    '<div class="repair-finding-title">' +
                        '<span class="repair-finding-icon">' + icon + '</span>' +
                        esc(f.title) +
                        '<span class="repair-finding-type-badge">' + esc(typeLabel) + '</span>' +
                        statusBadge +
                    '</div>' +
                    '<div class="repair-finding-desc">' + esc(f.description || '') + '</div>' +
                    '<div class="repair-finding-meta">' +
                        '<span>' + esc(f.job_id.replace(/_/g, ' ')) + '</span>' +
                        '<span>&middot;</span><span>' + esc(f.entity_type || 'item') + '</span>' +
                        '<span>&middot;</span><span>' + esc(age(f.created_at)) + '</span>' +
                    '</div>' +
                '</div>' +
                '<div class="repair-finding-actions" data-vjr-noexpand>' +
                    (f.status === 'pending'
                        ? (fixLabel ? '<button class="repair-finding-btn fix" type="button" data-vjr-fix title="' +
                              esc(fixLabel) + '">' + esc(fixLabel) + '</button>' : '') +
                          '<button class="repair-finding-btn dismiss" type="button" data-vjr-dismiss title="Dismiss">&times;</button>'
                        : '') +
                    '<button class="repair-finding-expand-btn" type="button" data-vjr-expand title="Details">&#9660;</button>' +
                '</div>' +
            '</div>' +
            '<div class="repair-finding-detail" id="video-repair-detail-' + f.id + '">' +
                '<div class="repair-finding-detail-inner">' + detailHTML(f) + '</div>' +
            '</div>' +
        '</div>';
    }

    var LAZY_TYPES = { missing_episodes: 1, quality_upgrade: 1, broken_file: 1, metadata_gap: 1 };

    function detailHTML(f) {
        var d = f.details || {};
        // Lazy types fetch live library data (art, overview) on first expand.
        if (LAZY_TYPES[f.finding_type]) {
            return '<div class="vrf-detail" data-vrf-fid="' + f.id + '">' +
                '<div class="repair-loading">Loading details…</div></div>';
        }
        if (f.finding_type === 'incomplete_collection') return collectionDetailHTML(d);
        if (f.finding_type === 'duplicate_movie') return duplicateDetailHTML(d);
        if (f.finding_type === 'stale_wishlist') return staleDetailHTML(d);
        return '<pre class="repair-finding-json">' + esc(JSON.stringify(d, null, 2)) + '</pre>';
    }

    function tmdbImg(p) {
        if (!p) return '';
        if (p.indexOf('http') === 0 || p.indexOf('/api/') === 0) return p;
        return 'https://image.tmdb.org/t/p/w342' + p;
    }

    function gb(bytes) { return ((bytes || 0) / 1073741824).toFixed(1) + ' GB'; }

    function miniCard(posterUrl, title, year, extraClass, chip) {
        var img = posterUrl
            ? '<img class="vrf-mini-poster" src="' + esc(posterUrl) +
              '" alt="" loading="lazy" onerror="this.style.visibility=\'hidden\'">'
            : '<div class="vrf-mini-poster vrf-poster--empty">🎬</div>';
        return '<div class="vrf-mini' + (extraClass ? ' ' + extraClass : '') + '">' + img +
            (chip || '') +
            '<div class="vrf-mini-title" title="' + esc(title) + '">' + esc(title) + '</div>' +
            (year ? '<div class="vrf-mini-year">' + year + '</div>' : '') +
        '</div>';
    }

    // ── Incomplete Collection: missing vs owned poster rails ─────────────────
    function collectionDetailHTML(d) {
        var missing = (d.missing || []).map(function (m) {
            return miniCard(tmdbImg(m.poster_url), m.title || '?', m.year, 'vrf-mini--want',
                '<span class="vrf-mini-chip">missing</span>');
        }).join('');
        var owned = (d.owned || []).map(function (m) {
            return miniCard(m.library_id != null ? '/api/video/poster/movie/' + m.library_id : '',
                m.title || '?', m.year, 'vrf-mini--dim',
                '<span class="vrf-mini-chip vrf-mini-chip--got">✓</span>');
        }).join('');
        return '<div class="vrf-chips">' +
                '<span class="vrf-chip vrf-chip--miss">' + (d.count || (d.missing || []).length) +
                    ' missing</span>' +
                '<span class="vrf-chip">' + (d.owned || []).length + ' / ' + (d.total || '?') +
                    ' owned</span></div>' +
            '<div class="vrf-rail">' + missing + owned + '</div>' +
            '<p class="repair-finding-hint">Approving sends the missing films to the wishlist — ' +
                'the auto-downloader takes it from there.</p>';
    }

    // ── Duplicate Movies: every copy side by side ────────────────────────────
    function duplicateDetailHTML(d) {
        var cards;
        if (d.kind === 'rows') {
            cards = (d.rows || []).map(function (r) {
                return '<div class="vrf-file">' +
                    '<div class="vrf-file-head">Library entry #' + r.id +
                        (r.server_source ? ' · ' + esc(r.server_source) : '') + '</div>' +
                    (r.path ? '<div class="vrf-file-path">' + esc(r.path) + '</div>' : '') +
                    '<button class="vrf-btn" type="button" data-vjr-open-movie="' + r.id +
                        '">View entry →</button>' +
                '</div>';
            }).join('');
        } else {
            cards = (d.files || []).map(function (fl) {
                return '<div class="vrf-file">' +
                    '<div class="vrf-file-head">' + esc(fl.resolution || '?') +
                        (fl.video_codec ? ' · ' + esc(fl.video_codec) : '') +
                        ' · ' + gb(fl.size_bytes) + '</div>' +
                    (fl.relative_path ? '<div class="vrf-file-path">' + esc(fl.relative_path) + '</div>' : '') +
                '</div>';
            }).join('') +
            (d.movie_id != null
                ? '<button class="vrf-btn" type="button" data-vjr-open-movie="' + d.movie_id +
                  '">View movie →</button>' : '');
        }
        return '<div class="vrf-files">' + cards + '</div>' +
            '<p class="repair-finding-hint">Report-only — nothing is deleted from here. Dismiss ' +
                'the copies you keep on purpose; they won’t come back.</p>';
    }

    // ── Stale Wishlist ───────────────────────────────────────────────────────
    function staleDetailHTML(d) {
        var poster = d.poster_url
            ? '<img class="vrf-poster" src="' + esc(tmdbImg(d.poster_url)) +
              '" alt="" loading="lazy" onerror="this.style.visibility=\'hidden\'">'
            : '<div class="vrf-poster vrf-poster--empty">🧹</div>';
        var view = '';
        if (d.library_id != null) {
            view = d.kind === 'episode'
                ? '<button class="vrf-btn" type="button" data-vjr-open-show="' + d.library_id + '">View show →</button>'
                : '<button class="vrf-btn" type="button" data-vjr-open-movie="' + d.library_id + '">View movie →</button>';
        }
        return '<div class="vrf-show">' + poster +
            '<div class="vrf-show-info">' +
                '<div class="vrf-show-title">' + esc(d.title || '?') + '</div>' +
                '<div class="vrf-chips"><span class="vrf-chip vrf-chip--got">✓ already owned</span>' +
                    '<span class="vrf-chip">' + esc(d.kind || 'movie') + '</span></div>' +
                '<p class="vrf-show-overview">The download engine never re-grabs owned items, so this ' +
                    'wishlist row will sit forever. Approving removes the row only — files are untouched.</p>' +
                '<div class="vrf-actions">' + view + '</div>' +
            '</div></div>';
    }

    function simpleEpisodeList(d) {
        var rows = (d.episodes || []).map(function (e) {
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

    // ── lazy rich details (live library data + art on first expand) ──────────
    function buildRichDetail(fid) {
        var f = (state.findingsById || {})[fid];
        var host = document.querySelector('#video-repair-detail-' + fid + ' .vrf-detail');
        if (!f || !host || host.getAttribute('data-loaded')) return;
        host.setAttribute('data-loaded', '1');
        var d = f.details || {};
        if (f.finding_type === 'missing_episodes') {
            if (d.show_id == null) { host.innerHTML = simpleEpisodeList(d); return; }
            jget('/api/video/detail/show/' + d.show_id)
                .then(function (show) {
                    host.innerHTML = show ? richDetailHTML(f, d, show) : simpleEpisodeList(d);
                })
                .catch(function () { host.innerHTML = simpleEpisodeList(d); });
            return;
        }
        // quality_upgrade / broken_file / metadata_gap — one shared movie panel.
        var fallback = '<pre class="repair-finding-json">' +
            esc(JSON.stringify(d, null, 2)) + '</pre>';
        if (d.movie_id == null) { host.innerHTML = fallback; return; }
        jget('/api/video/detail/movie/' + d.movie_id)
            .then(function (movie) {
                host.innerHTML = movie ? moviePanelHTML(f, d, movie) : fallback;
            })
            .catch(function () { host.innerHTML = fallback; });
    }

    // Shared movie header + per-type body for the movie-side findings.
    function moviePanelHTML(f, d, movie) {
        var poster = movie.has_poster
            ? '<img class="vrf-poster" src="/api/video/poster/movie/' + movie.id +
              '" alt="" loading="lazy" onerror="this.style.visibility=\'hidden\'">'
            : '<div class="vrf-poster vrf-poster--empty">🎬</div>';
        var subBits = [];
        if (movie.studio) subBits.push(esc(movie.studio));
        if (movie.content_rating) subBits.push(esc(movie.content_rating));
        if (movie.runtime_minutes) subBits.push(movie.runtime_minutes + ' min');

        var chips = [], body = '';
        if (f.finding_type === 'quality_upgrade') {
            var fl = d.file || {};
            chips.push('<span class="vrf-chip vrf-chip--miss">now: ' +
                esc(fl.resolution || '?') + '</span>');
            chips.push('<span class="vrf-chip">cutoff: ' + esc(d.cutoff || '?') + '</span>');
            body = fileCardHTML(fl);
        } else if (f.finding_type === 'broken_file') {
            chips.push('<span class="vrf-chip vrf-chip--miss">' + esc(d.reason || 'suspect') + '</span>');
            if (d.expected_seconds) {
                var pct = d.expected_seconds
                    ? Math.min(100, Math.round(100 * (d.actual_seconds || 0) / d.expected_seconds)) : 0;
                body = '<div class="vrf-runtime"><div class="vrf-runtime-fill" style="width:' +
                    pct + '%"></div></div>' +
                    '<div class="vrf-runtime-label">file runs ' +
                        Math.round((d.actual_seconds || 0) / 60) + ' of ' +
                        Math.round(d.expected_seconds / 60) + ' minutes</div>' +
                    fileCardHTML(d.file || {});
            } else {
                body = fileCardHTML(d.file || {});
            }
        } else if (f.finding_type === 'metadata_gap') {
            (d.gaps || []).forEach(function (g) {
                chips.push('<span class="vrf-chip vrf-chip--miss">' +
                    esc(GAP_LABELS[g] || g) + '</span>');
            });
        }
        return '<div class="vrf-show">' + poster +
            '<div class="vrf-show-info">' +
                '<div class="vrf-show-title">' + esc(movie.title) +
                    (movie.year ? ' <span class="vrf-show-year">(' + movie.year + ')</span>' : '') + '</div>' +
                (subBits.length ? '<div class="vrf-show-sub">' + subBits.join(' · ') + '</div>' : '') +
                '<div class="vrf-chips">' + chips.join('') + '</div>' +
                (movie.overview ? '<p class="vrf-show-overview">' + esc(movie.overview) + '</p>' : '') +
                '<div class="vrf-actions"><button class="vrf-btn" type="button" ' +
                    'data-vjr-open-movie="' + movie.id + '">View movie →</button></div>' +
            '</div></div>' + body;
    }

    function fileCardHTML(fl) {
        if (!fl || (!fl.relative_path && !fl.size_bytes)) return '';
        var bits = [];
        if (fl.resolution) bits.push(esc(fl.resolution));
        if (fl.video_codec) bits.push(esc(fl.video_codec));
        if (fl.audio_codec) bits.push(esc(fl.audio_codec));
        if (fl.release_source) bits.push(esc(fl.release_source));
        if (fl.size_bytes) bits.push(gb(fl.size_bytes));
        return '<div class="vrf-file">' +
            '<div class="vrf-file-head">' + bits.join(' · ') + '</div>' +
            (fl.relative_path ? '<div class="vrf-file-path">' + esc(fl.relative_path) + '</div>' : '') +
        '</div>';
    }

    function richDetailHTML(f, d, show) {
        // Which (season, episode) pairs this finding names.
        var missing = {};
        (d.episodes || []).forEach(function (e) {
            missing[e.season_number + ':' + e.episode_number] = e;
        });
        var poster = show.has_poster
            ? '<img class="vrf-poster" src="/api/video/poster/show/' + show.id +
              '" alt="" loading="lazy" onerror="this.style.visibility=\'hidden\'">'
            : '<div class="vrf-poster vrf-poster--empty">📺</div>';
        var subBits = [];
        if (show.network) subBits.push(esc(show.network));
        if (show.status) {
            subBits.push(esc(show.status.charAt(0).toUpperCase() + show.status.slice(1)));
        }
        if (show.content_rating) subBits.push(esc(show.content_rating));

        var chips = ['<span class="vrf-chip vrf-chip--miss">' + (d.count || (d.episodes || []).length) +
            ' missing</span>'];
        if (show.episode_total) {
            chips.push('<span class="vrf-chip">' + (show.episode_owned || 0) + ' / ' +
                show.episode_total + ' owned</span>');
        }
        if (show.first_air_date) {
            chips.push('<span class="vrf-chip">' + esc(String(show.first_air_date).slice(0, 4)) +
                (show.last_air_date ? '–' + esc(String(show.last_air_date).slice(0, 4)) : '') + '</span>');
        }

        var head =
            '<div class="vrf-show">' + poster +
                '<div class="vrf-show-info">' +
                    '<div class="vrf-show-title">' + esc(show.title) +
                        (show.year ? ' <span class="vrf-show-year">(' + show.year + ')</span>' : '') + '</div>' +
                    (subBits.length ? '<div class="vrf-show-sub">' + subBits.join(' · ') + '</div>' : '') +
                    '<div class="vrf-chips">' + chips.join('') + '</div>' +
                    (show.overview ? '<p class="vrf-show-overview">' + esc(show.overview) + '</p>' : '') +
                    '<div class="vrf-actions">' +
                        '<button class="vrf-btn" type="button" data-vjr-open-show="' + show.id +
                            '">View show →</button>' +
                    '</div>' +
                '</div>' +
            '</div>';

        var sections = (show.seasons || []).map(function (s) {
            var eps = (s.episodes || []).filter(function (e) {
                return missing[s.season_number + ':' + e.episode_number];
            });
            if (!eps.length) return '';
            var sPoster = (s.has_poster && s.id != null)
                ? '<img class="vrf-season-poster" src="/api/video/poster/season/' + s.id +
                  '" alt="" loading="lazy" onerror="this.style.visibility=\'hidden\'">'
                : '<div class="vrf-season-poster vrf-poster--empty">' +
                    (s.season_number === 0 ? '⭐' : 'S' + s.season_number) + '</div>';
            var rows = eps.map(function (e) {
                var code = 'S' + String(s.season_number).padStart(2, '0') +
                    'E' + String(e.episode_number).padStart(2, '0');
                var still = e.has_still && e.id != null
                    ? '<img class="vrf-ep-still" src="/api/video/poster/episode/' + e.id +
                      '?w=500" alt="" loading="lazy" onerror="this.style.visibility=\'hidden\'">'
                    : '<div class="vrf-ep-still vrf-poster--empty">▷</div>';
                return '<div class="vrf-ep">' + still +
                    '<div class="vrf-ep-body">' +
                        '<div class="vrf-ep-head"><span class="vrf-ep-code">' + esc(code) + '</span>' +
                            '<span class="vrf-ep-title">' + esc(e.title || 'Episode ' + e.episode_number) + '</span>' +
                            (e.owned ? '<span class="vrf-chip vrf-chip--got">✓ got it since</span>' : '') +
                            '<span class="vrf-ep-date">' + esc(e.air_date || '') + '</span></div>' +
                        (e.overview ? '<p class="vrf-ep-overview">' + esc(e.overview) + '</p>' : '') +
                    '</div>' +
                '</div>';
            }).join('');
            return '<div class="vrf-season">' +
                '<div class="vrf-season-head">' + sPoster +
                    '<span class="vrf-season-name">' + esc(s.title) + '</span>' +
                    '<span class="vrf-season-count">' + eps.length + ' missing of ' +
                        (s.episode_total || eps.length) + '</span>' +
                '</div>' +
                '<div class="vrf-eps">' + rows + '</div>' +
            '</div>';
        }).join('');

        return head + (sections ||
            '<p class="repair-finding-hint">These episodes are no longer in the library listing — re-run the job to refresh this finding.</p>') +
            '<p class="repair-finding-hint">Approving sends every listed episode to the wishlist — the auto-downloader takes it from there.</p>';
    }

    function renderPagination(total, currentPage) {
        var container = $('video-repair-findings-pagination');
        if (!container) return;
        var totalPages = Math.ceil(total / PAGE_SIZE);
        if (totalPages <= 1) { container.innerHTML = ''; return; }
        var html = '';
        if (currentPage > 0) {
            html += '<button class="repair-page-btn" type="button" data-vjr-goto="' +
                (currentPage - 1) + '">&larr;</button>';
        }
        var startPage = Math.max(0, currentPage - 3);
        var endPage = Math.min(totalPages, startPage + 7);
        if (endPage - startPage < 7) startPage = Math.max(0, endPage - 7);
        if (startPage > 0) {
            html += '<button class="repair-page-btn" type="button" data-vjr-goto="0">1</button>';
            if (startPage > 1) html += '<span class="repair-page-info">...</span>';
        }
        for (var p = startPage; p < endPage; p++) {
            html += '<button class="repair-page-btn' + (p === currentPage ? ' active' : '') +
                '" type="button" data-vjr-goto="' + p + '">' + (p + 1) + '</button>';
        }
        if (endPage < totalPages) {
            if (endPage < totalPages - 1) html += '<span class="repair-page-info">...</span>';
            html += '<button class="repair-page-btn" type="button" data-vjr-goto="' +
                (totalPages - 1) + '">' + totalPages + '</button>';
        }
        if (currentPage < totalPages - 1) {
            html += '<button class="repair-page-btn" type="button" data-vjr-goto="' +
                (currentPage + 1) + '">&rarr;</button>';
        }
        container.innerHTML = html;
    }

    function paintBulk() {
        var n = Object.keys(state.selected).length;
        var bar = $('video-repair-findings-bulk');
        if (bar) bar.style.display = n ? '' : 'none';
        var c = $('video-repair-bulk-count');
        if (c) c.textContent = n + ' selected';
    }

    // ── history (the music entry DOM, verbatim) ──────────────────────────────
    function loadHistory() {
        var container = $('video-repair-history-list');
        if (!container) return;
        jget(API + '/history?limit=50').then(function (data) {
            if (!data) { container.innerHTML = '<div class="repair-empty">Error loading history</div>'; return; }
            var runs = data.runs || [];
            if (!runs.length) {
                container.innerHTML = '<div class="repair-empty-state">' +
                    '<div class="repair-empty-icon">&#128337;</div>' +
                    '<div class="repair-empty-title">No History Yet</div>' +
                    '<div class="repair-empty-text">Job run history will appear here after maintenance jobs complete their first scan.</div></div>';
                return;
            }
            var names = {};
            state.jobs.forEach(function (j) { names[j.job_id] = j.display_name; });
            container.innerHTML = runs.map(function (run) {
                var duration = run.duration_seconds ? run.duration_seconds.toFixed(1) + 's' : '-';
                var statusClass = run.status === 'completed' ? 'success' :
                    run.status === 'failed' ? 'error' : 'running';
                var stats = ['<span class="repair-history-stat"><strong>' +
                    (run.items_scanned || 0).toLocaleString() + '</strong> scanned</span>'];
                if (run.findings_created) {
                    stats.push('<span class="repair-history-stat findings"><strong>' +
                        run.findings_created + '</strong> findings</span>');
                }
                if (run.auto_fixed) {
                    stats.push('<span class="repair-history-stat fixed"><strong>' +
                        run.auto_fixed + '</strong> fixed</span>');
                }
                if (run.errors) {
                    stats.push('<span class="repair-history-stat errors"><strong>' +
                        run.errors + '</strong> errors</span>');
                }
                var startTime = run.started_at ? new Date(run.started_at + 'Z').toLocaleString() : '-';
                var endTime = run.finished_at ? new Date(run.finished_at + 'Z').toLocaleString() : 'In progress';
                return '<div class="repair-history-entry">' +
                    '<div class="repair-history-header">' +
                        '<div class="repair-history-dot ' + statusClass + '"></div>' +
                        '<span class="repair-history-name">' + esc(names[run.job_id] || run.job_id) + '</span>' +
                        '<span class="repair-history-status ' + statusClass + '">' + esc(run.status) + '</span>' +
                        '<span class="repair-history-duration">' + duration + '</span>' +
                    '</div>' +
                    '<div class="repair-history-stats">' + stats.join('') + '</div>' +
                    '<div class="repair-history-meta">' + esc(age(run.started_at)) + ' &middot; ' +
                        esc(startTime) + ' &rarr; ' + esc(endTime) + '</div>' +
                '</div>';
            }).join('');
        });
    }

    // ── wiring ───────────────────────────────────────────────────────────────
    function wire() {
        var r = root();
        if (!r || r.getAttribute('data-wired')) return;
        r.setAttribute('data-wired', '1');

        var master = $('video-repair-master-toggle');
        if (master) {
            master.addEventListener('change', function () {
                jsend(API + '/toggle', { enabled: master.checked }).then(function (res) {
                    var l = $('video-repair-master-label');
                    if (l && res.ok) l.textContent = res.body.enabled ? 'Enabled' : 'Disabled';
                });
            });
        }

        r.addEventListener('click', function (e) {
            var tab = e.target.closest('[data-video-repair-tab]');
            if (tab) { switchTab(tab.getAttribute('data-video-repair-tab')); return; }

            var save = e.target.closest('[data-vjr-save]');
            if (save) { saveJobSettings(save.getAttribute('data-vjr-save')); return; }

            var jobCard = e.target.closest('.repair-job-card[data-job-id]');
            if (jobCard) {
                var jobId = jobCard.getAttribute('data-job-id');
                if (e.target.closest('[data-vjr-run]')) {
                    // Optimistic swap ▶ → ⏹; the progress socket confirms within a second.
                    var rb = e.target.closest('[data-vjr-run]');
                    rb.outerHTML = '<button class="repair-stop-btn" type="button" data-vjr-stop title="Stop this run">&#9209;</button>';
                    jsend(API + '/jobs/' + jobId + '/run').then(function (res) {
                        if (!res.ok) { toast('Could not start job', 'error'); loadJobs(); }
                    });
                    return;
                }
                if (e.target.closest('[data-vjr-stop]')) {
                    var sb = e.target.closest('[data-vjr-stop]');
                    sb.disabled = true;
                    jsend(API + '/jobs/' + jobId + '/stop').then(function () { setTimeout(loadJobs, 600); });
                    return;
                }
                if (e.target.closest('[data-vjr-settings]')) {
                    var panel = $('video-repair-settings-' + jobId);
                    if (panel) panel.style.display = panel.style.display === 'none' ? '' : 'none';
                    return;
                }
                if (e.target.closest('[data-vjr-help]')) {
                    var job = state.jobs.filter(function (j) { return j.job_id === jobId; })[0];
                    if (job) {
                        confirmDlg({ title: job.display_name, message: job.help_text || job.description,
                            confirmText: 'Got it', cancelText: 'Close' });
                    }
                    return;
                }
            }

            var goto_ = e.target.closest('[data-vjr-goto]');
            if (goto_) {
                state.page = parseInt(goto_.getAttribute('data-vjr-goto'), 10) || 0;
                loadFindings();
                return;
            }

            var card = e.target.closest('.repair-finding-card[data-id]');
            if (card) {
                var fid = card.getAttribute('data-id');
                if (e.target.closest('[data-vjr-fix]')) {
                    jsend(API + '/findings/' + fid + '/fix').then(function (res) {
                        if (res.ok && res.body.success) {
                            toast(res.body.message || 'Approved', 'success');
                            loadFindings(); loadStatus();
                        } else {
                            toast((res.body && res.body.error) || 'Fix failed', 'error');
                        }
                    });
                    return;
                }
                if (e.target.closest('[data-vjr-dismiss]')) {
                    jsend(API + '/findings/' + fid + '/dismiss').then(function (res) {
                        if (res.ok) { loadFindings(); loadStatus(); }
                    });
                    return;
                }
                var openShow = e.target.closest('[data-vjr-open-show]');
                if (openShow) {
                    document.dispatchEvent(new CustomEvent('soulsync:video-open-detail', {
                        detail: { kind: 'show',
                                  id: parseInt(openShow.getAttribute('data-vjr-open-show'), 10),
                                  source: 'library' },
                    }));
                    return;
                }
                var openMovie = e.target.closest('[data-vjr-open-movie]');
                if (openMovie) {
                    document.dispatchEvent(new CustomEvent('soulsync:video-open-detail', {
                        detail: { kind: 'movie',
                                  id: parseInt(openMovie.getAttribute('data-vjr-open-movie'), 10),
                                  source: 'library' },
                    }));
                    return;
                }
                // Expand: the ▼ button OR anywhere on the main row (music behavior),
                // except the checkbox / action zones.
                if (e.target.closest('[data-vjr-expand]') ||
                        (e.target.closest('[data-vjr-expandrow]') && !e.target.closest('[data-vjr-noexpand]'))) {
                    var det = $('video-repair-detail-' + fid);
                    var btn = card.querySelector('.repair-finding-expand-btn');
                    if (det) {
                        var open = det.classList.toggle('open');
                        if (btn) btn.classList.toggle('open', open);
                        if (open) buildRichDetail(fid);
                    }
                    return;
                }
            }

            var bulk = e.target.closest('[data-video-repair-bulk]');
            if (bulk) {
                var ids = Object.keys(state.selected).map(Number);
                if (!ids.length) return;
                if (bulk.getAttribute('data-video-repair-bulk') === 'fix') {
                    jsend(API + '/findings/bulk-fix', { ids: ids }).then(function (res) {
                        var b = res.body || {};
                        toast('Approved ' + (b.fixed || 0) + (b.failed ? ', ' + b.failed + ' failed' : ''),
                            b.failed ? 'warning' : 'success');
                        loadFindings(); loadStatus();
                    });
                } else {
                    jsend(API + '/findings/bulk', { ids: ids, action: 'dismiss' }).then(function () {
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
                    jsend(API + '/findings/clear',
                          { job_id: f.job_id || null, status: f.status || null })
                        .then(function (res) {
                            toast('Cleared ' + ((res.body || {}).deleted || 0) + ' findings', 'info');
                            loadFindings(); loadStatus();
                        });
                });
            }
        });

        r.addEventListener('change', function (e) {
            var jobCard = e.target.closest('.repair-job-card[data-job-id]');
            if (jobCard && e.target.hasAttribute('data-vjr-enable')) {
                var jobId = jobCard.getAttribute('data-job-id');
                var enabled = e.target.checked;
                jsend(API + '/jobs/' + jobId + '/toggle', { enabled: enabled });
                jobCard.classList.toggle('disabled', !enabled);
                var dot = jobCard.querySelector('.repair-job-status');
                if (dot) dot.className = 'repair-job-status ' + (enabled ? 'enabled' : 'disabled');
                return;
            }
            if (e.target.id === 'video-repair-select-all-cb') {
                var boxes = document.querySelectorAll('#video-repair-findings-list [data-vjr-check]');
                state.selected = {};
                for (var i = 0; i < boxes.length; i++) {
                    boxes[i].checked = e.target.checked;
                    if (e.target.checked) {
                        state.selected[boxes[i].closest('.repair-finding-card')
                            .getAttribute('data-id')] = true;
                    }
                }
                paintBulk();
                return;
            }
            if (e.target.hasAttribute && e.target.hasAttribute('data-vjr-check')) {
                var fid = e.target.closest('.repair-finding-card').getAttribute('data-id');
                if (e.target.checked) state.selected[fid] = true;
                else delete state.selected[fid];
                paintBulk();
                return;
            }
            if (e.target.id === 'video-repair-findings-job-filter' ||
                    e.target.id === 'video-repair-findings-severity-filter' ||
                    e.target.id === 'video-repair-findings-status-filter') {
                state.page = 0;
                loadFindings();
            }
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
