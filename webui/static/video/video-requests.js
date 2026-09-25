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
 * watchlist and the drain/RSS take over. the row keeps telling the story
 * from the backend's progress + state: On the way, 3 of 10 episodes (with a
 * thin bar), Partly here, Couldn't find it (admins get Search again), then
 * In your library.
 *
 * also owns window.VideoRequestSheet, the small season picker + reason
 * modals the detail page's Request button reuses, and window.VideoRequests:
 * the one request flow (quota check, seasons + quality sheet, post) plus the
 * Requested / Available ribbons on video cards for profiles that can't
 * download. styled by .vreq-* in video-side.css.
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

    // the quality select, only when there's more than the default to pick from
    function qualitySelectHtml(profiles, current) {
        if (!profiles || profiles.length < 2) return '';
        var cur = Number(current) || 0;
        return '<label class="vreq-sheet-label vreq-sheet-label--gap" for="vreq-quality-in">Quality</label>' +
            '<select id="vreq-quality-in" class="vreq-sheet-select" data-vreq-quality>' +
            profiles.map(function (p) {
                var id = Number(p.id) || 0;
                return '<option value="' + id + '"' + (id === cur ? ' selected' : '') + '>' +
                    esc(id === 0 ? 'Default' : p.name) + '</option>';
            }).join('') + '</select>';
    }

    // one sheet for both asks: which seasons (shows) and which quality (when
    // named profiles exist). resolves {monitor, quality_profile_id}, or null
    // when cancelled. monitor is null for movies, quality 0 means default.
    function pickRequest(opts) {
        opts = opts || {};
        var show = opts.kind !== 'movie';
        var current = opts.current || 'all';
        var choices = show ? MONITOR_CHOICES.map(function (c) {
            return '<button type="button" class="vreq-choice' + (c.id === current ? ' is-on' : '') +
                '" data-vreq-choice="' + c.id + '">' +
                '<span class="vreq-choice-label">' + esc(c.label) + '</span>' +
                '<span class="vreq-choice-hint">' + esc(c.hint) + '</span></button>';
        }).join('') : '';
        var heading = opts.heading || (show ? 'Which seasons?' : 'Request this movie');
        var inner =
            '<div class="vreq-sheet-head"><div class="vreq-sheet-title">' + esc(heading) + '</div>' +
            (opts.title ? '<div class="vreq-sheet-sub">' + esc(opts.title) + '</div>' : '') + '</div>' +
            (show ? '<div class="vreq-choices">' + choices + '</div>' : '') +
            qualitySelectHtml(opts.profiles, opts.quality) +
            '<div class="vreq-sheet-foot">' +
                '<button type="button" class="vreq-btn vreq-btn--ghost" data-vreq-sheet-cancel>Cancel</button>' +
                '<button type="button" class="vreq-btn vreq-btn--primary" data-vreq-sheet-go>' + esc(opts.confirm || 'Request') + '</button>' +
            '</div>';
        return openSheet(inner, function (sheet, close) {
            var picked = show ? current : null;
            sheet.addEventListener('click', function (e) {
                var c = e.target.closest('[data-vreq-choice]');
                if (c) {
                    picked = c.getAttribute('data-vreq-choice');
                    sheet.querySelectorAll('[data-vreq-choice]').forEach(function (b) {
                        b.classList.toggle('is-on', b === c);
                    });
                    return;
                }
                if (e.target.closest('[data-vreq-sheet-go]')) {
                    var sel = sheet.querySelector('[data-vreq-quality]');
                    close({ monitor: picked, quality_profile_id: sel ? (Number(sel.value) || 0) : 0 });
                }
            });
            var on = sheet.querySelector('.vreq-choice.is-on') || sheet.querySelector('[data-vreq-choice]') ||
                sheet.querySelector('[data-vreq-quality]') || sheet.querySelector('[data-vreq-sheet-go]');
            if (on) on.focus();
        });
    }

    // resolves the chosen monitor id, or null when cancelled
    function pickSeasons(opts) {
        var o = {};
        for (var k in (opts || {})) o[k] = opts[k];
        o.kind = 'show';
        return pickRequest(o).then(function (res) { return res ? res.monitor : null; });
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

    window.VideoRequestSheet = { pickSeasons: pickSeasons, pickRequest: pickRequest, askReason: askReason,
        monitorLabel: function (m) { return MONITOR_LABELS[m] || ''; } };

    // ── where an approved request stands ───────────────────────────────────
    // the backend stamps approved rows with progress {owned, wanted, failed,
    // total} and state available | partial | failed | on_the_way. rows from
    // before that (or a db hiccup) fall back to in_library / available_at.
    function progressOf(r) {
        var p = (r && r.progress) || {};
        return { owned: Number(p.owned) || 0, wanted: Number(p.wanted) || 0,
            failed: Number(p.failed) || 0, total: Number(p.total) || 0 };
    }

    // where a single row is at: pending | onway | available | denied
    function bucketOf(r) {
        if (r.status === 'pending') return 'pending';
        if (r.status === 'denied') return 'denied';
        if (r.state) return r.state === 'available' ? 'available' : 'onway';
        return (r.in_library || r.available_at) ? 'available' : 'onway';
    }

    // the status words + tone for a row. tone picks the colour:
    // pending | onway | partial | failed | available | denied
    function requestStatus(r) {
        if (!r || r.status === 'pending') return { text: 'Waiting', tone: 'pending' };
        if (r.status === 'denied') return { text: 'Declined', tone: 'denied' };
        var st = r.state;
        if (!st) {
            return (r.in_library || r.available_at)
                ? { text: 'In your library', tone: 'available' }
                : { text: 'On the way', tone: 'onway' };
        }
        var p = progressOf(r);
        var show = r.kind === 'show';
        if (st === 'available') return { text: 'In your library', tone: 'available' };
        if (st === 'failed') return { text: 'Couldn’t find it', tone: 'failed' };
        if (st === 'partial') {
            if (show && p.wanted > 0 && p.total > 0) {
                return { text: p.owned + ' of ' + p.total + ' episodes' +
                    (p.failed ? ' · ' + p.failed + ' failed' : ''), tone: 'partial' };
            }
            return { text: 'Partly here' + (p.failed ? ' · ' + p.failed + ' failed' : ''), tone: 'partial' };
        }
        return { text: 'On the way', tone: 'onway' };
    }

    // 0-100 for a show still arriving, null when a bar would say nothing
    function progressPct(r) {
        if (!r || r.kind !== 'show' || r.status !== 'approved') return null;
        if (r.state !== 'partial' && r.state !== 'on_the_way') return null;
        var p = progressOf(r);
        if (!(p.total > 0)) return null;
        return Math.max(0, Math.min(100, Math.round((p.owned / p.total) * 100)));
    }

    // is there something left for a search to find
    function canSearchAgain(r) {
        if (!r || r.status !== 'approved') return false;
        return r.state === 'failed' || (r.state === 'partial' && progressOf(r).failed > 0);
    }

    // the chosen profile's name, '' for the default or one that's gone
    function qualityName(profiles, id) {
        var n = Number(id) || 0;
        if (!n) return '';
        for (var i = 0; i < (profiles || []).length; i++) {
            if (Number(profiles[i].id) === n) return profiles[i].name || '';
        }
        return '';
    }

    // what a card should say for this profile's asks, keyed kind:tmdb_id.
    // 'available' beats 'requested'; declined asks say nothing.
    function cardStates(rows) {
        var out = {};
        (rows || []).forEach(function (r) {
            if (!r || !r.tmdb_id || r.status === 'denied') return;
            var key = r.kind + ':' + r.tmdb_id;
            var st = (r.status === 'approved' && bucketOf(r) === 'available') ? 'available' : 'requested';
            if (out[key] !== 'available') out[key] = st;
        });
        return out;
    }

    function quotaSpent(q) {
        return !!(q && Number(q.limit) > 0 && (Number(q.remaining) || 0) <= 0);
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
                    quality_profile_id: r.quality_profile_id || null,
                    admin_response: r.admin_response, lead: r, rows: [] };
                order.push(g);
            }
            g.rows.push(r);
            // every row of a title shares its progress; keep one that carries it
            if (!g.lead.state && r.state) g.lead = r;
            if (!g.quality_profile_id && r.quality_profile_id) g.quality_profile_id = r.quality_profile_id;
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
        sub.push(qualityName(profilesCache.list, g.quality_profile_id));
        sub = sub.filter(Boolean).join(' · ');
        var st = requestStatus(g.lead);
        var pct = progressPct(g.lead);
        var bar = pct == null ? ''
            : '<div class="vreq-progress" role="progressbar" aria-label="Episodes here" aria-valuemin="0"' +
              ' aria-valuemax="100" aria-valuenow="' + pct + '"><span style="width:' + pct + '%"></span></div>';
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
                bar +
                '<div class="vreq-who">' + esc(whoAsked(g)) + '</div>' +
                notes +
                (g.bucket === 'denied' && g.admin_response
                    ? '<div class="vreq-note vreq-note--admin">“' + esc(g.admin_response) + '”</div>' : '') +
            '</div>' +
            '<div class="vreq-actions">' +
                '<span class="vreq-status vreq-status--' + st.tone + '">' + esc(st.text) + '</span>' +
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
        // the named quality profiles label rows and fill the approve sheet
        loadProfiles().then(function () { if (state.groups) render(); });
        fetch('/api/video/requests', { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d || !d.success) return;
                state.rows = d.requests || [];
                state.quota = d.quota || null;
                setBadge(d.pending || 0);
                remember(d);
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

    // opts: {monitor?, quality_profile_id?} from the approve sheet
    function approve(g, opts, btn) {
        if (btn) { btn.disabled = true; btn.textContent = 'Approving…'; }
        opts = opts || {};
        var body = {};
        if (opts.monitor) body.monitor = opts.monitor;
        if (Number(opts.quality_profile_id) > 0) body.quality_profile_id = Number(opts.quality_profile_id);
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

    // admin: a title the searches gave up on, cleared and tried on every source
    function searchAgain(g) {
        act('/api/video/wishlist/retry', 'POST',
            { scope: g.kind === 'movie' ? 'movie' : 'show', tmdb_id: g.tmdb_id },
            function (j) {
                if (j.missing_target) return 'Set a download folder first';
                if (!j.total) return 'Nothing left to search for';
                return j.queued ? 'Searching again' : 'Already searching';
            });
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
        var named = (profilesCache.list || []).length > 1;
        if (g.bucket === 'pending' && admin) {
            if (g.kind === 'show') items.push({ id: 'seasons', label: named ? 'Approve with seasons and quality…' : 'Approve with seasons…' });
            else if (named) items.push({ id: 'seasons', label: 'Approve with quality…' });
            items.push({ id: 'decline', label: 'Decline…', danger: true });
        }
        if (admin && canSearchAgain(g.lead)) items.push({ id: 'retry', label: 'Search again' });
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
            else if (which === 'retry') searchAgain(g);
            else if (which === 'seasons') {
                pickRequest({ kind: g.kind, title: g.title, current: g.monitor || 'all',
                    heading: g.kind === 'show' ? 'Approve which seasons?' : 'Approve at which quality?',
                    confirm: 'Approve', profiles: profilesCache.list, quality: g.quality_profile_id })
                    .then(function (res) { if (res) approve(g, res); });
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

    // ── the shared request flow (detail page + cards) ───────────────────────
    // named quality profiles, fetched once per page load. [{id, name}], Default first
    var profilesCache = { list: null, inflight: null };
    function loadProfiles() {
        if (profilesCache.list) return Promise.resolve(profilesCache.list);
        if (profilesCache.inflight) return profilesCache.inflight;
        profilesCache.inflight = fetch('/api/video/downloads/quality/profiles', { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                var list = ((d && d.profiles) || []).map(function (p) {
                    return { id: Number(p.id) || 0, name: String(p.name || '') };
                });
                profilesCache.list = list.length ? list : [{ id: 0, name: 'Default' }];
                return profilesCache.list;
            })
            .catch(function () { return [{ id: 0, name: 'Default' }]; })
            .then(function (list) { profilesCache.inflight = null; return list; });
        return profilesCache.inflight;
    }

    // this profile's own asks, cached a minute. {rows, quota, states}
    var mineCache = { at: 0, pid: null, data: null, inflight: null };
    function profileId() {
        var cp = (typeof currentProfile !== 'undefined') ? currentProfile : null;
        return cp ? cp.id : null;
    }
    function remember(d) {
        var rows = (d && d.requests) || [];
        mineCache.data = { rows: rows, quota: (d && d.quota) || null, states: cardStates(rows) };
        mineCache.at = Date.now();
        mineCache.pid = profileId();
        return mineCache.data;
    }
    function mine(force) {
        var fresh = mineCache.data && mineCache.pid === profileId() && (Date.now() - mineCache.at) < 60000;
        if (fresh && !force) return Promise.resolve(mineCache.data);
        if (mineCache.inflight) return mineCache.inflight;
        mineCache.inflight = fetch('/api/video/requests', { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) { return (d && d.success) ? remember(d) : (mineCache.data || null); })
            .catch(function () { return mineCache.data || null; })
            .then(function (v) { mineCache.inflight = null; return v; });
        return mineCache.inflight;
    }

    function canDl() {
        return (typeof canDownload !== 'function') || canDownload();
    }

    function changed() {
        document.dispatchEvent(new CustomEvent('soulsync:video-requests-changed'));
    }

    // item: {kind, tmdb_id, title, year?, poster_url?}. checks the quota
    // first, then the seasons/quality sheet, then posts. resolves
    // {ok, already, in_library} or null when nothing was sent. toasts itself.
    function requestTitle(item) {
        if (!item || !item.tmdb_id || (item.kind !== 'movie' && item.kind !== 'show')) return Promise.resolve(null);
        var key = item.kind + ':' + item.tmdb_id;
        return Promise.all([mine(true), loadProfiles()]).then(function (res) {
            var m = res[0], profiles = res[1];
            if (m && m.states[key] === 'requested') {
                toast('Already requested. You’ll hear when it’s decided', 'info');
                return null;
            }
            if (m && quotaSpent(m.quota)) {
                toast(quotaLine(m.quota) || 'You’ve used your requests for now', 'warning');
                return null;
            }
            var named = profiles.length > 1;
            var pick = (item.kind === 'show' || named)
                ? pickRequest({ kind: item.kind, title: item.title || '', current: 'all', profiles: profiles })
                : Promise.resolve({ monitor: null, quality_profile_id: 0 });
            return pick.then(function (choice) {
                if (!choice) return null;
                var body = { kind: item.kind, tmdb_id: item.tmdb_id, title: item.title, year: item.year,
                    poster_url: item.poster_url || null };
                if (choice.monitor) body.monitor = choice.monitor;
                if (choice.quality_profile_id > 0) body.quality_profile_id = choice.quality_profile_id;
                return fetch('/api/video/requests', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
                    .then(function (r) {
                        return r.json().catch(function () { return null; }).then(function (j) {
                            return { status: r.status, j: j || {} };
                        });
                    })
                    .then(function (out) {
                        var j = out.j;
                        if (out.status === 429) {
                            // request limit used up: the server's own words, calmly
                            if (mineCache.data && j.quota) mineCache.data.quota = j.quota;
                            toast(j.error || 'You’ve used your requests for now', 'warning');
                            return null;
                        }
                        if (j.in_library) {
                            toast('That’s already in your library', 'info');
                            return { ok: false, in_library: true };
                        }
                        if (!j.success) throw new Error(j.error || '');
                        toast(j.already ? 'Already requested. You’ll hear when it’s decided'
                                        : 'Requested. You’ll hear when it’s decided', 'success');
                        mine(true).then(changed);
                        return { ok: true, already: !!j.already };
                    });
            });
        }).catch(function (err) {
            toast((err && err.message) || 'Couldn’t send the request', 'error');
            return null;
        });
    }

    // the card's quick action for a profile that can't download
    function cardButton(o) {
        if (!o || !o.tmdbId || (o.kind !== 'movie' && o.kind !== 'show')) return '';
        return '<button type="button" class="vreq-card-btn" data-vreq-card' +
            ' data-kind="' + esc(o.kind) + '" data-tmdb="' + esc(o.tmdbId) + '"' +
            ' data-title="' + esc(o.title || '') + '" data-year="' + esc(o.year || '') + '"' +
            ' data-poster="' + esc(o.poster || '') + '"' +
            ' title="Request" aria-label="Request ' + esc(o.title || 'this') + '">' +
            '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"' +
            ' stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
            '<path d="M22 2 11 13"/><path d="M22 2 15 22l-4-9-9-4 20-7z"/></svg></button>';
    }

    // Requested / Available on un-owned cards, over the Preview ribbon. keyed
    // off the card's request button: every surface (search, discover, person,
    // studio, more like this) gets one for these profiles, whatever its own
    // data attributes are called.
    var RIBBON_SEL = '.vsr-ribbon--preview, .vsr-ribbon--wish, .vsr-ribbon--req';
    function paintCards(root, states) {
        var btns = root.querySelectorAll('[data-vreq-card]');
        for (var i = 0; i < btns.length; i++) {
            var b = btns[i];
            var st = states[b.getAttribute('data-kind') + ':' + b.getAttribute('data-tmdb')] || '';
            b.hidden = !!st;
            var card = b.closest('.vsr-card, .vd-sim-card, .vwlp-card') || b.parentNode;
            var ribbon = card && card.querySelector(RIBBON_SEL);
            if (ribbon && st) {
                ribbon.className = 'vsr-ribbon vsr-ribbon--req' + (st === 'available' ? ' vsr-ribbon--req-here' : '');
                ribbon.textContent = st === 'available' ? 'Available' : 'Requested';
            } else if (ribbon && ribbon.classList.contains('vsr-ribbon--req')) {
                ribbon.className = 'vsr-ribbon vsr-ribbon--preview';
                ribbon.textContent = 'Preview';
            }
        }
    }

    function hydrate(root) {
        if (canDl()) return;
        root = root || document;
        mine(false).then(function (m) { if (m) paintCards(root, m.states); });
    }

    // the card button sits inside a card <a>: capture it before the link does
    document.addEventListener('click', function (e) {
        var b = e.target.closest && e.target.closest('[data-vreq-card]');
        if (!b) return;
        e.preventDefault();
        e.stopPropagation();
        if (b.disabled) return;
        b.disabled = true;
        requestTitle({ kind: b.getAttribute('data-kind'), tmdb_id: Number(b.getAttribute('data-tmdb')),
            title: b.getAttribute('data-title') || '', year: Number(b.getAttribute('data-year')) || null,
            poster_url: b.getAttribute('data-poster') || null })
            .then(function () { b.disabled = false; });
    }, true);

    var repaintT;
    function repaintSoon() {
        clearTimeout(repaintT);
        repaintT = setTimeout(function () { hydrate(document); }, 200);
    }
    document.addEventListener('soulsync:video-requests-changed', repaintSoon);

    window.VideoRequests = {
        request: requestTitle,
        cardButton: cardButton,
        hydrate: hydrate,
        mine: mine,
        profiles: loadProfiles
    };

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
        if (!canDl()) mine(true).then(changed);
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
