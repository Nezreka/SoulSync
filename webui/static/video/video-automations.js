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

    function renderSystem(sys) {
        var host = document.querySelector('[data-vauto-list]'); if (!host) return;
        var emptyEl = document.querySelector('[data-vauto-empty]');
        var existing = host.querySelector('#vauto-section-system');
        if (!sys.length) {
            if (existing) existing.remove();
            if (emptyEl) emptyEl.style.display = '';
            return;
        }
        if (emptyEl) emptyEl.style.display = 'none';
        if (typeof window._buildAutomationSection !== 'function') return;
        // exact same section the music page builds for its System group (unique id so
        // it never clashes with the real music page's #auto-section-system).
        var section = window._buildAutomationSection('vauto-section-system', 'System', sys, true, { isProtected: true });
        if (existing) host.replaceChild(section, existing);
        else host.insertBefore(section, host.firstChild);
    }

    // The hub's built-in tabs are MUSIC content (playlist pipelines, music recipes,
    // music quick-start/tips). The video side will get its own content here; for now
    // empty those four panes (Reference stays — it's generic automation reference).
    var _HUB_EMPTY = { pipelines: 'pipelines', recipes: 'singles', guides: 'quick-start guides', tips: 'tips' };

    function renderHubOnce() {
        var host = document.querySelector('[data-vauto-list]'); if (!host) return;
        if (host.querySelector('#auto-section-hub')) return;   // build it once; it's static
        if (typeof window._buildAutomationHub !== 'function') return;
        var hub = window._buildAutomationHub();
        Object.keys(_HUB_EMPTY).forEach(function (t) {
            var pane = hub.querySelector('#auto-hub-pane-' + t);   // scoped to the detached hub (no id clash)
            if (pane) pane.innerHTML = '<div class="vauto-hub-soon">Video ' + _HUB_EMPTY[t] + ' coming soon.</div>';
        });
        host.appendChild(hub);
    }

    function renderStats(sys) {
        var el = document.querySelector('[data-vauto-stats]'); if (!el) return;
        if (!sys.length) { el.innerHTML = ''; return; }
        var active = sys.filter(function (a) { return a.enabled; }).length;
        el.innerHTML = '<span class="auto-stat"><strong>' + active + '</strong> Active</span>' +
            '<span class="auto-stat"><strong>' + sys.length + '</strong> System</span>';
    }

    function load() {
        getJSON('/api/automations').then(function (d) {
            var all = Array.isArray(d) ? d : (d && d.automations) || [];
            var sys = all.filter(isVideoAutomation);
            renderStats(sys);
            renderSystem(sys);
            renderHubOnce();
        });
    }

    function onPage() {
        return document.body.getAttribute('data-side') === 'video' &&
            !!document.querySelector('[data-video-subpage="video-automations"]:not([hidden])');
    }

    function start() {
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
