/*
 * SoulSync — Video dashboard data layer.
 *
 * ISOLATION CONTRACT: like video-side.js this is a self-contained IIFE (no
 * globals, no inline handlers, lives under static/video/ which the script-split
 * integrity scan does not touch). It NEVER references music code, and music
 * never references it.
 *
 * It owns only the *data* of the video dashboard — the markup lives in
 * index.html and reuses music's .dash-card CSS for an identical look. It learns
 * when the dashboard becomes visible by listening for the
 * 'soulsync:video-page-shown' event that video-side.js dispatches (so the two
 * modules stay decoupled — no direct calls between them).
 *
 * Stats come from /api/video/dashboard (FALLBACK_STATS only covers a failed
 * fetch); the attention badges (open issues / pending maintenance findings)
 * ride their own endpoints so every subsystem surfaces on the landing page.
 */
(function () {
    'use strict';

    var DASHBOARD_ID = 'video-dashboard';

    var DASHBOARD_URL = '/api/video/dashboard';

    // System stats (uptime + memory) come from the SAME endpoint the music
    // dashboard uses — it's one machine, so these figures are identical on both
    // sides. Polled on the dashboard's 10s cadence for parity with music's push
    // loop. (Reached over HTTP, not music's socket, so the isolation contract
    // holds — no music-code reference.)
    var SYSTEM_STATS_URL = '/api/system/stats';
    var systemPollTimer = null;

    // Fallback only — shown if the /api/video/dashboard call fails. (uptime/memory
    // are NOT here — they come from the shared /api/system/stats via loadSystemStats.)
    var FALLBACK_STATS = {
        'active-downloads': '0',
        'finished-downloads': '0',
        'download-speed': '0 KB/s',
        'disk-usage': '--',
        'movies': '0',
        'shows': '0',
        'episodes': '0',
        'library-size': '--'
    };

    function formatBytes(n) {
        n = Number(n) || 0;
        if (n <= 0) return '0 B';
        var units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
        var i = Math.floor(Math.log(n) / Math.log(1024));
        if (i >= units.length) i = units.length - 1;
        return (n / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1) + ' ' + units[i];
    }

    function formatSpeed(bps) {
        return formatBytes(bps) + '/s';
    }

    // Map the API payload onto the flat data-video-stat keys in the markup.
    function flatten(d) {
        var lib = d.library || {}, dl = d.downloads || {};
        return {
            'active-downloads': String(dl.active != null ? dl.active : 0),
            'finished-downloads': String(dl.finished != null ? dl.finished : 0),
            'download-speed': formatSpeed(dl.speed_bps),
            'disk-usage': formatBytes(lib.size_bytes),
            'movies': String(lib.movies != null ? lib.movies : 0),
            'shows': String(lib.shows != null ? lib.shows : 0),
            'episodes': String(lib.episodes != null ? lib.episodes : 0),
            'library-size': formatBytes(lib.size_bytes)
        };
    }

    function applyStats(stats) {
        var nodes = document.querySelectorAll('[data-video-stat]');
        for (var i = 0; i < nodes.length; i++) {
            var key = nodes[i].getAttribute('data-video-stat');
            if (Object.prototype.hasOwnProperty.call(stats, key)) {
                nodes[i].textContent = stats[key];
            }
        }
    }

    function applyBadges(d) {
        var nodes = document.querySelectorAll('[data-video-badge]');
        for (var i = 0; i < nodes.length; i++) {
            var key = nodes[i].getAttribute('data-video-badge');
            if (d[key] != null) nodes[i].textContent = String(d[key]);
        }
    }

    function _esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
        });
    }
    // 'added 2h ago' style label from an ISO/SQL timestamp (UTC); '' when unknown.
    function _ago(ts) {
        if (!ts) return '';
        var t = Date.parse(String(ts).replace(' ', 'T') + (String(ts).indexOf('Z') === -1 ? 'Z' : ''));
        if (isNaN(t)) return '';
        var s = Math.max(0, (Date.now() - t) / 1000);
        if (s < 3600) return Math.max(1, Math.round(s / 60)) + 'm ago';
        if (s < 86400) return Math.round(s / 3600) + 'h ago';
        if (s < 86400 * 30) return Math.round(s / 86400) + 'd ago';
        return '';
    }
    function _recentSub(it) {
        var bits = [];
        if (it.year) bits.push(String(it.year));
        var ago = _ago(it.added_at);
        if (ago) bits.push(ago);
        return bits.length ? '<div class="video-recent-year">' + _esc(bits.join(' · ')) + '</div>' : '';
    }

    // Attention badges: open issues (everyone) + pending maintenance findings
    // (admins — the repair API is admin-gated; a 403 just leaves it hidden).
    // Issues/Findings are EXCEPTION states, not destinations (unlike Watchlist/
    // Wishlist) — both already have permanent homes (the Issues nav badge, the
    // Tools page). Their header buttons only appear when something actually
    // needs attention; at zero they stay out of the chrome entirely.
    function _toggleAttentionBtn(sel, count) {
        var btn = document.querySelector(sel);
        if (btn) btn.style.display = count > 0 ? '' : 'none';
    }

    function loadAttention() {
        fetch('/api/video/issues/counts', { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                var open = (d && d.counts && d.counts.open) || 0;
                applyBadges({ issues_open: open });
                _toggleAttentionBtn('[data-video-issues-btn]', open);
            }).catch(function () { /* button stays hidden */ });
        fetch('/api/video/repair/findings/counts', { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                var pending = (d && d.pending) || 0;
                applyBadges({ findings_pending: pending });
                _toggleAttentionBtn('[data-video-maint-btn]', pending);
            }).catch(function () { /* non-admin / unavailable → button stays hidden */ });
    }

    // The Studios are admin-only — non-admins got two prominent cards whose
    // buttons silently did nothing. Hide the cards outright for them.
    function gateStudioCards() {
        if (typeof currentProfile !== 'undefined' && currentProfile && !currentProfile.is_admin) {
            ['overlay-studio', 'collection-studio'].forEach(function (k) {
                var card = document.querySelector('.dash-card[data-card="' + k + '"]');
                if (card) card.style.display = 'none';
            });
        }
    }

    // Render the "Recently Added" tiles (poster + title), newest first, each linking
    // to its detail page.
    function applyRecent(items) {
        var host = document.querySelector('[data-video-recent]');
        if (!host) return;
        items = items || [];
        if (!items.length) {
            host.innerHTML = '<div class="video-recent-empty">Nothing added yet.</div>';
            return;
        }
        host.innerHTML = items.map(function (it) {
            var href = '/video-detail/library/' + it.kind + '/' + it.id;
            var poster = '/api/video/poster/' + it.kind + '/' + it.id + '?w=160';
            return '<a class="video-recent-item" href="' + href + '" title="' + _esc(it.title) + '"' +
                ' data-video-card-open="' + _esc(it.kind) + '" data-video-card-id="' + it.id + '">' +
                '<div class="video-recent-poster"><img src="' + poster + '" alt="" loading="lazy" ' +
                'onerror="this.closest(\'.video-recent-poster\').classList.add(\'is-empty\')"></div>' +
                '<div class="video-recent-title">' + _esc(it.title) + '</div>' +
                _recentSub(it) +
                '</a>';
        }).join('');
    }

    // ── Upcoming (calendar preview) — mini-billboards for the next few episodes ──
    var CALENDAR_URL = '/api/video/calendar?days=2&scope=watchlist';
    // Minutes-since-midnight from an "HH:MM" / "h:MM PM" airs_time (null when unknown),
    // so today's episodes sort by when they actually air. Mirrors the calendar's airMins.
    function _airMins(s) {
        if (!s) return null;
        var m = String(s).trim().match(/^(\d{1,2}):(\d{2})/);
        if (!m) return null;
        var h = +m[1], mi = +m[2];
        if (/pm/i.test(s) && h < 12) h += 12;
        if (/am/i.test(s) && h === 12) h = 0;
        if (h > 23 || mi > 59 || (h === 0 && mi === 0)) return null;
        return h * 60 + mi;
    }
    function _fmtMins(mins) {
        if (mins == null) return '';
        var h = (mins / 60) | 0, mi = mins % 60, ap = h >= 12 ? 'PM' : 'AM', hh = h % 12 || 12;
        return hh + ':' + ('0' + mi).slice(-2) + ' ' + ap;
    }
    // All rows are today's releases now, so lead with the air time; "Today" only when
    // the slot is unknown.
    function _whenLabel(ep) { return _fmtMins(_airMins(ep.airs_time)) || 'Today'; }
    // Full episode objects for whatever's currently rendered, keyed by ep.id — so a
    // click can hand the SAME object the calendar page uses to VideoCalendar.openEpisode().
    var _upcomingEps = {};
    function loadUpcoming() {
        var host = document.querySelector('[data-video-upcoming]');
        if (!host) return;
        fetch(CALENDAR_URL, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d || d.error) { host.innerHTML = '<p class="video-empty-note">Couldn\'t load the calendar.</p>'; return; }
                // The whole fetched window (today + tomorrow) — an empty today no
                // longer hides episodes sitting a day out. Soonest first, 10 max.
                var eps = (d.episodes || []).slice()
                    .sort(function (a, b) {
                        if ((a.air_date || '') !== (b.air_date || '')) {
                            return (a.air_date || '') < (b.air_date || '') ? -1 : 1;
                        }
                        var ta = _airMins(a.airs_time), tb = _airMins(b.airs_time);
                        if (ta == null) ta = 1e9;   // unknown air time sorts last
                        if (tb == null) tb = 1e9;
                        if (ta !== tb) return ta - tb;
                        return (a.show_title || '') < (b.show_title || '') ? -1 : 1;
                    }).slice(0, 10);
                if (!eps.length) { host.innerHTML = '<p class="video-empty-note">Nothing airing in the next couple of days — check the calendar for what\'s coming up.</p>'; return; }
                _upcomingEps = {};
                var hueOf = (window.VideoCalendar && window.VideoCalendar.showHue) || function () { return 230; };
                host.innerHTML = eps.map(function (ep) {
                    _upcomingEps[ep.id] = ep;
                    var bg = ep.show_has_backdrop ? '/api/video/backdrop/show/' + ep.show_id + '?w=640' : '';
                    var se = '<span class="vup-se">S' + ep.season_number + ' · E' + ep.episode_number + '</span>';
                    var owned = ep.has_file ? '<span class="vup-owned">✓ Owned</span>' : '';
                    // href is the show page (modified-click / new-tab fallback); a plain
                    // click opens the episode modal via the delegated handler below.
                    // --vcal-h is the same per-show hue the calendar billboard uses.
                    return '<a class="vup-row" style="--vcal-h:' + hueOf(ep.show_title || '') + '"' +
                        ' href="/video-detail/library/show/' + ep.show_id + '"' +
                        ' data-video-cal-ep="' + ep.id + '" title="' + _esc(ep.show_title) + '">' +
                        (bg ? '<div class="vup-bg" style="background-image:url(\'' + bg + '\')"></div>' : '') +
                        '<div class="vup-scrim"></div>' +
                        '<div class="vup-content">' +
                            '<div class="vup-when"><span class="vup-dot"></span>' +
                            (ep.air_date !== d.today ? 'Tomorrow · ' : '') + _esc(_whenLabel(ep)) + owned + '</div>' +
                            '<div class="vup-title">' + _esc(ep.show_title) + '</div>' +
                            '<div class="vup-sub">' + se + (ep.title ? ' · ' + _esc(ep.title) : '') + '</div>' +
                        '</div></a>';
                }).join('');
            })
            .catch(function () { host.innerHTML = '<p class="video-empty-note">Couldn\'t load the calendar.</p>'; });
    }

    function loadStats() {
        fetch(DASHBOARD_URL, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (d && !d.error) {
                    applyStats(flatten(d));
                    applyBadges(d);
                    applyRecent(d.recent);
                } else {
                    applyStats(FALLBACK_STATS);
                }
            })
            .catch(function () { applyStats(FALLBACK_STATS); });
    }

    // True when the video dashboard subpage is the one currently shown (subpages
    // toggle via the `hidden` attribute in video-side.js).
    function dashboardVisible() {
        var el = document.querySelector('.video-subpage[data-video-subpage="' + DASHBOARD_ID + '"]');
        return !!el && !el.hidden;
    }

    // Pull the shared system stats and reflect uptime + memory on the cards. The
    // rest of the dashboard's figures (video downloads, library) come from
    // /api/video/dashboard via loadStats(); only the machine-level numbers are
    // shared. applyStats only touches the keys we pass, so nothing else is clobbered.
    function loadSystemStats() {
        fetch(SYSTEM_STATS_URL, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d) return;
                applyStats({
                    'uptime': d.uptime != null ? String(d.uptime) : '--',
                    'memory': d.memory_usage != null ? String(d.memory_usage) : '--'
                });
            })
            .catch(function () { /* keep last-known values on a transient failure */ });
    }

    // Keep the system figures live while the dashboard is open — one 10s timer
    // that no-ops cheaply whenever the dashboard isn't the visible page.
    function startSystemStatsPolling() {
        if (systemPollTimer) return;
        systemPollTimer = setInterval(function () {
            if (dashboardVisible()) loadSystemStats();
        }, 10000);
    }

    // ── System health strip (roots/disk/recycle/maintenance/monitor) ────────
    function esc(t) {
        return String(t == null ? '' : t)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function loadHealth() {
        var host = document.querySelector('[data-vdash-health]');
        if (!host) return;
        fetch('/api/video/health', { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (h) {
                if (!h || !(h.checks || []).length) { host.hidden = true; host.innerHTML = ''; return; }
                var icons = { error: '🔴', warning: '⚠️' };
                host.innerHTML = h.checks.map(function (c) {
                    return '<div class="vdash-health-chip vdash-health-chip--' + c.status + '">' +
                        (icons[c.status] || 'ℹ️') + ' <strong>' + esc(c.label) + ':</strong> ' +
                        esc(c.detail) + '</div>';
                }).join('');
                host.hidden = false;
            }).catch(function () { host.hidden = true; });
    }

    function onPageShown(e) {
        if (!e || e.detail !== DASHBOARD_ID) return;
        loadHealth();
        loadStats();
        loadUpcoming();
        loadAttention();            // open issues + pending maintenance findings
        gateStudioCards();
        loadSystemStats();          // immediate fill (memory/uptime)
        startSystemStatsPolling();  // then keep it live
    }

    // ── Library card: live scan progress (parity with the music dashboard) ──
    // The scan buttons (data-video-scan-mode) are wired by video-scan.js; here
    // we reflect progress on the card and hydrate if a scan is already running
    // (video-scan.js re-emits the progress event on load).
    function dashButtons() {
        return document.querySelectorAll(
            '.video-subpage[data-video-subpage="video-dashboard"] [data-video-scan-mode]');
    }

    function onDashScanProgress(e) {
        var s = e.detail || {};
        if (s.state !== 'scanning') return;
        var prog = document.querySelector('[data-video-dash-progress]');
        if (prog) prog.classList.remove('hidden');
        var phase = (s.phase || 'scanning');
        var phaseEl = document.querySelector('[data-video-dash-phase]');
        if (phaseEl) phaseEl.textContent = phase.charAt(0).toUpperCase() + phase.slice(1);
        var bar = document.querySelector('[data-video-dash-bar]');
        if (bar) bar.style.width = (s.percent != null ? s.percent : 100) + '%';
        var detail = document.querySelector('[data-video-dash-detail]');
        if (detail) {
            detail.textContent = (s.movies || 0) + ' movies, ' + (s.shows || 0) + ' shows'
                + (s.percent != null ? ' · ' + s.percent + '%' : '');
        }
        var btns = dashButtons();
        for (var i = 0; i < btns.length; i++) btns[i].disabled = true;
    }

    function onDashScanDone() {
        var prog = document.querySelector('[data-video-dash-progress]');
        if (prog) prog.classList.add('hidden');
        var btns = dashButtons();
        for (var i = 0; i < btns.length; i++) btns[i].disabled = false;
        loadStats();
    }

    // Poster Manager quick-action tile → open the full-screen poster picker (its
    // own self-contained module). Delegated so it survives dashboard re-renders.
    document.addEventListener('click', function (e) {
        var t = e.target.closest && e.target.closest('[data-video-poster-manager]');
        if (!t) return;
        e.preventDefault();
        if (window.VideoPoster) VideoPoster.openSearch();
    });

    // Overlay Studio launcher → the full-bleed overlay-template editor.
    document.addEventListener('click', function (e) {
        var t = e.target.closest && e.target.closest('[data-video-overlay-studio]');
        if (!t) return;
        e.preventDefault();
        // Overlay Studio is admin-only (defense in depth behind the hidden launcher).
        if (typeof currentProfile !== 'undefined' && currentProfile && !currentProfile.is_admin) return;
        if (window.VideoOverlayEditor) VideoOverlayEditor.open();
    });

    // Collection Studio launcher → the full-bleed collection builder pseudo-page.
    document.addEventListener('click', function (e) {
        var t = e.target.closest && e.target.closest('[data-video-collection-studio]');
        if (!t) return;
        e.preventDefault();
        // Admin-only (defense in depth behind the hidden launcher).
        if (typeof currentProfile !== 'undefined' && currentProfile && !currentProfile.is_admin) return;
        if (window.VideoCollectionEditor) VideoCollectionEditor.open();
    });

    // Recently Added tiles → SPA detail navigation (same contract as the library
    // grid): plain left-click routes in-app; modified clicks use the real href.
    document.addEventListener('click', function (e) {
        if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        var card = e.target.closest && e.target.closest('[data-video-recent] [data-video-card-open]');
        if (!card) return;
        e.preventDefault();
        document.dispatchEvent(new CustomEvent('soulsync:video-open-detail', {
            detail: { kind: card.getAttribute('data-video-card-open'),
                      id: parseInt(card.getAttribute('data-video-card-id'), 10), source: 'library' },
        }));
    });

    // Upcoming cards → the calendar's episode modal (which itself has an "open full
    // show" button). Plain left-click opens the modal; modified clicks fall through
    // to the card's href (the show page) so new-tab still works.
    document.addEventListener('click', function (e) {
        if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        var card = e.target.closest && e.target.closest('[data-video-upcoming] [data-video-cal-ep]');
        if (!card) return;
        var ep = _upcomingEps[card.getAttribute('data-video-cal-ep')];
        if (!ep || !window.VideoCalendar || !window.VideoCalendar.openEpisode) return;  // fall through to href
        e.preventDefault();
        window.VideoCalendar.openEpisode(ep);
    });

    document.addEventListener('soulsync:video-page-shown', onPageShown);
    document.addEventListener('soulsync:video-scan-progress', onDashScanProgress);
    document.addEventListener('soulsync:video-scan-done', onDashScanDone);
})();
