/*
 * SoulSync — Video Requests page (arr-parity P4): the in-app Overseerr.
 *
 * members ask for titles from preview detail pages, this page is where the
 * asks live. rows for the same title are one row ("Thomas and Kim asked"),
 * so an admin approves or declines it once and everyone who asked hears
 * about it. admins get one Approve button on waiting rows, everything else
 * (decline with a reason, pick seasons, open the title) sits in the ⋯ menu.
 * members see their own asks, with Withdraw in the menu.
 *
 * approval IS acquisition: the backend adds the title to the wishlist or
 * watchlist and the drain/RSS take over. the row keeps telling the story:
 * On the way until the title reaches the library, then In your library
 * (in_library / available_at from the backend).
 *
 * also owns window.VideoRequestSheet, the small season picker + reason
 * modals the detail page's Request button reuses. styled by .vreq-* in
 * video-side.css.
 */
(function () {
    'use strict';

    var PAGE_ID = 'video-requests';
    var state = { loaded: false, rows: [], tab: 'pending', quota: null };

    function $(s) { return document.querySelector(s); }
    function esc(s) {
        return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function toast(m, t) { if (typeof showToast === 'function') showToast(m, t); }
    function isAdmin() {
        // currentProfile is a top-level `let` in init.js — it is NOT a window
        // property, so it must be read by bare name; reading it off window is
        // always undefined and rendered everyone, admins included, as a member.
        var cp = (typeof currentProfile !== 'undefined') ? currentProfile : null;
        return !!(cp && (cp.is_admin || cp.id === 1));
    }

    // ── seasons + reason sheets (shared with the detail page) ───────────────
    var MONITOR_CHOICES = [
        { id: 'all', label: 'All seasons', hint: 'Everything that has aired, and what comes next' },
        { id: 'first_season', label: 'First season', hint: 'Try it out before committing' },
        { id: 'latest_season', label: 'Latest season', hint: 'Catch up on the newest one' },
        { id: 'future', label: 'Only new episodes', hint: 'Nothing old, just what airs from now' }
    ];
    var MONITOR_LABELS = { all: 'All seasons', first_season: 'First season',
        latest_season: 'Latest season', future: 'Only new episodes', pilot: 'Pilot only' };

    function openSheet(inner, onReady) {
        return new Promise(function (resolve) {
            var overlay = document.createElement('div');
            overlay.className = 'vreq-sheet-overlay';
            overlay.innerHTML = '<div class="vreq-sheet" role="dialog" aria-modal="true">' + inner + '</div>';
            var done = false;
            function close(value) {
                if (done) return;
                done = true;
                document.removeEventListener('keydown', onKey, true);
                overlay.classList.remove('vreq-sheet-overlay--in');
                setTimeout(function () { if (overlay.parentNode) overlay.parentNode.removeChild(overlay); }, 160);
                resolve(value);
            }
            function onKey(e) {
                if (e.key === 'Escape') { e.stopPropagation(); close(null); }
            }
            overlay.addEventListener('click', function (e) {
                if (e.target === overlay || e.target.closest('[data-vreq-sheet-cancel]')) close(null);
            });
            document.addEventListener('keydown', onKey, true);
            document.body.appendChild(overlay);
            requestAnimationFrame(function () { overlay.classList.add('vreq-sheet-overlay--in'); });
            onReady(overlay.querySelector('.vreq-sheet'), close);
        });
    }

    // resolves the chosen monitor id, or null when cancelled
    function pickSeasons(opts) {
        opts = opts || {};
        var current = opts.current || 'all';
        var choices = MONITOR_CHOICES.map(function (c) {
            return '<button type="button" class="vreq-choice' + (c.id === current ? ' is-on' : '') +
                '" data-vreq-choice="' + c.id + '">' +
                '<span class="vreq-choice-label">' + esc(c.label) + '</span>' +
                '<span class="vreq-choice-hint">' + esc(c.hint) + '</span></button>';
        }).join('');
        var inner =
            '<div class="vreq-sheet-head"><div class="vreq-sheet-title">' + esc(opts.heading || 'Which seasons?') + '</div>' +
            (opts.title ? '<div class="vreq-sheet-sub">' + esc(opts.title) + '</div>' : '') + '</div>' +
            '<div class="vreq-choices">' + choices + '</div>' +
            '<div class="vreq-sheet-foot">' +
                '<button type="button" class="vreq-btn vreq-btn--ghost" data-vreq-sheet-cancel>Cancel</button>' +
                '<button type="button" class="vreq-btn vreq-btn--primary" data-vreq-sheet-go>' + esc(opts.confirm || 'Request') + '</button>' +
            '</div>';
        return openSheet(inner, function (sheet, close) {
            var picked = current;
            sheet.addEventListener('click', function (e) {
                var c = e.target.closest('[data-vreq-choice]');
                if (c) {
                    picked = c.getAttribute('data-vreq-choice');
                    sheet.querySelectorAll('[data-vreq-choice]').forEach(function (b) {
                        b.classList.toggle('is-on', b === c);
                    });
                    return;
                }
                if (e.target.closest('[data-vreq-sheet-go]')) close(picked);
            });
            var on = sheet.querySelector('.vreq-choice.is-on') || sheet.querySelector('[data-vreq-choice]');
            if (on) on.focus();
        });
    }

    // resolves the reason ('' when left blank), or null when cancelled
    function askReason(opts) {
        opts = opts || {};
        var inner =
            '<div class="vreq-sheet-head"><div class="vreq-sheet-title">' + esc(opts.heading || 'Decline request') + '</div>' +
            (opts.title ? '<div class="vreq-sheet-sub">' + esc(opts.title) + '</div>' : '') + '</div>' +
            '<label class="vreq-sheet-label" for="vreq-reason-in">Reason (optional)</label>' +
            '<textarea id="vreq-reason-in" class="vreq-sheet-input" rows="3" maxlength="500" placeholder="' +
                esc(opts.placeholder || 'They see this') + '"></textarea>' +
            '<div class="vreq-sheet-foot">' +
                '<button type="button" class="vreq-btn vreq-btn--ghost" data-vreq-sheet-cancel>Cancel</button>' +
                '<button type="button" class="vreq-btn vreq-btn--danger" data-vreq-sheet-go>' + esc(opts.confirm || 'Decline') + '</button>' +
            '</div>';
        return openSheet(inner, function (sheet, close) {
            var input = sheet.querySelector('textarea');
            sheet.addEventListener('click', function (e) {
                if (e.target.closest('[data-vreq-sheet-go]')) close((input.value || '').trim());
            });
            if (input) input.focus();
        });
    }

    window.VideoRequestSheet = { pickSeasons: pickSeasons, askReason: askReason, monitorLabel: function (m) {
        return MONITOR_LABELS[m] || '';
    } };

    // ── grouping ────────────────────────────────────────────────────────────
    // where a single row is at: pending | onway | available | denied
    function bucketOf(r) {
        if (r.status === 'pending') return 'pending';
        if (r.status === 'denied') return 'denied';
        return (r.in_library || r.available_at) ? 'available' : 'onway';
    }

    // one group per title per bucket, newest ask first inside the group
    function groupRows(rows) {
        var byKey = {}, order = [];
        rows.forEach(function (r) {
            var bucket = bucketOf(r);
            var key = bucket + ':' + r.kind + ':' + r.tmdb_id;
            var g = byKey[key];
            if (!g) {
                g = byKey[key] = { key: key, bucket: bucket, kind: r.kind, tmdb_id: r.tmdb_id,
                    title: r.title, year: r.year, poster_url: r.poster_url, monitor: r.monitor,
                    admin_response: r.admin_response, rows: [] };
                order.push(g);
            }
            g.rows.push(r);
            if (!g.poster_url && r.poster_url) g.poster_url = r.poster_url;
            if (!g.admin_response && r.admin_response) g.admin_response = r.admin_response;
        });
        return order;
    }

    function joinNames(names) {
        if (names.length <= 1) return names[0] || '';
        if (names.length === 2) return names[0] + ' and ' + names[1];
        return names.slice(0, -1).join(', ') + ' and ' + names[names.length - 1];
    }

    function whoAsked(g) {
        if (!isAdmin()) return 'You asked';
        var seen = {}, names = [];
        g.rows.forEach(function (r) {
            var n = r.requester_name || 'Someone';
            if (!seen[n]) { seen[n] = 1; names.push(n); }
        });
        return joinNames(names) + ' asked';
    }

    var STATUS_TEXT = { pending: 'Waiting', onway: 'On the way', available: 'In your library', denied: 'Declined' };

    var TABS = [
        { id: 'pending', label: 'Waiting', counted: true },
        { id: 'onway', label: 'On the way', counted: true },
        { id: 'available', label: 'Available' },
        { id: 'denied', label: 'Declined' },
        { id: 'all', label: 'All' }
    ];

    function visibleGroups(groups) {
        if (state.tab === 'all') return groups;
        return groups.filter(function (g) { return g.bucket === state.tab; });
    }

    function emptyText() {
        if (state.tab === 'pending') {
            return isAdmin()
                ? 'Nothing waiting. When someone asks for a movie or show, it lands here.'
                : 'Nothing waiting. Hit Request on any movie or show you want.';
        }
        if (state.tab === 'onway') return 'Nothing on the way right now.';
        if (state.tab === 'available') return 'Nothing has arrived yet.';
        if (state.tab === 'denied') return 'Nothing declined.';
        return isAdmin() ? 'No requests yet.' : 'You haven’t asked for anything yet.';
    }

    // ── render ──────────────────────────────────────────────────────────────
    function row(g) {
        var poster = g.poster_url
            ? '<img class="vreq-poster" src="' + esc(g.poster_url) + '" alt="" loading="lazy" onerror="this.style.visibility=\'hidden\'">'
            : '<div class="vreq-poster vreq-poster--ph">' + (g.kind === 'movie' ? '🎬' : '📺') + '</div>';
        var sub = [g.year];
        if (g.kind === 'show' && MONITOR_LABELS[g.monitor]) sub.push(MONITOR_LABELS[g.monitor]);
        sub = sub.filter(Boolean).join(' · ');
        var notes = g.rows.filter(function (r) { return r.note; }).map(function (r) {
            return '<div class="vreq-note">“' + esc(r.note) + '”' +
                (isAdmin() && r.requester_name ? ' <span class="vreq-note-by">' + esc(r.requester_name) + '</span>' : '') + '</div>';
        }).join('');
        var first = g.rows[0];
        var approve = (g.bucket === 'pending' && isAdmin())
            ? '<button class="vreq-btn vreq-btn--primary" type="button" data-vreq-approve="' + first.id + '">Approve</button>'
            : '';
        return '<div class="vreq-row" data-vreq-row="' + esc(g.key) + '">' + poster +
            '<div class="vreq-main">' +
                '<div class="vreq-title"><span class="vreq-title-t">' + esc(g.title) + '</span>' +
                    '<span class="vreq-kind">' + (g.kind === 'movie' ? 'Movie' : 'Show') + '</span></div>' +
                (sub ? '<div class="vreq-sub">' + esc(sub) + '</div>' : '') +
                '<div class="vreq-who">' + esc(whoAsked(g)) + '</div>' +
                notes +
                (g.bucket === 'denied' && g.admin_response
                    ? '<div class="vreq-note vreq-note--admin">“' + esc(g.admin_response) + '”</div>' : '') +
            '</div>' +
            '<div class="vreq-actions">' +
                '<span class="vreq-status vreq-status--' + g.bucket + '">' + STATUS_TEXT[g.bucket] + '</span>' +
                approve +
                '<button class="vreq-more" type="button" data-vreq-more="' + esc(g.key) + '" aria-label="More">⋯</button>' +
            '</div>' +
        '</div>';
    }

    function renderToolbar(groups) {
        var host = $('[data-vreq-toolbar]');
        if (!host) return;
        var tabs = TABS.map(function (t) {
            var n = t.id === 'all' ? groups.length
                : groups.filter(function (g) { return g.bucket === t.id; }).length;
            return '<button type="button" class="vreq-tab' + (state.tab === t.id ? ' active' : '') +
                '" role="tab" aria-selected="' + (state.tab === t.id) + '" data-vreq-tab="' + t.id + '">' +
                t.label + (t.counted && n ? ' · ' + n : '') + '</button>';
        }).join('');
        var resolved = groups.some(function (g) { return g.bucket !== 'pending'; });
        var clear = resolved
            ? '<button type="button" class="vreq-clear" data-vreq-clear>Clear history</button>'
            : '';
        host.innerHTML = '<div class="vreq-tabs" role="tablist">' + tabs + '</div>' + clear;
    }

    // "2 of 3 requests left this week", or that they're used up
    function quotaLine(q) {
        if (!q || !(Number(q.limit) > 0)) return '';
        var limit = Number(q.limit), left = Math.max(0, Number(q.remaining) || 0), days = Number(q.days) || 7;
        var span = days === 1 ? 'today' : days === 7 ? 'this week' : days === 30 ? 'this month' : 'in the last ' + days + ' days';
        var noun = limit === 1 ? 'request' : 'requests';
        if (left <= 0) return limit === 1 ? 'You’ve used your request ' + span : 'You’ve used all ' + limit + ' requests ' + span;
        return left + ' of ' + limit + ' ' + noun + ' left ' + span;
    }

    // the header: the quiet quota line for members, approve all for admins
    function renderHead(groups) {
        var head = $('.vreq-head');
        if (!head) return;
        var text = head.querySelector('.vreq-head-text');
        if (!text) {
            text = document.createElement('div');
            text.className = 'vreq-head-text';
            while (head.firstChild) text.appendChild(head.firstChild);
            head.appendChild(text);
        }
        var quota = text.querySelector('.vreq-quota');
        var line = isAdmin() ? '' : quotaLine(state.quota);
        if (line && !quota) {
            quota = document.createElement('p');
            quota.className = 'vreq-quota';
            text.appendChild(quota);
        }
        if (quota) { quota.textContent = line; quota.hidden = !line; }

        var waiting = groups.filter(function (g) { return g.bucket === 'pending'; }).length;
        var btn = head.querySelector('[data-vreq-approve-all]');
        if (isAdmin() && waiting > 1) {
            if (!btn) {
                btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'vreq-btn';
                btn.setAttribute('data-vreq-approve-all', '');
                btn.textContent = 'Approve all';
                head.appendChild(btn);
            }
            btn.hidden = false;
            btn.setAttribute('data-count', String(waiting));
        } else if (btn) {
            btn.hidden = true;
        }
    }

    function render() {
        var subH = $('.vreq-sub-h');
        if (subH) {
            subH.textContent = isAdmin()
                ? 'What your household asked for. Approve once, everyone who asked hears about it.'
                : 'What you asked for, and where it’s at.';
        }
        var groups = groupRows(state.rows);
        state.groups = groups;
        renderHead(groups);
        renderToolbar(groups);
        var host = $('[data-vreq-list]');
        if (!host) return;
        var shown = visibleGroups(groups);
        host.innerHTML = shown.length
            ? shown.map(row).join('')
            : '<div class="vreq-empty">' + emptyText() + '</div>';
    }

    function findGroup(key) {
        var gs = state.groups || [];
        for (var i = 0; i < gs.length; i++) if (gs[i].key === key) return gs[i];
        return null;
    }

    function setBadge(n) {
        var b = $('[data-video-requests-badge]');
        if (!b) return;
        b.textContent = n;
        b.classList.toggle('hidden', !n);
    }

    function load() {
        state.loaded = true;
        fetch('/api/video/requests', { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d || !d.success) return;
                state.rows = d.requests || [];
                state.quota = d.quota || null;
                setBadge(d.pending || 0);
                render();
            })
            .catch(function () { /* keep last */ });
    }

    function act(url, method, body, okMsg) {
        return fetch(url, { method: method,
            headers: { 'Content-Type': 'application/json' },
            body: body == null ? undefined : JSON.stringify(body) })
            .then(function (r) { return r.json().catch(function () { return null; }).then(function (j) { return { ok: r.ok, j: j }; }); })
            .then(function (res) {
                if (!res.ok || !res.j || !res.j.success) {
                    toast((res.j && res.j.error) || 'That didn’t work', 'error');
                    return null;
                }
                if (typeof okMsg === 'function') okMsg = okMsg(res.j);
                if (okMsg) toast(okMsg, 'success');
                return res.j;
            })
            .catch(function () { toast('That didn’t work', 'error'); return null; })
            .then(function (j) { load(); return j; });
    }

    function approve(g, monitor, btn) {
        if (btn) { btn.disabled = true; btn.textContent = 'Approving…'; }
        var body = monitor ? { monitor: monitor } : {};
        return act('/api/video/requests/' + g.rows[0].id + '/approve', 'POST', body, function (j) {
            var who = (j.approved || 0) > 1 ? 'Everyone who asked' : (isAdmin() && g.rows[0].requester_name) || 'They';
            if (j.kind === 'movie') return 'Approved. ' + who + ' will hear when it lands';
            return 'Approved. Following the show' + (j.wished ? ', ' + j.wished + ' episodes wanted' : '');
        });
    }

    function decline(g) {
        var names = whoAsked(g).replace(/ asked$/, '');
        askReason({ title: g.title, placeholder: names ? names + ' sees this' : 'They see this' })
            .then(function (reason) {
                if (reason === null) return;
                act('/api/video/requests/' + g.rows[0].id + '/deny', 'POST', { response: reason }, 'Declined');
            });
    }

    function withdraw(g) {
        var go = function () {
            Promise.all(g.rows.map(function (r) {
                return fetch('/api/video/requests/' + r.id, { method: 'DELETE' });
            })).then(function () { toast('Request withdrawn', 'info'); load(); })
              .catch(function () { toast('That didn’t work', 'error'); load(); });
        };
        if (typeof showConfirmDialog !== 'function') { go(); return; }
        showConfirmDialog({ title: 'Withdraw request', message: 'Take back your request for ' + g.title + '?',
            confirmText: 'Withdraw', destructive: true }).then(function (yes) { if (yes) go(); });
    }

    function removeGroup(g) {
        Promise.all(g.rows.map(function (r) {
            return fetch('/api/video/requests/' + r.id, { method: 'DELETE' });
        })).then(load).catch(load);
    }

    // admin: every waiting title in one go, after a confirm naming the count
    function approveAll(btn) {
        var n = parseInt(btn.getAttribute('data-count'), 10) || 0;
        var go = function () {
            btn.disabled = true;
            btn.textContent = 'Approving…';
            act('/api/video/requests/approve-all', 'POST', {}, function (j) {
                var ok = j.approved || 0, bad = j.failed || 0;
                if (!ok && !bad) return 'Nothing was waiting';
                return 'Approved ' + ok + ' request' + (ok === 1 ? '' : 's') +
                    (bad ? ', ' + bad + ' couldn’t be added' : '. Everyone who asked will hear');
            }).then(function () { btn.disabled = false; btn.textContent = 'Approve all'; });
        };
        if (typeof showConfirmDialog !== 'function') { go(); return; }
        showConfirmDialog({
            title: 'Approve all ' + n + ' requests?',
            message: 'Everything waiting goes to the wishlist, and everyone who asked hears it’s on the way.',
            confirmText: 'Approve all'
        }).then(function (yes) { if (yes) go(); });
    }

    function clearResolved() {
        var go = function () {
            act('/api/video/requests/resolved', 'DELETE', null, function (j) {
                return 'Cleared ' + (j.removed || 0) + ' from history';
            });
        };
        if (typeof showConfirmDialog === 'function') {
            showConfirmDialog({
                title: 'Clear history',
                message: 'Remove every approved and declined request from this list? Approved titles keep downloading, this only clears the history.',
                confirmText: 'Clear',
                destructive: false
            }).then(function (yes) { if (yes) go(); });
        } else {
            go();
        }
    }

    // ── ⋯ menu ──────────────────────────────────────────────────────────────
    var menuEl = null;
    function closeMenu() {
        if (menuEl && menuEl.parentNode) menuEl.parentNode.removeChild(menuEl);
        menuEl = null;
        document.removeEventListener('click', onDocClick, true);
        window.removeEventListener('scroll', closeMenu, true);
        document.removeEventListener('keydown', onMenuKey, true);
    }
    function onDocClick(e) { if (menuEl && !menuEl.contains(e.target) && !e.target.closest('[data-vreq-more]')) closeMenu(); }
    function onMenuKey(e) { if (e.key === 'Escape') closeMenu(); }

    function openMenu(trigger, g) {
        var same = menuEl && menuEl._key === g.key;
        closeMenu();
        if (same) return;
        var items = [];
        var admin = isAdmin();
        if (g.bucket === 'pending' && admin) {
            if (g.kind === 'show') items.push({ id: 'seasons', label: 'Approve with seasons…' });
            items.push({ id: 'decline', label: 'Decline…', danger: true });
        }
        if (g.bucket === 'pending' && !admin) items.push({ id: 'withdraw', label: 'Withdraw', danger: true });
        items.push({ id: 'open', label: g.kind === 'movie' ? 'Open movie' : 'Open show' });
        if (g.bucket !== 'pending') items.push({ id: 'remove', label: 'Remove from history' });

        menuEl = document.createElement('div');
        menuEl.className = 'vreq-menu';
        menuEl.setAttribute('role', 'menu');
        menuEl._key = g.key;
        menuEl.innerHTML = items.map(function (it) {
            if (it.id === 'open') {
                return '<a class="vreq-menu-item" role="menuitem" href="/video-detail/tmdb/' + esc(g.kind) + '/' +
                    esc(g.tmdb_id) + '" data-vreq-menu="open">' + esc(it.label) + '</a>';
            }
            return '<button type="button" class="vreq-menu-item' + (it.danger ? ' vreq-menu-item--danger' : '') +
                '" role="menuitem" data-vreq-menu="' + it.id + '">' + esc(it.label) + '</button>';
        }).join('');
        document.body.appendChild(menuEl);
        var r = trigger.getBoundingClientRect();
        var w = menuEl.offsetWidth, h = menuEl.offsetHeight;
        var top = r.bottom + 6;
        if (top + h > window.innerHeight - 8) top = Math.max(8, r.top - h - 6);
        menuEl.style.top = top + 'px';
        menuEl.style.left = Math.max(8, Math.min(window.innerWidth - w - 8, r.right - w)) + 'px';
        menuEl.addEventListener('click', function (e) {
            var it = e.target.closest('[data-vreq-menu]');
            if (!it) return;
            var which = it.getAttribute('data-vreq-menu');
            closeMenu();
            if (which === 'open') return;   // the link does the navigating
            if (which === 'decline') decline(g);
            else if (which === 'withdraw') withdraw(g);
            else if (which === 'remove') removeGroup(g);
            else if (which === 'seasons') {
                pickSeasons({ title: g.title, current: g.monitor || 'all', heading: 'Approve which seasons?', confirm: 'Approve' })
                    .then(function (m) { if (m) approve(g, m); });
            }
        });
        setTimeout(function () {
            document.addEventListener('click', onDocClick, true);
            window.addEventListener('scroll', closeMenu, true);
            document.addEventListener('keydown', onMenuKey, true);
        }, 0);
        var firstItem = menuEl.querySelector('[data-vreq-menu]');
        if (firstItem) firstItem.focus();
    }

    function wire() {
        var page = $('.vreq-page');
        if (!page || page._wired) return;
        page._wired = true;
        page.addEventListener('click', function (e) {
            var tab = e.target.closest('[data-vreq-tab]');
            if (tab) {
                state.tab = tab.getAttribute('data-vreq-tab');
                render();
                return;
            }
            if (e.target.closest('[data-vreq-clear]')) { clearResolved(); return; }
            var all = e.target.closest('[data-vreq-approve-all]');
            if (all) { approveAll(all); return; }
            var ap = e.target.closest('[data-vreq-approve]');
            if (ap) {
                var rowEl = ap.closest('[data-vreq-row]');
                var g = rowEl && findGroup(rowEl.getAttribute('data-vreq-row'));
                if (g) approve(g, null, ap);
                return;
            }
            var more = e.target.closest('[data-vreq-more]');
            if (more) {
                var mg = findGroup(more.getAttribute('data-vreq-more'));
                if (mg) openMenu(more, mg);
            }
        });
    }

    function pollBadge() {
        fetch('/api/video/requests/counts', { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) { if (d && d.success) setBadge(d.pending || 0); })
            .catch(function () { /* non-critical */ });
    }

    function onPageShown(e) {
        if (!e || e.detail !== PAGE_ID) { closeMenu(); return; }
        wire();
        load();
    }

    // the server told this profile something about a request
    function onProfileNotify(e) {
        var link = e && e.detail && e.detail.link;
        if (link !== 'video-requests') return;
        pollBadge();
        var page = document.querySelector('[data-video-subpage="video-requests"]');
        if (state.loaded && page && !page.hidden) load();
    }

    function init() {
        document.addEventListener('soulsync:video-page-shown', onPageShown);
        window.addEventListener('soulsync:profile-notify', onProfileNotify);
        // badge without visiting the page — same lazy cadence as the other navs
        setTimeout(pollBadge, 4000);
        setInterval(pollBadge, 60000);
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
