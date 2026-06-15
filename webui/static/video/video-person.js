/*
 * SoulSync — Video Person page (isolated, in-app).
 *
 * Drill-in for a cast/crew member (from a detail page or a search result). Shows
 * bio + a filmography grid; every credit links back into SoulSync — the owned
 * library detail when we have it, otherwise the TMDB-backed preview detail. No
 * external links.
 *
 * Opened by soulsync:video-open-detail {kind:'person', id, source:'tmdb'};
 * video-side.js navigates to the person subpage and this loads + renders.
 * Self-contained IIFE, no globals, event-delegated.
 */
(function () {
    'use strict';

    var PERSON_URL = '/api/video/person/';
    var data = null;
    var currentId = null;
    var tab = 'all';            // all | movie | show

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
    function root() { return document.querySelector('[data-video-person]'); }
    function q(sel) { var r = root(); return r ? r.querySelector(sel) : null; }
    function setText(sel, t) { var n = q(sel); if (n) n.textContent = t || ''; }
    function showLoading(on) { var l = q('[data-vp-loading]'); if (l) l.hidden = !on; }

    function creditCard(c) {
        var fallback = c.kind === 'movie' ? '🎬' : '📺';
        var img = c.poster
            ? '<div class="library-artist-image"><img src="' + esc(c.poster) + '" alt="" loading="lazy" ' +
              'onerror="this.parentNode.innerHTML=\'<div class=&quot;library-artist-image-fallback&quot;>' + fallback + '</div>\'"></div>'
            : '<div class="library-artist-image"><div class="library-artist-image-fallback">' + fallback + '</div></div>';
        var owned = c.library_id != null;
        var ribbon = owned ? '<div class="vsr-ribbon vsr-ribbon--owned">In Library</div>'
            : '<div class="vsr-ribbon vsr-ribbon--preview">Preview</div>';
        var meta = [];
        if (c.year) meta.push(String(c.year));
        if (c.role) meta.push(c.role);
        var source = owned ? 'library' : 'tmdb';
        var id = owned ? c.library_id : c.tmdb_id;
        var href = '/video-detail/' + source + '/' + c.kind + '/' + id;
        return '<a class="library-artist-card video-card--clickable vsr-card" href="' + href + '" ' +
            'data-vp-open="' + c.kind + '" data-vp-source="' + source + '" data-vp-cid="' + id + '">' +
            img + ribbon +
            '<div class="library-artist-info">' +
            '<h3 class="library-artist-name" title="' + esc(c.title) + '">' + esc(c.title) + '</h3>' +
            '<div class="library-artist-stats"><span class="library-artist-stat">' +
            esc(meta.join(' · ')) + '</span></div></div></a>';
    }

    function renderTabs() {
        var host = q('[data-vp-tabs]');
        if (!host || !data) return;
        var credits = data.credits || [];
        var movies = credits.filter(function (c) { return c.kind === 'movie'; }).length;
        var shows = credits.filter(function (c) { return c.kind === 'show'; }).length;
        var defs = [['all', 'All', credits.length], ['movie', 'Movies', movies], ['show', 'TV', shows]];
        host.innerHTML = defs.filter(function (d) { return d[2] > 0; }).map(function (d) {
            return '<button class="vp-tab' + (d[0] === tab ? ' vp-tab--active' : '') +
                '" type="button" data-vp-tab="' + d[0] + '">' + esc(d[1]) +
                '<span class="vp-tab-count">' + d[2] + '</span></button>';
        }).join('');
    }

    function renderCredits() {
        var host = q('[data-vp-credits]');
        if (!host || !data) return;
        var credits = (data.credits || []).filter(function (c) {
            return tab === 'all' || c.kind === tab;
        });
        host.innerHTML = credits.map(creditCard).join('');
    }

    function lifespan(d) {
        if (!d.birthday && !d.deathday) return '';
        var by = (d.birthday || '').slice(0, 4);
        var dy = (d.deathday || '').slice(0, 4);
        return dy ? (by + ' – ' + dy) : (by ? 'Born ' + by : '');
    }

    function render(d) {
        data = d; tab = 'all';
        var photo = q('[data-vp-photo]'), ph = q('[data-vp-photo-ph]');
        if (photo) {
            if (d.photo) {
                photo.src = d.photo; photo.hidden = false; if (ph) ph.hidden = true;
                photo.onerror = function () { photo.hidden = true; if (ph) ph.hidden = false; };
            } else { photo.hidden = true; if (ph) ph.hidden = false; }
        }
        setText('[data-vp-name]', d.name);
        var meta = [];
        if (d.known_for) meta.push(d.known_for);
        var ls = lifespan(d); if (ls) meta.push(ls);
        if (d.place_of_birth) meta.push(d.place_of_birth);
        var m = q('[data-vp-meta]');
        if (m) m.innerHTML = meta.map(function (x) { return '<span>' + esc(x) + '</span>'; }).join('');
        var bio = q('[data-vp-bio]');
        if (bio) { bio.textContent = d.biography || ''; bio.hidden = !d.biography; }
        renderTabs(); renderCredits();
        var sub = document.querySelector('.video-subpage[data-video-subpage="video-person-detail"]');
        if (sub) sub.scrollTop = 0;
    }

    function load(id) {
        if (!root()) return;
        currentId = id;
        showLoading(true);
        setText('[data-vp-name]', '');
        var m = q('[data-vp-meta]'); if (m) m.innerHTML = '';
        var c = q('[data-vp-credits]'); if (c) c.innerHTML = '';
        var t = q('[data-vp-tabs]'); if (t) t.innerHTML = '';
        fetch(PERSON_URL + id, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                showLoading(false);
                if (currentId !== id) return;
                if (!d || d.error) { setText('[data-vp-name]', 'Not found'); return; }
                render(d);
            })
            .catch(function () { showLoading(false); setText('[data-vp-name]', 'Could not load'); });
    }

    function onOpen(e) {
        if (!e || !e.detail || e.detail.kind !== 'person') return;
        load(e.detail.id);
    }

    function onClick(e) {
        var r = root(); if (!r) return;
        var tabBtn = e.target.closest('[data-vp-tab]');
        if (tabBtn && r.contains(tabBtn)) {
            tab = tabBtn.getAttribute('data-vp-tab'); renderTabs(); renderCredits(); return;
        }
        var card = e.target.closest('[data-vp-open]');
        if (card && r.contains(card)) {
            if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
            e.preventDefault();
            var id = parseInt(card.getAttribute('data-vp-cid'), 10);
            if (isNaN(id)) return;
            document.dispatchEvent(new CustomEvent('soulsync:video-open-detail', {
                detail: { kind: card.getAttribute('data-vp-open'), id: id,
                          source: card.getAttribute('data-vp-source') || 'tmdb' },
            }));
        }
    }

    function init() {
        document.addEventListener('soulsync:video-open-detail', onOpen);
        document.addEventListener('click', onClick);
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
