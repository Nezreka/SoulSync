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
    var tab = 'all';            // kind filter: all | movie | show
    var own = 'all';            // ownership filter: all | owned | missing
    var dept = 'all';           // department filter: all | Acting | Directing | …
    var sortBy = 'newest';      // newest | oldest | popularity
    var ROLE_NOUNS = {
        Acting: 'Actor', Directing: 'Director', Writing: 'Writer', Production: 'Producer',
        Sound: 'Composer', Camera: 'Cinematographer', Editing: 'Editor', Creator: 'Creator',
    };

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
        var cb = window.VideoGet ? VideoGet.cardButton({ kind: c.kind, tmdbId: c.tmdb_id,
            libraryId: c.library_id, title: c.title, poster: c.poster, status: c.status, source: source }) : '';
        return '<a class="vsr-card" href="' + href + '" ' +
            'data-vp-open="' + c.kind + '" data-vp-source="' + source + '" data-vp-cid="' + id + '">' + cb +
            '<div class="vsr-poster">' + img + ribbon +
            '<span class="vsr-peek" aria-hidden="true">i</span></div>' +
            '<div class="vsr-info"><span class="vsr-name" title="' + esc(c.title) + '">' + esc(c.title) +
            '</span><span class="vsr-sub">' + esc(sub) + '</span></div></a>';
    }

    function tabBtn(attr, key, active, label, count) {
        return '<button class="vp-tab' + (active ? ' vp-tab--active' : '') + '" type="button" ' +
            attr + '="' + key + '">' + esc(label) +
            '<span class="vp-tab-count">' + count + '</span></button>';
    }

    function renderDept() {
        var host = q('[data-vp-dept]');
        if (!host || !data) return;
        var counts = {};
        (data.credits || []).forEach(function (c) {
            var dp = c.department || 'Other'; counts[dp] = (counts[dp] || 0) + 1;
        });
        var depts = Object.keys(counts);
        // Only worth a row for multi-hyphenates (actor-directors etc.).
        if (depts.length < 2) { host.innerHTML = ''; host.hidden = true; return; }
        host.hidden = false;
        depts.sort(function (a, b) { return counts[b] - counts[a]; });
        var html = tabBtn('data-vp-dept', 'all', dept === 'all', 'All', countWith({ dept: 'all' }));
        html += depts.map(function (dp) {
            return tabBtn('data-vp-dept', dp, dp === dept, dp, countWith({ dept: dp }));
        }).join('');
        host.innerHTML = html;
    }

    function renderTabs() {
        var host = q('[data-vp-tabs]');
        if (!host || !data) return;
        var defs = [['all', 'All'], ['movie', 'Movies'], ['show', 'TV']];
        host.innerHTML = defs.map(function (d) { return [d[0], d[1], countWith({ tab: d[0] })]; })
            .filter(function (d) { return d[2] > 0 || d[0] === 'all'; })
            .map(function (d) { return tabBtn('data-vp-tab', d[0], d[0] === tab, d[1], d[2]); }).join('');
    }

    function renderOwn() {
        var host = q('[data-vp-own]');
        if (!host || !data) return;
        var defs = [['all', 'All'], ['owned', 'In Library'], ['missing', 'Missing']];
        host.innerHTML = defs.map(function (d) {
            return tabBtn('data-vp-own', d[0], d[0] === own, d[1], countWith({ own: d[0] }));
        }).join('');
    }

    function applyFilters() {
        renderDept(); renderTabs(); renderOwn(); renderKnownFor(); renderCredits();
    }

    // ── filters (department + kind + ownership) ───────────────────────────────
    function matchKind(c, k) { return k === 'all' || c.kind === k; }
    function isOwned(c) { return c.library_id != null; }
    function matchOwn(c, o) { return o === 'all' || (o === 'owned' ? isOwned(c) : !isOwned(c)); }
    function matchDept(c, dp) { return dp === 'all' || (c.department || '') === dp; }
    function filtered() {
        return (data.credits || []).filter(function (c) {
            return matchDept(c, dept) && matchKind(c, tab) && matchOwn(c, own);
        });
    }
    // Count credits under the active filters, overriding one dimension — so every
    // chip's count reflects what you'd actually get if you clicked it.
    function countWith(o) {
        var dp = 'dept' in o ? o.dept : dept, k = 'tab' in o ? o.tab : tab, ow = 'own' in o ? o.own : own;
        return (data.credits || []).filter(function (c) {
            return matchDept(c, dp) && matchKind(c, k) && matchOwn(c, ow);
        }).length;
    }

    function renderKnownFor() {
        var section = q('[data-vp-known-section]'), host = q('[data-vp-known]');
        if (!section || !host || !data) return;
        // Credits arrive popularity-sorted → the top few of the FILTERED set are
        // the "known for" (so it tracks the owned/missing + kind filters too).
        var top = filtered().slice(0, 10);
        section.hidden = top.length < 3;             // only worth a rail if there are a few
        host.innerHTML = top.map(creditCard).join('');
        if (window.VideoWatchlist) VideoWatchlist.hydrate(host);
    }

    function renderCredits() {
        var host = q('[data-vp-credits]'), empty = q('[data-vp-credits-empty]');
        if (!host || !data) return;
        var credits = filtered();
        if (sortBy === 'popularity') {
            credits.sort(function (a, b) { return (b.popularity || 0) - (a.popularity || 0); });
        } else if (sortBy === 'oldest') {
            credits.sort(function (a, b) { return (a.date || '9999').localeCompare(b.date || '9999'); });
        } else {
            credits.sort(function (a, b) { return (b.date || '').localeCompare(a.date || ''); });
        }
        host.innerHTML = credits.map(creditCard).join('');
        if (window.VideoWatchlist) VideoWatchlist.hydrate(host);
        if (empty) {
            empty.hidden = credits.length > 0;
            if (!credits.length) {
                empty.textContent = own === 'owned' ? 'Nothing from this person in your library yet.'
                    : own === 'missing' ? 'You already have everything here. 🎉'
                        : 'No titles here.';
            }
        }
    }

    function lifespan(d) {
        if (!d.birthday && !d.deathday) return '';
        var by = (d.birthday || '').slice(0, 4);
        var dy = (d.deathday || '').slice(0, 4);
        return dy ? (by + ' – ' + dy) : (by ? 'Born ' + by : '');
    }

    // Per-person accent: sample the portrait's dominant vibrant colour (via the
    // same-origin image proxy, so the cross-origin canvas isn't tainted).
    function applyAccent(photoUrl) {
        var page = root();
        if (!page || !photoUrl) return;
        var img = new Image();
        img.crossOrigin = 'anonymous';
        img.onload = function () {
            try {
                var w = 24, h = 24, c = document.createElement('canvas'); c.width = w; c.height = h;
                var ctx = c.getContext('2d'); ctx.drawImage(img, 0, 0, w, h);
                var px = ctx.getImageData(0, 0, w, h).data;
                var best = null, bestScore = -1, fr = 0, fg = 0, fb = 0, n = 0;
                for (var i = 0; i < px.length; i += 4) {
                    var r = px[i], g = px[i + 1], b = px[i + 2], a = px[i + 3];
                    if (a < 128) continue;
                    var mx = Math.max(r, g, b), mn = Math.min(r, g, b), light = (mx + mn) / 2;
                    fr += r; fg += g; fb += b; n++;
                    if (light < 35 || light > 225) continue;
                    var sat = mx === 0 ? 0 : (mx - mn) / mx, score = sat * (mx / 255);
                    if (score > bestScore) { bestScore = score; best = [r, g, b]; }
                }
                if (!best && n) best = [Math.round(fr / n), Math.round(fg / n), Math.round(fb / n)];
                if (best && root()) root().style.setProperty('--vd-accent-rgb', best[0] + ', ' + best[1] + ', ' + best[2]);
            } catch (e) { /* tainted / no image — keep theme accent */ }
        };
        img.src = '/api/video/img?u=' + encodeURIComponent(photoUrl);
    }

    // ── photos gallery + lightbox (reuses the shared .vd-lightbox styles) ──────
    var photoFulls = [], lbIdx = 0;
    function renderPhotos(photos) {
        var sec = q('[data-vp-photos-section]'), host = q('[data-vp-photos]');
        photos = photos || [];
        photoFulls = photos.map(function (p) { return p.full; });
        if (!sec || !host) return;
        if (!photos.length) { sec.hidden = true; return; }
        sec.hidden = false;
        host.innerHTML = photos.map(function (p, i) {
            return '<button class="vp-photo-thumb" type="button" data-vp-shot="' + i + '">' +
                '<img src="' + esc(p.thumb) + '" alt="" loading="lazy"></button>';
        }).join('');
    }
    function openLightbox(idx) {
        if (!photoFulls.length) return;
        lbIdx = idx;
        var ov = document.getElementById('vp-lightbox');
        if (!ov) {
            ov = document.createElement('div'); ov.id = 'vp-lightbox'; ov.className = 'vd-lightbox';
            ov.addEventListener('click', function (e) {
                if (e.target.closest('[data-vp-lb-prev]')) lbStep(-1);
                else if (e.target.closest('[data-vp-lb-next]')) lbStep(1);
                else if (e.target === ov || e.target.closest('[data-vp-lb-close]')) closeLightbox();
            });
            document.body.appendChild(ov);
        }
        renderLb(); ov.classList.add('vd-lightbox--open');
    }
    function renderLb() {
        var ov = document.getElementById('vp-lightbox'); if (!ov) return;
        ov.innerHTML = '<button class="vd-lb-close" type="button" data-vp-lb-close aria-label="Close">&times;</button>' +
            '<button class="vd-lb-nav vd-lb-prev" type="button" data-vp-lb-prev aria-label="Previous">&lsaquo;</button>' +
            '<img class="vd-lb-img" src="' + esc(photoFulls[lbIdx]) + '" alt="">' +
            '<button class="vd-lb-nav vd-lb-next" type="button" data-vp-lb-next aria-label="Next">&rsaquo;</button>' +
            '<div class="vd-lb-count">' + (lbIdx + 1) + ' / ' + photoFulls.length + '</div>';
    }
    function lbStep(dir) { lbIdx = (lbIdx + dir + photoFulls.length) % photoFulls.length; renderLb(); }
    function closeLightbox() {
        var ov = document.getElementById('vp-lightbox');
        if (ov) { ov.classList.remove('vd-lightbox--open'); ov.innerHTML = ''; }
    }
    function lbOpen() {
        var ov = document.getElementById('vp-lightbox');
        return ov && ov.classList.contains('vd-lightbox--open');
    }

    function computeAge(birthday, deathday) {
        if (!birthday) return null;
        var b = new Date(birthday), end = deathday ? new Date(deathday) : new Date();
        if (isNaN(b.getTime()) || isNaN(end.getTime())) return null;
        var age = end.getFullYear() - b.getFullYear();
        var mo = end.getMonth() - b.getMonth();
        if (mo < 0 || (mo === 0 && end.getDate() < b.getDate())) age--;
        return (age >= 0 && age < 130) ? age : null;
    }

    function render(d) {
        data = d; tab = 'all'; own = 'all'; dept = 'all'; sortBy = 'newest';
        var ss = document.querySelector('[data-vp-sort]'); if (ss) ss.value = 'newest';
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
        applyAccent(d.photo);

        setText('[data-vp-name]', d.name);

        // Role tagline (a friendlier noun than the raw department).
        var role = q('[data-vp-role]');
        if (role) {
            var noun = ROLE_NOUNS[d.known_for] || d.known_for || '';
            role.textContent = noun; role.hidden = !noun;
        }

        var meta = [];
        var ls = lifespan(d), age = computeAge(d.birthday, d.deathday);
        if (d.deathday) {
            if (ls) meta.push(ls);
            if (age != null) meta.push('aged ' + age);
        } else if (age != null) {
            meta.push(age + ' years old');
        } else if (ls) {
            meta.push(ls);
        }
        if (d.place_of_birth) meta.push(d.place_of_birth);
        var n = (d.credits || []).length;
        if (n) meta.push(n + (n === 1 ? ' credit' : ' credits'));
        var m = q('[data-vp-meta]');
        if (m) m.innerHTML = meta.map(function (x) { return '<span>' + esc(x) + '</span>'; }).join('');

        var aka = q('[data-vp-aka]');
        if (aka) {
            var names = d.also_known_as || [];
            aka.textContent = names.length ? 'Also known as: ' + names.join(' · ') : '';
            aka.hidden = !names.length;
        }
        var bio = q('[data-vp-bio]'), more = q('[data-vp-bio-more]');
        if (bio) { bio.textContent = d.biography || ''; bio.hidden = !d.biography; bio.classList.remove('vp-bio--open'); }
        if (more) { more.hidden = !((d.biography || '').length > 320); more.textContent = 'Read more'; }
        renderWatchlist(d);
        renderPhotos(d.photos);

        applyFilters();
        var sub = document.querySelector('.video-subpage[data-video-subpage="video-person-detail"]');
        if (sub) sub.scrollTop = 0;
    }

    // ── watchlist (follow a person — same button as the movie/show pages) ──────
    // persons are tmdb-only, so currentId (the navigation id) IS the tmdb person id.
    function pid() { return currentId || (data && data.id); }
    function renderWatchlist(d) {
        var host = q('[data-vp-actions]'); if (!host) return;
        var on = !!d._vw_watched;
        host.innerHTML =
            '<button class="library-artist-watchlist-btn' + (on ? ' watching' : '') + '" type="button" data-vp-watch>' +
            '<span class="watchlist-icon">' + (on ? '✓' : '＋') + '</span>' +
            '<span class="watchlist-text">' + (on ? 'In Watchlist' : 'Watchlist') + '</span></button>';
        // Resolve the real watched state once (lazy), then re-render the button.
        var id = pid();
        if (!d._vw_checked && id) {
            d._vw_checked = true;
            fetch('/api/video/watchlist/check', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ kind: 'person', tmdb_ids: [id] }) })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (res) {
                    if (res && res.results && data === d) {
                        d._vw_watched = !!res.results[String(id)];
                        renderWatchlist(d);
                    }
                }).catch(function () { /* keep default (off) */ });
        }
    }
    function toggleWatch() {
        var d = data; if (!d) return;
        var id = pid(); if (!id) return;
        var on = !!d._vw_watched;
        var url = on ? '/api/video/watchlist/remove' : '/api/video/watchlist/add';
        var body = on ? { kind: 'person', tmdb_id: id }
            : { kind: 'person', tmdb_id: id, title: d.name, poster_url: d.photo || null };
        fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (res) {
                if (!res || res.success === false) {
                    if (typeof showToast === 'function') showToast('Watchlist update failed', 'error');
                    return;
                }
                d._vw_watched = !on;
                renderWatchlist(d);
                if (typeof showToast === 'function')
                    showToast(!on ? 'Added to watchlist' : 'Removed from watchlist', !on ? 'success' : 'info');
                document.dispatchEvent(new CustomEvent('soulsync:video-watchlist-changed',
                    { detail: { kind: 'person', id: String(id), watched: !on } }));
            }).catch(function () { if (typeof showToast === 'function') showToast('Watchlist update failed', 'error'); });
    }

    function load(id) {
        if (!root()) return;
        currentId = id;
        var pg = root(); if (pg) pg.style.removeProperty('--vd-accent-rgb');   // reset per-person accent
        showLoading(true);
        setText('[data-vp-name]', '');
        var m = q('[data-vp-meta]'); if (m) m.innerHTML = '';
        var c = q('[data-vp-credits]'); if (c) c.innerHTML = '';
        var t = q('[data-vp-tabs]'); if (t) t.innerHTML = '';
        var o = q('[data-vp-own]'); if (o) o.innerHTML = '';
        var dp = q('[data-vp-dept]'); if (dp) dp.innerHTML = '';
        var ce = q('[data-vp-credits-empty]'); if (ce) ce.hidden = true;
        var ks = q('[data-vp-known-section]'); if (ks) ks.hidden = true;
        var k = q('[data-vp-known]'); if (k) k.innerHTML = '';
        var psec = q('[data-vp-photos-section]'); if (psec) psec.hidden = true;
        var pho = q('[data-vp-photos]'); if (pho) pho.innerHTML = '';
        var ak = q('[data-vp-aka]'); if (ak) ak.hidden = true;
        photoFulls = [];
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
        var watchBtn = e.target.closest('[data-vp-watch]');
        if (watchBtn && r.contains(watchBtn)) { toggleWatch(); return; }
        var kindBtn = e.target.closest('[data-vp-tab]');
        if (kindBtn && r.contains(kindBtn)) {
            tab = kindBtn.getAttribute('data-vp-tab'); applyFilters(); return;
        }
        var ownBtn = e.target.closest('[data-vp-own]');
        if (ownBtn && r.contains(ownBtn)) {
            own = ownBtn.getAttribute('data-vp-own'); applyFilters(); return;
        }
        var deptBtn = e.target.closest('[data-vp-dept]');
        if (deptBtn && r.contains(deptBtn)) {
            dept = deptBtn.getAttribute('data-vp-dept'); applyFilters(); return;
        }
        var shot = e.target.closest('[data-vp-shot]');
        if (shot && r.contains(shot)) { openLightbox(parseInt(shot.getAttribute('data-vp-shot'), 10) || 0); return; }
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
        document.addEventListener('keydown', function (e) {
            if (!lbOpen()) return;
            if (e.key === 'Escape') closeLightbox();
            else if (e.key === 'ArrowLeft') lbStep(-1);
            else if (e.key === 'ArrowRight') lbStep(1);
        });
        var sortSel = document.querySelector('[data-vp-sort]');
        if (sortSel) sortSel.addEventListener('change', function () {
            sortBy = sortSel.value; renderCredits();
        });
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
