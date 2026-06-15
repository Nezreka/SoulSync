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
            ? '<img src="' + esc(c.poster) + '" alt="" loading="lazy" ' +
              'onerror="this.outerHTML=\'<div class=&quot;vsr-poster-ph&quot;>' + fallback + '</div>\'">'
            : '<div class="vsr-poster-ph">' + fallback + '</div>';
        var owned = c.library_id != null;
        var ribbon = owned ? '<span class="vsr-ribbon vsr-ribbon--owned">In Library</span>'
            : '<span class="vsr-ribbon vsr-ribbon--preview">Preview</span>';
        var source = owned ? 'library' : 'tmdb';
        var id = owned ? c.library_id : c.tmdb_id;
        var href = '/video-detail/' + source + '/' + c.kind + '/' + id;
        var sub = [c.year, c.role].filter(Boolean).join(' · ');
        return '<a class="vsr-card" href="' + href + '" ' +
            'data-vp-open="' + c.kind + '" data-vp-source="' + source + '" data-vp-cid="' + id + '">' +
            '<div class="vsr-poster">' + img + ribbon +
            '<span class="vsr-play" aria-hidden="true">▶</span></div>' +
            '<div class="vsr-info"><span class="vsr-name" title="' + esc(c.title) + '">' + esc(c.title) +
            '</span><span class="vsr-sub">' + esc(sub) + '</span></div></a>';
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

    function renderKnownFor() {
        var section = q('[data-vp-known-section]'), host = q('[data-vp-known]');
        if (!section || !host || !data) return;
        // The credits arrive popularity-sorted → the top few are the "known for".
        var top = (data.credits || []).slice(0, 10);
        section.hidden = top.length < 3;             // only worth a rail if there are a few
        host.innerHTML = top.map(creditCard).join('');
    }

    function renderCredits() {
        var host = q('[data-vp-credits]');
        if (!host || !data) return;
        var credits = (data.credits || []).filter(function (c) {
            return tab === 'all' || c.kind === tab;
        });
        // Full filmography reads best chronologically (newest first); Known For
        // already covers the popular ones.
        credits.sort(function (a, b) { return (b.date || '').localeCompare(a.date || ''); });
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
        // Cinematic ambient backdrop sampled from the portrait (blurred in CSS).
        var page = root(), amb = q('[data-vp-ambient]');
        if (page) page.setAttribute('data-has-bg', d.photo ? '1' : '0');
        if (amb) amb.style.setProperty('--vp-bg', d.photo ? "url('" + d.photo + "')" : 'none');

        setText('[data-vp-name]', d.name);
        var meta = [];
        if (d.known_for) meta.push(d.known_for);
        var ls = lifespan(d); if (ls) meta.push(ls);
        if (d.place_of_birth) meta.push(d.place_of_birth);
        var m = q('[data-vp-meta]');
        if (m) m.innerHTML = meta.map(function (x) { return '<span>' + esc(x) + '</span>'; }).join('');

        var bio = q('[data-vp-bio]'), more = q('[data-vp-bio-more]');
        if (bio) { bio.textContent = d.biography || ''; bio.hidden = !d.biography; bio.classList.remove('vp-bio--open'); }
        if (more) { more.hidden = !((d.biography || '').length > 320); more.textContent = 'Read more'; }

        renderKnownFor(); renderTabs(); renderCredits();
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
        var ks = q('[data-vp-known-section]'); if (ks) ks.hidden = true;
        var k = q('[data-vp-known]'); if (k) k.innerHTML = '';
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
        var moreBtn = e.target.closest('[data-vp-bio-more]');
        if (moreBtn && r.contains(moreBtn)) {
            var bio = q('[data-vp-bio]');
            if (bio) {
                var open = bio.classList.toggle('vp-bio--open');
                moreBtn.textContent = open ? 'Read less' : 'Read more';
            }
            return;
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
