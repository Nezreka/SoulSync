/*
 * SoulSync — Video Automations page.
 *
 * The automation engine is app-wide, so these are the SAME system automations the
 * music side runs — surfaced on the video side too (minus Refresh Beatport Cache and
 * any user/playlist-pipeline automations). Read + toggle + run only; reuses the music
 * .automation-* card look. Calls the shared /api/automations endpoint (no music imports).
 */
(function () {
    'use strict';

    var URL_LIST = '/api/automations';
    var _timer = null, _wired = false;

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function toast(m, t) { if (typeof showToast === 'function') showToast(m, t); }
    function getJSON(u) {
        return fetch(u, { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; });
    }
    function post(u) {
        return fetch(u, { method: 'POST', headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; });
    }

    // Friendly labels (system action_types only; Beatport/playlist excluded from display).
    var ACTIONS = {
        process_wishlist: 'Process wishlist', scan_watchlist: 'Scan watchlist',
        scan_library: 'Scan library', start_database_update: 'Update database',
        deep_scan_library: 'Deep-scan library', clean_search_history: 'Clean search history',
        clean_completed_downloads: 'Clean completed downloads', backup_database: 'Back up database',
        full_cleanup: 'Full cleanup', clear_quarantine: 'Clear quarantine',
        cleanup_wishlist: 'Clean up wishlist', update_discovery_pool: 'Update discovery pool'
    };
    var TICONS = { schedule: '⏱️', daily_time: '🕓', weekly_time: '📅', batch_complete: '⬇️', library_scan_completed: '🔄' };

    function fmtTrigger(type, cfg) {
        cfg = cfg || {};
        if (type === 'schedule') return 'Every ' + (cfg.interval || 1) + ' ' + (cfg.unit || 'hours');
        if (type === 'daily_time') return 'Daily at ' + (cfg.time || '00:00');
        if (type === 'weekly_time') return 'Weekly at ' + (cfg.time || '00:00');
        if (type === 'batch_complete') return 'When downloads finish';
        if (type === 'library_scan_completed') return 'After a library scan';
        return String(type || '').replace(/_/g, ' ');
    }
    function fmtAction(type) { return ACTIONS[type] || String(type || '').replace(/_/g, ' '); }

    function timeAgo(ts) {
        var t = Date.parse(String(ts || '').replace(' ', 'T') + (/[zZ]|[+-]\d\d:?\d\d$/.test(ts || '') ? '' : 'Z'));
        if (isNaN(t)) return '';
        var s = Math.round((Date.now() - t) / 1000);
        if (s < 60) return s + 's ago';
        if (s < 3600) return Math.round(s / 60) + 'm ago';
        if (s < 86400) return Math.round(s / 3600) + 'h ago';
        return Math.round(s / 86400) + 'd ago';
    }
    function timeUntil(ts) {
        var t = Date.parse(String(ts || '').replace(' ', 'T') + (/[zZ]|[+-]\d\d:?\d\d$/.test(ts || '') ? '' : 'Z'));
        if (isNaN(t)) return '';
        var s = Math.round((t - Date.now()) / 1000);
        if (s <= 0) return 'due';
        if (s < 60) return 'in ' + s + 's';
        if (s < 3600) return 'in ' + Math.round(s / 60) + 'm';
        if (s < 86400) return 'in ' + Math.round(s / 3600) + 'h';
        return 'in ' + Math.round(s / 86400) + 'd';
    }

    // System automations the video side shows (drop Beatport + user/playlist ones).
    function isVideoSystem(a) {
        return a && a.is_system &&
            a.action_type !== 'refresh_beatport_cache' &&
            a.action_type !== 'playlist_pipeline' &&
            a.owned_by !== 'playlist_pipeline';
    }

    function cardHTML(a) {
        var timers = ['schedule', 'daily_time', 'weekly_time'];
        var meta = [];
        if (a.last_run) meta.push('Last: ' + esc(timeAgo(a.last_run)));
        if (a.next_run && a.enabled && timers.indexOf(a.trigger_type) > -1) meta.push('Next: ' + esc(timeUntil(a.next_run)));
        else if (a.enabled && timers.indexOf(a.trigger_type) === -1) meta.push('Listening');
        if (a.run_count) meta.push('Runs: ' + a.run_count);
        if (a.last_error) meta.push('<span class="vauto-err">Error: ' + esc(a.last_error) + '</span>');
        return '<div class="automation-card system' + (a.enabled ? '' : ' disabled') + '" data-auto-id="' + a.id + '">' +
            '<div class="automation-status ' + (a.enabled ? 'enabled' : 'disabled') + '"></div>' +
            '<div class="automation-info">' +
                '<div class="automation-name">' + esc(a.name) + '</div>' +
                '<div class="automation-flow">' +
                    '<span class="flow-trigger">' + (TICONS[a.trigger_type] || '⚙️') + ' ' + esc(fmtTrigger(a.trigger_type, a.trigger_config)) + '</span>' +
                    '<span class="flow-arrow">&rarr;</span>' +
                    '<span class="flow-action">' + esc(fmtAction(a.action_type)) + '</span>' +
                '</div>' +
                '<div class="automation-meta">' + meta.join(' &middot; ') + '</div>' +
            '</div>' +
            '<div class="automation-actions">' +
                '<button class="automation-run-btn" type="button" data-auto-run="' + a.id + '" title="Run now">&#9654;</button>' +
                '<label class="automation-toggle"><input type="checkbox" data-auto-toggle="' + a.id + '"' + (a.enabled ? ' checked' : '') + '>' +
                    '<span class="toggle-slider"></span></label>' +
            '</div>' +
        '</div>';
    }

    function render(list) {
        var host = document.querySelector('[data-vauto-list]'); if (!host) return;
        var sys = (list || []).filter(isVideoSystem);
        var sub = document.querySelector('[data-vauto-sub]');
        if (sub) {
            var on = sys.filter(function (a) { return a.enabled; }).length;
            sub.textContent = sys.length ? (on + ' of ' + sys.length + ' enabled · shared with the music side')
                : 'System tasks that keep your library fresh — shared with the music side.';
        }
        if (!sys.length) {
            host.innerHTML = '<div class="vauto-empty">No system automations yet.</div>';
            return;
        }
        host.innerHTML = sys.map(cardHTML).join('');
    }

    function load() { getJSON(URL_LIST).then(function (d) { render(Array.isArray(d) ? d : (d && d.automations) || []); }); }

    function start() {
        wire(); load();
        if (_timer) clearInterval(_timer);
        _timer = setInterval(function () {
            if (document.body.getAttribute('data-side') === 'video' &&
                document.querySelector('[data-video-subpage="video-automations"]:not([hidden])')) load();
            else stop();
        }, 5000);
    }
    function stop() { if (_timer) { clearInterval(_timer); _timer = null; } }

    function wire() {
        if (_wired) return; _wired = true;
        var host = document.querySelector('[data-vauto-list]'); if (!host) return;
        host.addEventListener('click', function (e) {
            var run = e.target.closest('[data-auto-run]');
            if (run) {
                run.disabled = true;
                post('/api/automations/' + run.getAttribute('data-auto-run') + '/run').then(function (r) {
                    run.disabled = false;
                    toast(r && r.success ? 'Automation started' : 'Could not run it', r && r.success ? 'success' : 'error');
                    setTimeout(load, 800);
                });
            }
        });
        host.addEventListener('change', function (e) {
            var tg = e.target.closest('[data-auto-toggle]');
            if (tg) post('/api/automations/' + tg.getAttribute('data-auto-toggle') + '/toggle').then(function () { setTimeout(load, 300); });
        });
    }

    document.addEventListener('soulsync:video-page-shown', function (e) {
        if (e.detail === 'video-automations') start(); else stop();
    });
})();
