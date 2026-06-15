/*
 * SoulSync — Video settings additions (isolated).
 *
 * The video side shows the real music settings page; this module only drives the
 * VIDEO-specific bits added to it — for now, the Movies/TV library mapping
 * (which server library the scan reads). It populates the dropdowns from
 * /api/video/libraries when the Settings page is shown on the video side and
 * saves the choice back. Self-contained IIFE, no globals, no inline handlers.
 */
(function () {
    'use strict';

    var PAGE_ID = 'video-settings';
    var URL = '/api/video/libraries';
    var CONFIG_URL = '/api/video/enrichment/config';

    function status(text) {
        var n = document.querySelector('[data-video-lib-status]');
        if (n) n.textContent = text || '';
    }

    function fill(select, items, selected) {
        if (!select) return;
        select.textContent = '';
        var none = document.createElement('option');
        none.value = '';
        none.textContent = '— None —';
        select.appendChild(none);
        for (var i = 0; i < items.length; i++) {
            var opt = document.createElement('option');
            opt.value = items[i].title;
            opt.textContent = items[i].title;
            if (items[i].title === selected) opt.selected = true;
            select.appendChild(opt);
        }
    }

    function load() {
        status('Loading…');
        fetch(URL, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d || d.error) { status('Could not load libraries'); return; }
                var sel = d.selected || {};
                fill(document.querySelector('[data-video-lib-select="movies"]'), d.movies || [], sel.movies);
                fill(document.querySelector('[data-video-lib-select="tv"]'), d.tv || [], sel.tv);
                status('');
            })
            .catch(function () { status('Could not load libraries'); });
    }

    function save() {
        var m = document.querySelector('[data-video-lib-select="movies"]');
        var t = document.querySelector('[data-video-lib-select="tv"]');
        status('Saving…');
        fetch(URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({ movies: m ? m.value : '', tv: t ? t.value : '' })
        })
            .then(function (r) { return r.json(); })
            .then(function () { status('Saved'); })
            .catch(function () { status('Save failed'); });
    }

    // ── Enrichment API keys (TMDB / TVDB) ───────────────────────────────────
    function loadKeys() {
        fetch(CONFIG_URL, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d) return;
                var t = document.getElementById('tmdb-api-key');
                var v = document.getElementById('tvdb-api-key');
                var o = document.getElementById('omdb-api-key');
                if (t && d.tmdb_api_key != null) t.value = d.tmdb_api_key;
                if (v && d.tvdb_api_key != null) v.value = d.tvdb_api_key;
                if (o && d.omdb_api_key != null) o.value = d.omdb_api_key;
                var ap = document.getElementById('video-billboard-autoplay');
                if (ap && d.billboard_autoplay != null) ap.checked = !!d.billboard_autoplay;
            })
            .catch(function () { /* ignore */ });
    }

    function saveAutoplay() {
        var ap = document.getElementById('video-billboard-autoplay');
        fetch(CONFIG_URL, {
            method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({ billboard_autoplay: ap ? ap.checked : true })
        }).catch(function () { /* ignore */ });
    }

    function saveKeys() {
        var t = document.getElementById('tmdb-api-key');
        var v = document.getElementById('tvdb-api-key');
        var o = document.getElementById('omdb-api-key');
        return fetch(CONFIG_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({
                tmdb_api_key: t ? t.value : '', tvdb_api_key: v ? v.value : '',
                omdb_api_key: o ? o.value : '',
            })
        }).catch(function () { /* ignore */ });
    }

    function toast(msg, type) {
        if (typeof showToast === 'function') showToast(msg, type);  // shared shell helper
    }

    // Mirrors music's testConnection(): save the key, then hit the test
    // endpoint, then toast the result. Isolated -> /api/video/enrichment/<svc>/test.
    function testConnection(svc) {
        var name = svc.toUpperCase();
        toast('Testing ' + name + ' connection…', 'info');
        saveKeys().then(function () {
            return fetch('/api/video/enrichment/' + svc + '/test',
                { method: 'POST', headers: { 'Accept': 'application/json' } });
        }).then(function (r) { return r.json(); }).then(function (res) {
            if (res && res.success) toast(res.message || (name + ' connection successful'), 'success');
            else toast(name + ' connection failed: ' + ((res && res.error) || 'unknown'), 'error');
        }).catch(function () { toast('Failed to test ' + name + ' connection', 'error'); });
    }

    function onPageShown(e) {
        if (e && e.detail !== PAGE_ID) return;
        load();
        loadKeys();
    }

    function init() {
        // Save the moment a library is picked — same behaviour as the music
        // 'Music Library' selector above (which saves on change, no button).
        var selects = document.querySelectorAll('[data-video-lib-select]');
        for (var i = 0; i < selects.length; i++) {
            selects[i].addEventListener('change', save);
        }
        // Enrichment keys save on blur/change (turns the workers on).
        ['tmdb-api-key', 'tvdb-api-key', 'omdb-api-key'].forEach(function (id) {
            var el = document.getElementById(id);
            if (el) el.addEventListener('change', saveKeys);
        });
        var autoplay = document.getElementById('video-billboard-autoplay');
        if (autoplay) autoplay.addEventListener('change', saveAutoplay);
        // Per-connection Test buttons (same behaviour as music's testConnection).
        var testBtns = document.querySelectorAll('[data-video-test-service]');
        for (var k = 0; k < testBtns.length; k++) {
            (function (b) {
                b.addEventListener('click', function () {
                    testConnection(b.getAttribute('data-video-test-service'));
                });
            })(testBtns[k]);
        }
        document.addEventListener('soulsync:video-page-shown', onPageShown);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
