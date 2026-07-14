/*
 * SoulSync — Video Automations page.
 *
 * The automation engine is app-wide, so this shows the SAME system automations the
 * music side runs (minus Refresh Beatport Cache + user/playlist ones). To match the
 * music page EXACTLY it reuses the music page's own builders — _buildAutomationSection,
 * renderAutomationCard, _buildAutomationHub (all global in stats-automations.js) — so
 * the System section, cards, and Automation Hub are byte-for-byte identical.
 * Read + run + toggle (handled by the reused music card handlers). No music imports.
 */
(function () {
    'use strict';

    var _timer = null;

    function getJSON(u) {
        return fetch(u, { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; });
    }

    // ONLY video-owned automations belong here — the music system automations target
    // music resources and are hidden. Video automations (tagged owned_by='video') are a
    // separate set, built next; for now none exist so the list is empty by design.
    function isVideoAutomation(a) {
        return a && a.owned_by === 'video';
    }

    // The API returns system automations newest-created-first, which jumbles them (a
    // re-seeded row jumps to the top). Impose a logical top-to-bottom pipeline order
    // instead. Scoped to THIS page — the music page's ordering is untouched. Unknown /
    // future action types fall to the end, keeping their API order (stable sort).
    var _SYS_ORDER = [
        // Stage 1 — scans that FILL the wishlist
        'video_scan_watchlist_people', 'video_scan_watchlist_studios', 'video_scan_watchlist_channels',
        'video_scan_watchlist_playlists', 'video_refresh_airing_schedules', 'video_add_airing_episodes',
        // Stage 2 — processors that DRAIN it (download)
        'video_process_movie_wishlist', 'video_process_episode_wishlist', 'video_process_youtube_wishlist',
        'video_clean_youtube_episodes',
        // Library scan / sync
        'video_scan_server', 'video_scan_library', 'video_update_database',
        'video_update_database_hourly', 'video_deep_scan_tv', 'video_deep_scan_movies',
        // Presentation
        'video_apply_overlays', 'video_sync_collections',
        // Maintenance
        'video_clean_plex_images',
        'video_clean_search_history', 'video_clean_completed_downloads', 'video_full_cleanup', 'video_backup_database',
    ];
    function _sysOrderIndex(a) {
        var i = _SYS_ORDER.indexOf(a && a.action_type);
        return i === -1 ? _SYS_ORDER.length : i;
    }
    function sortSystem(list) {
        return list.slice().sort(function (a, b) { return _sysOrderIndex(a) - _sysOrderIndex(b); });
    }

    function renderSystem(sys, anyAtAll) {
        var host = document.querySelector('[data-vauto-list]'); if (!host) return;
        var emptyEl = document.querySelector('[data-vauto-empty]');
        if (emptyEl) emptyEl.style.display = anyAtAll ? 'none' : '';
        var existing = host.querySelector('#vauto-section-system');
        if (!sys.length) {
            if (existing) existing.remove();
            return;
        }
        if (typeof window._buildAutomationSection !== 'function') return;
        // exact same section the music page builds for its System group (unique id so
        // it never clashes with the real music page's #auto-section-system).
        var section = window._buildAutomationSection('vauto-section-system', 'System', sys, true, { isProtected: true });
        if (existing) host.replaceChild(section, existing);
        else host.insertBefore(section, host.firstChild);
        _injectOverlayConfig(section);
    }

    // The overlay automation's behaviour is driven by the per-scope overlay
    // settings (which template per movie/show/season/episode). Surface a Configure
    // button right on its card that opens that settings editor — the same modal
    // the Studio uses, one source of truth. Re-injected on every section rebuild.
    function _injectOverlayConfig(section) {
        var card = section && section.querySelector('.automation-card[data-action-type="video_apply_overlays"]');
        if (!card || card.querySelector('[data-vauto-overlay-cfg]')) return;
        var actions = card.querySelector('.automation-actions');
        if (!actions) return;
        var btn = document.createElement('button');
        btn.className = 'automation-edit-btn';
        btn.setAttribute('data-vauto-overlay-cfg', '');
        btn.title = 'Configure overlays (which template per movie / show / season / episode)';
        btn.innerHTML = '🎨';   // 🎨
        btn.addEventListener('click', function (e) {
            e.stopPropagation();
            if (window.VideoOverlayEditor && window.VideoOverlayEditor.openSettings) window.VideoOverlayEditor.openSettings();
        });
        actions.insertBefore(btn, actions.firstChild);
    }

    // The hub builder is side-aware (stats-automations.js _hubGroups()/_hubRecipes()/
    // _hubGuides()/_hubTips()/_hubReference() read the VIDEO_HUB_* datasets when
    // data-side='video'), so it renders full video content here — pipelines that
    // deploy owned_by='video' rows, video recipes, guides, tips and reference.
    function renderHubOnce() {
        var host = document.querySelector('[data-vauto-list]'); if (!host) return;
        if (host.querySelector('#auto-section-hub')) return;   // build it once; it's static
        if (typeof window._buildAutomationHub !== 'function') return;
        host.appendChild(window._buildAutomationHub());
    }

    // User-built video automations — same grouped sections the music page renders
    // (folders + "My Automations"), built with the shared _buildAutomationSection so
    // edit/duplicate/delete/group card actions all work. Rebuilt on structural change.
    function renderUser(user) {
        var host = document.querySelector('[data-vauto-list]'); if (!host) return;
        host.querySelectorAll('[data-vauto-user-section]').forEach(function (el) { el.remove(); });
        if (!user.length || typeof window._buildAutomationSection !== 'function') return;
        var groups = [];
        user.forEach(function (a) {
            if (a.group_name && groups.indexOf(a.group_name) === -1) groups.push(a.group_name);
        });
        groups.sort();
        var frag = document.createDocumentFragment();
        groups.forEach(function (g) {
            var autos = user.filter(function (a) { return a.group_name === g; });
            var s = window._buildAutomationSection(
                'vauto-section-group-' + g.replace(/\W+/g, '_'), '📁 ' + g, autos, true, { groupName: g });
            s.setAttribute('data-vauto-user-section', '');
            frag.appendChild(s);
        });
        var ungrouped = user.filter(function (a) { return !a.group_name; });
        if (ungrouped.length) {
            var s2 = window._buildAutomationSection('vauto-section-custom', 'My Automations', ungrouped, true);
            s2.setAttribute('data-vauto-user-section', '');
            frag.appendChild(s2);
        }
        host.appendChild(frag);
    }

    function renderStats(sys, user) {
        var el = document.querySelector('[data-vauto-stats]'); if (!el) return;
        var all = sys.concat(user || []);
        if (!all.length) { el.innerHTML = ''; return; }
        var active = all.filter(function (a) { return a.enabled; }).length;
        el.innerHTML = '<span class="auto-stat"><strong>' + active + '</strong> Active</span>' +
            '<span class="auto-stat"><strong>' + sys.length + '</strong> System</span>' +
            '<span class="auto-stat"><strong>' + (user || []).length + '</strong> Custom</span>';
    }

    var _lastSig = null;
    function load() {
        return getJSON('/api/automations').then(function (d) {
            var all = Array.isArray(d) ? d : (d && d.automations) || [];
            var mine = all.filter(isVideoAutomation);
            var sys = sortSystem(mine.filter(function (a) { return a.is_system; }));
            var user = mine.filter(function (a) { return !a.is_system; });
            // Re-rendering the whole System section every 8s poll destroys + recreates every
            // card — that's the blink, and it wipes the live progress the socket patches in.
            // Only rebuild when something STRUCTURAL changed (added/removed/toggled/ran);
            // live progress arrives via socket, the "Next: in Xm" countdown ticks locally.
            var sig = JSON.stringify(sys.concat(user).map(function (a) {
                return [a.id, a.enabled, a.name, a.trigger_type, a.action_type, a.group_name,
                        a.last_run, a.next_run, a.run_count, a.last_result];
            }));
            if (sig === _lastSig) return;
            _lastSig = sig;
            renderStats(sys, user);
            renderSystem(sys, mine.length > 0);
            renderHubOnce();
            renderUser(user);
        });
    }

    // Exposed so the shared automation builder (stats-automations.js) can refresh
    // THIS list after a video automation is created/edited/saved. Lives on window
    // because the builder is global and this module is an IIFE.
    window._reloadVideoAutomations = load;

    function onPage() {
        return document.body.getAttribute('data-side') === 'video' &&
            !!document.querySelector('[data-video-subpage="video-automations"]:not([hidden])');
    }

    function start() {
        // Always enter on the list view — if the builder was left open from a
        // previous visit, swap back so the page doesn't greet you mid-build.
        var bv = document.getElementById('vauto-builder-view');
        var lv = document.getElementById('vauto-list-view');
        if (bv && lv && bv.style.display !== 'none') { bv.style.display = 'none'; lv.style.display = ''; }
        load();
        // Refresh the System section shortly after a run/toggle (the reused music card
        // handlers fire on the music list, not ours) — keeps our cards in sync.
        var host = document.querySelector('[data-vauto-list]');
        if (host && !host._vautoSync) {
            host._vautoSync = true;
            var soon = function () { setTimeout(load, 700); };
            host.addEventListener('click', function (e) { if (e.target.closest('.automation-run-btn')) soon(); });
            host.addEventListener('change', function (e) { if (e.target.closest('.automation-toggle')) soon(); });
        }
        if (_timer) clearInterval(_timer);
        _timer = setInterval(function () { if (onPage()) load(); else stop(); }, 8000);
    }
    function stop() { if (_timer) { clearInterval(_timer); _timer = null; } }

    document.addEventListener('soulsync:video-page-shown', function (e) {
        if (e.detail === 'video-automations') start(); else stop();
    });
})();
