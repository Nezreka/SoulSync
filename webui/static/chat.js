// Chat — Soulseek rooms + private messages, proxied through slskd (/api/chat).
// ONE page for the whole app: the music sidebar shows it directly, the video
// sidebar reveals the same #chat-page via SHARED_PAGES (video-side.js).
//
// Polling: 4s room refresh, but ONLY while the chat page is actually visible
// AND the tab is foregrounded (request-flood rules) — leaving the page or
// hiding the tab stops the timer dead. Messages render newest-last with
// autoscroll pinned to the bottom unless the user scrolled up to read.
(function () {
    'use strict';

    var POLL_MS = 4000;
    var state = {
        view: 'room',            // 'room' | 'pm'
        pmUser: null,            // active conversation username
        room: null,              // configured room name (from /status)
        canSend: false,
        configured: null,        // null = unknown yet
        timer: null,
        lastStamp: null,         // newest message timestamp we've rendered
        stickBottom: true,       // autoscroll unless the user scrolled up
        started: false,
        ssOnly: false,           // room filter: show only SoulSync-app messages
        isAdmin: false,          // shows the settings cog (from /status)
    };
    try { state.ssOnly = localStorage.getItem('chat_ss_only') === '1'; } catch (e) { /* ignore */ }

    function q(sel) {
        var page = document.getElementById('chat-page');
        return page ? page.querySelector(sel) : null;
    }
    // Pure string escaping (no DOM): safe in BOTH text and attribute context,
    // and testable under node (tests/js/chat_render_harness.mjs).
    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
    var attr = esc;   // esc now covers attribute context too

    // ── rich rendering (the !SS1! envelope payload, markdown subset) ─────────
    // EVERYTHING here is remote input wearing a costume: escape FIRST, then
    // apply formatting to the escaped text. Code spans and URLs are pulled out
    // into \u0000-sentinel placeholders before markdown so their contents stay literal.
    var EMOJI = {
        smile: '😄', grin: '😁', joy: '😂', wink: '😉', cry: '😢', sob: '😭',
        heart: '❤️', broken_heart: '💔', fire: '🔥', tada: '🎉', rocket: '🚀',
        thumbsup: '👍', thumbsdown: '👎', clap: '👏', wave: '👋', pray: '🙏',
        eyes: '👀', thinking: '🤔', shrug: '🤷', facepalm: '🤦', skull: '💀',
        notes: '🎵', musical_note: '🎶', headphones: '🎧', guitar: '🎸', cd: '💿',
        vinyl: '📀', mic: '🎤', speaker: '🔊', movie: '🎬', tv: '📺',
        popcorn: '🍿', star: '⭐', sparkles: '✨', zap: '⚡', boom: '💥',
        check: '✅', x: '❌', warning: '⚠️', question: '❓', exclamation: '❗',
        wave_hand: '👋', beers: '🍻', coffee: '☕', pizza: '🍕', cake: '🎂',
        sunglasses: '😎', robot: '🤖', ghost: '👻', alien: '👽', crown: '👑',
        gem: '💎', money: '🤑', hundred: '💯', point_up: '☝️', muscle: '💪',
        rofl: '🤣', melting: '🫠', salute: '🫡', handshake: '🤝', brain: '🧠',
    };
    var URL_RE = /(https?:\/\/[^\s]+)/g;

    function _trimUrl(u) {
        // trailing sentence punctuation is chat, not URL
        var m = u.match(/[.,;:!?)\]]+$/);
        return m ? u.slice(0, -m[0].length) : u;
    }

    function _linkHtml(u) {
        // u is already-escaped text (esc ran first) — safe in attr + label.
        return '<a class="chat-link" href="' + u + '" target="_blank" rel="noopener noreferrer">' + u + '</a>';
    }

    // ── embeds (richchat P3): click-to-load, never auto-load ─────────────────
    // Loading a remote image reveals your IP to whoever hosts it — so nothing
    // fetches until the reader clicks the chip. Works for BOTH rich and plain
    // messages (rendering is our choice, not the sender's).
    var IMG_RE = /\.(png|jpe?g|gif|webp|avif)(\?[^\s]*)?$/i;

    function _ytId(u) {
        // u is escaped text: undo &amp; on a PARSING COPY only
        var raw = u.replace(/&amp;/g, '&');
        var m = raw.match(/youtube\.com\/watch\?(?:[^\s&]*&)*v=([A-Za-z0-9_-]{6,20})/) ||
                raw.match(/youtu\.be\/([A-Za-z0-9_-]{6,20})/) ||
                raw.match(/youtube\.com\/shorts\/([A-Za-z0-9_-]{6,20})/);
        return m ? m[1] : null;
    }

    // ── SoulSync deep links (richchat P4) ────────────────────────────────────
    // Paste your address bar and every SoulSync renders it as a LOCAL link:
    // the sharer's host is theirs, only the path travels. Whitelisted shapes
    // only — and NEVER 'library'-source video paths (those ids are local db
    // rows; on another install they'd open a random title). tmdb ids and
    // artist source-ids are universal.
    var SS_PATH_RE = /\/(artist-detail\/[a-z0-9_-]{1,32}\/[A-Za-z0-9][A-Za-z0-9_.-]{0,63}|video-detail\/tmdb\/(?:movie|show)\/\d{1,10})(?:$|[?#])/;

    function _ssChip(path, label) {
        // path is regex-whitelisted above — attribute-safe by shape
        return ' <a class="chat-embed-chip chat-ss-chip" href="' + path +
            '" title="Open in SoulSync">↪ ' + label + '</a>';
    }

    function _ssPathChip(u) {
        var m = u.replace(/&amp;/g, '&').match(SS_PATH_RE);
        if (!m) return '';
        var path = '/' + m[1];
        var label = path.indexOf('/artist-detail/') === 0 ? '🎵 open artist'
            : (path.indexOf('/movie/') > -1 ? '🎬 open movie' : '📺 open show');
        return _ssChip(path, label);
    }

    // GIFs picked from the in-app search auto-render: these CDNs are the two
    // the picker can produce, single well-known hosts — unlike arbitrary image
    // links, which stay click-to-load.
    var GIF_CDN_RE = /^https?:\/\/((media|c)\.tenor\.com|media\d*\.giphy\.com)\//i;

    function _linkWithEmbeds(u) {
        if (GIF_CDN_RE.test(u)) {
            return '<img class="chat-embed-img chat-gif" loading="lazy" ' +
                'referrerpolicy="no-referrer" src="' + u + '" alt="GIF">';
        }
        var html = _linkHtml(u);
        var yt = _ytId(u);
        if (yt) {
            // id is regex-constrained to [A-Za-z0-9_-] — attribute-safe by shape
            return html + ' <button type="button" class="chat-embed-chip" data-chat-embed-yt="' +
                yt + '" title="Play here (YouTube)">▶ play</button>';
        }
        if (IMG_RE.test(u)) {
            return html + ' <button type="button" class="chat-embed-chip" data-chat-embed-img="' +
                u + '" title="Load this image (reveals your IP to its host)">🖼 show</button>';
        }
        return html + _ssPathChip(u);
    }

    function _extract(s, regex, out, transform) {
        return s.replace(regex, function (m, g1) {
            var kept = transform ? transform(m, g1) : m;
            out.push(kept);
            return '\u0000' + (out.length - 1) + '\u0000';
        });
    }

    function _restore(s, out) {
        return s.replace(/\u0000(\d+)\u0000/g, function (_, i) { return out[Number(i)]; });
    }

    function _preclean(text) {
        // strip literal NULs so crafted input can never touch the sentinel space
        return String(text == null ? '' : text).replace(/\u0000/g, '');
    }

    function renderPlain(text) {
        // non-envelope messages (other clients): escaped + clickable links only
        var hold = [];
        var s = _extract(esc(_preclean(text)), URL_RE, hold, function (m) {
            var u = _trimUrl(m);
            return _linkWithEmbeds(u) + m.slice(u.length);
        });
        return _restore(s, hold).replace(/\n/g, '<br>');
    }

    function _hostOf(u) {
        var m = u.match(/^https?:\/\/([^\/?#\s]+)/i);
        return m ? m[1] : '';
    }

    function renderRich(text) {
        var hold = [];
        var s = esc(_preclean(text));
        // 1) protect literal regions from markdown mangling: code BLOCKS first
        //    (their newlines survive inside <pre> because placeholders skip the
        //    later \n→<br> pass), then inline code, then masked links + URLs
        s = _extract(s, /```\n?([\s\S]+?)\n?```/g, hold, function (_, c) {
            return '<pre class="chat-codeblock">' + c + '</pre>';
        });
        s = _extract(s, /`([^`\n]+)`/g, hold, function (_, c) {
            return '<code class="chat-code">' + c + '</code>';
        });
        // [label](url) masked links — with the real domain disclosed right
        // after the label, so a masked link can't impersonate another site
        s = _extract(s, /\[([^\]\n]{1,80})\]\((https?:\/\/[^\s)]+)\)/g, hold, function (m) {
            var mm = m.match(/^\[([^\]]+)\]\((.+)\)$/);
            var label = mm[1], url = mm[2];
            return '<a class="chat-link" href="' + url + '" target="_blank" rel="noopener noreferrer">' +
                label + '</a><span class="chat-link-domain">(' + _hostOf(url) + ')</span>';
        });
        s = _extract(s, URL_RE, hold, function (m) {
            var u = _trimUrl(m);
            return _linkWithEmbeds(u) + m.slice(u.length);
        });
        // 1b) bare ss:// short links (envelope-only grammar):
        //     ss://artist/<source>/<id> · ss://movie/<tmdb> · ss://show/<tmdb>
        s = _extract(s, /ss:\/\/(artist\/[a-z0-9_-]{1,32}\/[A-Za-z0-9][A-Za-z0-9_.-]{0,63}|(?:movie|show)\/\d{1,10})\b/g,
            hold, function (_, g1) {
                if (g1.indexOf('artist/') === 0) {
                    return _ssChip('/artist-detail/' + g1.slice(7), '🎵 open artist');
                }
                var kind = g1.split('/')[0];
                return _ssChip('/video-detail/tmdb/' + g1,
                    kind === 'movie' ? '🎬 open movie' : '📺 open show');
            });
        // 2) markdown subset (on escaped text — tags below are OURS, not input's)
        s = s.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
        s = s.replace(/__([^_\n]+)__/g, '<u>$1</u>');
        s = s.replace(/\*([^*\n]+)\*/g, '<em>$1</em>');
        s = s.replace(/~~([^~\n]+)~~/g, '<s>$1</s>');
        s = s.replace(/\|\|([^|\n]+)\|\|/g,
            '<span class="chat-spoiler" data-chat-spoiler title="Spoiler — click to reveal">$1</span>');
        // 3) emoji shortcodes
        s = s.replace(/:([a-z0-9_+-]+):/g, function (m, name) { return EMOJI[name] || m; });
        // 4) line-level blocks: headings, quotes, bullets ('>' is &gt; here)
        s = s.split('\n').map(function (line) {
            if (line.indexOf('### ') === 0) return '<span class="chat-h3">' + line.slice(4) + '</span>';
            if (line.indexOf('## ') === 0) return '<span class="chat-h2">' + line.slice(3) + '</span>';
            if (line.indexOf('# ') === 0) return '<span class="chat-h1">' + line.slice(2) + '</span>';
            if (line.indexOf('&gt; ') === 0) return '<span class="chat-quote">' + line.slice(5) + '</span>';
            if (line.indexOf('- ') === 0) return '<span class="chat-li">•&nbsp;' + line.slice(2) + '</span>';
            return line;
        }).join('\n');
        return _restore(s.replace(/\n/g, '<br>'), hold);
    }

    function pageVisible() {
        // No .active check: on the VIDEO side the shared page is revealed by
        // CSS alone (SHARED_PAGES) and never gets the class — computed
        // visibility (offsetParent) is the one signal true on both sides.
        var page = document.getElementById('chat-page');
        return !!(page && page.offsetParent !== null && !document.hidden);
    }

    // ── data ─────────────────────────────────────────────────────────────────
    function getJSON(url) {
        return fetch(url, { headers: { 'Accept': 'application/json' } })
            .then(function (r) {
                return r.json().catch(function () { return {}; }).then(function (body) {
                    return { ok: r.ok, status: r.status, body: body };
                });
            });
    }
    function postJSON(url, payload) {
        return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify(payload) })
            .then(function (r) {
                return r.json().catch(function () { return {}; }).then(function (body) {
                    return { ok: r.ok, status: r.status, body: body };
                });
            });
    }

    // ── rendering ────────────────────────────────────────────────────────────
    function fmtTime(ts) {
        if (!ts) return '';
        var d = new Date(String(ts).replace(' ', 'T'));
        if (isNaN(d.getTime())) return '';
        var today = new Date();
        var sameDay = d.toDateString() === today.toDateString();
        var hm = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        return sameDay ? hm : (d.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' ' + hm);
    }

    // ── Discord-style rendering: avatars, grouping, date separators ──────────
    function _hue(name) {
        var h = 0;
        name = String(name || '');
        for (var i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
        return h % 360;
    }

    function _avatar(user) {
        return '<span class="chat-avatar" style="background:hsl(' + _hue(user) +
            ',52%,40%)" aria-hidden="true">' +
            esc(String(user || '?').charAt(0).toUpperCase()) + '</span>';
    }

    function _fullTs(ts) {
        var d = new Date(String(ts || '').replace(' ', 'T'));
        return isNaN(d.getTime()) ? '' : d.toLocaleString();
    }

    function _dayLabel(ts) {
        var d = new Date(String(ts || '').replace(' ', 'T'));
        if (isNaN(d.getTime())) return '';
        return d.toLocaleDateString([], { month: 'long', day: 'numeric', year: 'numeric' });
    }

    function _lineHtml(m) {
        return '<div class="chat-line" title="' + attr(_fullTs(m.timestamp)) + '">' +
            (m.rich ? renderRich(m.message) : renderPlain(m.message)) +
            '</div>';
    }

    // Consecutive messages from the same sender (same app-ness, <5 min apart)
    // fold under one avatar + name header, with day separators between dates.
    function renderGroups(msgs) {
        var html = '', group = null, lastDay = null, GAP = 5 * 60 * 1000;
        function flush() { if (group) { html += group.html + '</div></div>'; group = null; } }
        for (var i = 0; i < msgs.length; i++) {
            var m = msgs[i];
            var user = m.username || m.user || '?';
            var self = m.self === true || m.direction === 'Out';
            // the envelope IS the app signature: a plaintext room message means
            // the sender is on another Soulseek client, not SoulSync
            var ext = state.view === 'room' && !m.rich && !self;
            var day = _dayLabel(m.timestamp);
            if (day && day !== lastDay) {
                flush();
                html += '<div class="chat-day-sep"><span>' + esc(day) + '</span></div>';
                lastDay = day;
            }
            var t = Date.parse(String(m.timestamp || '').replace(' ', 'T')) || 0;
            if (group && group.user === user && group.ext === ext && group.self === self &&
                    (t - group.t) < GAP) {
                group.html += _lineHtml(m);
                group.t = t;
                continue;
            }
            flush();
            group = { user: user, ext: ext, self: self, t: t, html:
                '<div class="chat-group' + (self ? ' chat-group--self' : '') +
                    (ext ? ' chat-group--ext' : '') + '">' +
                _avatar(user) +
                '<div class="chat-group-body"><div class="chat-group-head">' +
                '<button class="chat-msg-user" type="button" data-chat-user="' + attr(user) +
                    '" style="color:hsl(' + _hue(user) + ',65%,68%)" title="Message ' +
                    attr(user) + '">' + esc(user) + '</button>' +
                (ext ? '<span class="chat-ext-tag" title="Sent from another Soulseek client — not SoulSync">via Soulseek</span>' : '') +
                '<span class="chat-msg-time">' + esc(fmtTime(m.timestamp)) + '</span>' +
                '</div>' + _lineHtml(m) };
        }
        flush();
        return html;
    }

    function renderMessages(list) {
        var host = q('[data-chat-messages]');
        if (!host) return;
        if (!list || !list.length) {
            host.innerHTML = '<div class="chat-empty">No messages yet — say hi 👋</div>';
            return;
        }
        // slskd returns oldest→newest for rooms; sort defensively by timestamp.
        var msgs = list.slice().sort(function (a, b) {
            return String(a.timestamp || '').localeCompare(String(b.timestamp || ''));
        });
        var newest = String(msgs[msgs.length - 1].timestamp || '') + ':' + msgs.length;
        if (newest === state.lastStamp && host.childElementCount) return;   // nothing new
        state.lastStamp = newest;
        var shown = msgs, hidden = 0;
        if (state.view === 'room' && state.ssOnly) {
            shown = msgs.filter(function (m) { return m.rich || m.self === true || m.direction === 'Out'; });
            hidden = msgs.length - shown.length;
        }
        host.innerHTML = renderGroups(shown) +
            (hidden ? '<div class="chat-hidden-note">' + hidden +
                ' message' + (hidden === 1 ? '' : 's') + ' from other Soulseek clients hidden</div>' : '');
        if (state.stickBottom) host.scrollTop = host.scrollHeight;
    }

    function renderUsers(users) {
        var host = q('[data-chat-users]');
        if (!host) return;
        if (state.view !== 'room' || !users || !users.length) {
            host.innerHTML = ''; host.hidden = true; return;
        }
        host.hidden = false;
        var names = users.map(function (u) { return u.username || u; })
            .filter(Boolean).sort(function (a, b) {
                return String(a).toLowerCase().localeCompare(String(b).toLowerCase());
            });
        host.innerHTML = '<div class="chat-users-label">' + names.length + ' online</div>' +
            names.map(function (n) {
                return '<button class="chat-user" type="button" data-chat-user="' + attr(n) + '" ' +
                    'title="Message ' + attr(n) + '">' + esc(n) + '</button>';
            }).join('');
    }

    function renderSide(convos) {
        var rooms = q('[data-chat-rooms]');
        if (rooms) {
            rooms.innerHTML = '<button class="chat-side-item' +
                (state.view === 'room' ? ' chat-side-item--on' : '') +
                '" type="button" data-chat-open-room># ' + esc(state.room || 'SoulSync') + '</button>';
        }
        var host = q('[data-chat-convos]');
        if (!host) return;
        var list = (convos || []).map(function (c) {
            var name = c.username || c.name || '';
            if (!name) return '';
            var unread = c.hasUnAcknowledgedMessages || c.unAcknowledgedMessageCount > 0;
            var on = state.view === 'pm' && state.pmUser === name;
            return '<button class="chat-side-item' + (on ? ' chat-side-item--on' : '') +
                '" type="button" data-chat-open-pm="' + attr(name) + '">' + esc(name) +
                (unread ? '<span class="chat-side-dot"></span>' : '') + '</button>';
        }).join('');
        host.innerHTML = list || '<div class="chat-side-none">No conversations</div>';
    }

    function renderHead() {
        var head = q('[data-chat-head]');
        if (!head) return;
        head.innerHTML = state.view === 'room'
            ? '<span class="chat-head-title"># ' + esc(state.room || '') + '</span>' +
              '<span class="chat-head-sub">the SoulSync community room on Soulseek</span>' +
              '<button class="chat-filter-btn' + (state.ssOnly ? ' chat-filter-btn--on' : '') +
              '" type="button" data-chat-filter title="' +
              (state.ssOnly ? 'Showing SoulSync app messages only — click for everything'
                            : 'Showing everything — click to hide other Soulseek clients') + '">' +
              (state.ssOnly ? 'SoulSync only' : 'All messages') + '</button>' +
              (state.isAdmin ? '<button class="chat-cog-btn" type="button" data-chat-settings-btn ' +
                  'title="Chat settings">⚙</button>' : '')
            : '<span class="chat-head-title">' + esc(state.pmUser || '') + '</span>' +
              '<span class="chat-head-sub">private message</span>';
    }

    function renderComposer() {
        var form = q('[data-chat-composer]');
        var input = q('[data-chat-input]');
        if (!form || !input) return;
        form.classList.toggle('chat-composer--locked', !state.canSend);
        input.disabled = !state.canSend;
        input.placeholder = state.canSend
            ? (state.view === 'room' ? 'Message # ' + (state.room || '') + '…'
                                     : 'Message ' + (state.pmUser || '') + '…')
            : 'Read-only — chat sending is admin-only on this server';
        // Formatting only exists inside the envelope — the toolbar is a ROOM
        // thing (PMs are plaintext for non-SoulSync readers + the ProveIt bots).
        var bar = q('[data-chat-toolbar]');
        if (bar) bar.hidden = !(state.view === 'room' && state.canSend);
        if (state.view !== 'room') { toggleEmojiPicker(true); toggleGifPicker(true); }
    }

    // ── composer toolbar (room only) ─────────────────────────────────────────
    var _FMT = { bold: ['**', '**'], italic: ['*', '*'], strike: ['~~', '~~'],
                 code: ['`', '`'], spoiler: ['||', '||'], quote: ['> ', ''] };

    function applyFormat(kind) {
        var input = q('[data-chat-input]');
        var pair = _FMT[kind];
        if (!input || !pair || input.disabled) return;
        var start = input.selectionStart || 0, end = input.selectionEnd || 0;
        var v = input.value;
        input.value = v.slice(0, start) + pair[0] + v.slice(start, end) + pair[1] + v.slice(end);
        var pos = (start === end) ? start + pair[0].length : end + pair[0].length + pair[1].length;
        input.focus();
        input.setSelectionRange(pos, pos);
    }

    function insertAtCursor(text) {
        var input = q('[data-chat-input]');
        if (!input || !text || input.disabled) return;
        var start = input.selectionStart || input.value.length;
        input.value = input.value.slice(0, start) + text + input.value.slice(input.selectionEnd || start);
        input.focus();
        input.setSelectionRange(start + text.length, start + text.length);
    }

    var _gifTimer = null;

    function openSettings() {
        var overlay = q('[data-chat-settings-modal]');
        if (!overlay) return;
        getJSON('/api/chat/settings').then(function (res) {
            if (!res.ok) {
                if (typeof showToast === 'function') {
                    showToast(res.body && res.body.error || 'Could not load chat settings', 'error');
                }
                return;
            }
            var b = res.body;
            var el = q('[data-chat-set-room]');
            if (el) el.value = b.room || '';
            el = q('[data-chat-set-giphy]');
            if (el) { el.value = ''; el.placeholder = b.giphy_key_set ? '••••••••  (configured)' : 'not set'; }
            el = q('[data-chat-set-autojoin]'); if (el) el.checked = !!b.auto_join;
            el = q('[data-chat-set-membersend]'); if (el) el.checked = !!b.member_send;
            el = q('[data-chat-set-autoprove]'); if (el) el.checked = !!b.auto_prove;
            overlay.hidden = false;
        });
    }

    function saveSettings() {
        var overlay = q('[data-chat-settings-modal]');
        var payload = {
            room: (q('[data-chat-set-room]') || {}).value || '',
            auto_join: !!(q('[data-chat-set-autojoin]') || {}).checked,
            member_send: !!(q('[data-chat-set-membersend]') || {}).checked,
            auto_prove: !!(q('[data-chat-set-autoprove]') || {}).checked,
        };
        // the key field is only SENT when the admin typed one — an untouched
        // blank must never clear a configured key
        var kEl = q('[data-chat-set-giphy]');
        if (kEl && kEl.value.trim()) payload.giphy_key = kEl.value.trim();
        postJSON('/api/chat/settings', payload).then(function (res) {
            if (!res.ok) {
                if (typeof showToast === 'function') {
                    showToast(res.body && res.body.error || 'Settings not saved', 'error');
                }
                return;
            }
            if (overlay) overlay.hidden = true;
            state.room = res.body.room || state.room;
            state.lastStamp = null;
            renderHead();
            refresh();
            if (typeof showToast === 'function') showToast('Chat settings saved', 'success');
        });
    }

    function toggleGifPicker(forceClose) {
        var pop = q('[data-chat-gif-pop]');
        if (!pop) return;
        if (forceClose === true) { pop.hidden = true; return; }
        pop.hidden = !pop.hidden;
        if (!pop.hidden) {
            toggleEmojiPicker(true);
            var inp = q('[data-chat-gif-search]');
            if (inp) inp.focus();
        }
    }

    function gifSearch(qstr) {
        var grid = q('[data-chat-gif-grid]');
        if (!grid) return;
        if (!qstr) { grid.innerHTML = '<div class="chat-gif-hint">Type to search GIPHY</div>'; return; }
        grid.innerHTML = '<div class="chat-gif-hint">Searching…</div>';
        getJSON('/api/chat/gifs?q=' + encodeURIComponent(qstr)).then(function (res) {
            if (!res.ok) {
                grid.innerHTML = '<div class="chat-gif-hint">' +
                    esc(res.body && res.body.error || 'GIF search unavailable') + '</div>';
                return;
            }
            var gifs = res.body.gifs || [];
            if (!gifs.length) { grid.innerHTML = '<div class="chat-gif-hint">No results</div>'; return; }
            grid.innerHTML = gifs.map(function (g2) {
                return '<button type="button" class="chat-gif-cell" data-chat-gif-send="' +
                    attr(g2.url) + '"><img src="' + attr(g2.preview) +
                    '" loading="lazy" referrerpolicy="no-referrer" alt=""></button>';
            }).join('');
        });
    }

    function sendGif(url) {
        if (!url || !state.canSend || state.view !== 'room') return;
        toggleGifPicker(true);
        postJSON('/api/chat/room/message', { message: url }).then(function (res) {
            if (!res.ok) {
                if (typeof showToast === 'function') {
                    showToast(res.body && res.body.error || 'GIF not sent', 'error');
                }
                return;
            }
            state.stickBottom = true;
            state.lastStamp = null;
            refresh();
        });
    }

    function toggleEmojiPicker(forceClose) {
        var pop = q('[data-chat-emoji-pop]');
        if (!pop) return;
        if (forceClose === true) { pop.hidden = true; return; }
        if (pop.hidden && !pop.getAttribute('data-built')) {
            pop.setAttribute('data-built', '1');
            var names = Object.keys(EMOJI);
            pop.innerHTML = names.map(function (n) {
                return '<button type="button" class="chat-emoji" data-chat-emoji-pick="' +
                    EMOJI[n] + '" title=":' + n + ':">' + EMOJI[n] + '</button>';
            }).join('');
        }
        pop.hidden = !pop.hidden;
    }

    function renderProblem(msg) {
        var host = q('[data-chat-messages]');
        if (host) host.innerHTML = '<div class="chat-problem">' + esc(msg) + '</div>';
        renderUsers(null);
    }

    // ── refresh loop ─────────────────────────────────────────────────────────
    function refresh() {
        if (!pageVisible()) return Promise.resolve();
        var work;
        if (state.view === 'room') {
            work = getJSON('/api/chat/room').then(function (res) {
                if (!res.ok) {
                    renderProblem(res.body && res.body.error
                        ? res.body.error
                        : 'Chat is unavailable right now.');
                    return;
                }
                state.room = res.body.room || state.room;
                state.canSend = !!res.body.can_send;
                renderHead(); renderComposer();
                renderMessages(res.body.messages);
                renderUsers(res.body.users);
            });
        } else {
            work = getJSON('/api/chat/conversations/' + encodeURIComponent(state.pmUser))
                .then(function (res) {
                    if (!res.ok) {
                        renderProblem(res.body && res.body.error || 'Conversation unavailable.');
                        return;
                    }
                    state.canSend = !!res.body.can_send;
                    renderHead(); renderComposer();
                    renderMessages(res.body.messages);
                    renderUsers(null);
                });
        }
        var convos = getJSON('/api/chat/conversations').then(function (res) {
            if (res.ok) renderSide(res.body.conversations);
        });
        return Promise.all([work, convos]).catch(function () { /* next tick retries */ });
    }

    function startPolling() {
        stopPolling();
        state.timer = setInterval(function () { refresh(); }, POLL_MS);
    }
    function stopPolling() {
        if (state.timer) { clearInterval(state.timer); state.timer = null; }
    }

    // ── actions ──────────────────────────────────────────────────────────────
    function openRoom() {
        state.view = 'room'; state.pmUser = null; state.lastStamp = null; state.stickBottom = true;
        renderHead(); renderComposer();
        var host = q('[data-chat-messages]');
        if (host) host.innerHTML = '<div class="chat-empty">Loading…</div>';
        refresh();
    }

    function openPm(username) {
        if (!username) return;
        state.view = 'pm'; state.pmUser = username; state.lastStamp = null; state.stickBottom = true;
        renderHead(); renderComposer();
        var host = q('[data-chat-messages]');
        if (host) host.innerHTML = '<div class="chat-empty">Loading…</div>';
        refresh();
    }

    function send() {
        var input = q('[data-chat-input]');
        if (!input) return;
        var text = (input.value || '').trim();
        if (!text || !state.canSend) return;
        input.value = '';
        var url = state.view === 'room'
            ? '/api/chat/room/message'
            : '/api/chat/conversations/' + encodeURIComponent(state.pmUser);
        postJSON(url, { message: text }).then(function (res) {
            if (!res.ok) {
                if (typeof showToast === 'function') {
                    showToast(res.body && res.body.error || 'Message not sent', 'error');
                }
                input.value = text;     // give the words back
                return;
            }
            // Optimistic echo: slskd takes a beat to include a just-sent message,
            // and the poll adds up to 4s more — paint it NOW, then let the next
            // authoritative render replace it (lastStamp reset forces that).
            var host = q('[data-chat-messages]');
            if (host) {
                var empty = host.querySelector('.chat-empty');
                if (empty) empty.remove();
                host.insertAdjacentHTML('beforeend', renderGroups([{
                    username: 'you', message: text,
                    timestamp: new Date().toISOString(), self: true,
                    // room sends ride the envelope → render the echo rich too
                    rich: state.view === 'room',
                }]));
                host.scrollTop = host.scrollHeight;
                state.lastStamp = null;
            }
            state.stickBottom = true;
            refresh();
        });
    }

    // ── wiring ───────────────────────────────────────────────────────────────
    function bind() {
        var page = document.getElementById('chat-page');
        if (!page || page.getAttribute('data-chat-bound')) return;
        page.setAttribute('data-chat-bound', '1');

        page.addEventListener('click', function (e) {
            // any click outside a picker (and its button) closes it
            if (!e.target.closest('[data-chat-emoji-btn]') &&
                    !e.target.closest('[data-chat-emoji-pop]')) {
                toggleEmojiPicker(true);
            }
            if (!e.target.closest('[data-chat-gif-btn]') &&
                    !e.target.closest('[data-chat-gif-pop]')) {
                toggleGifPicker(true);
            }
            var g = e.target.closest('[data-chat-gif-btn]');
            if (g) { toggleGifPicker(); return; }
            g = e.target.closest('[data-chat-gif-send]');
            if (g) { sendGif(g.getAttribute('data-chat-gif-send')); return; }
            var t = e.target.closest('[data-chat-embed-yt]');
            if (t) {
                t.outerHTML = '<span class="chat-embed-frame"><iframe src="https://www.youtube-nocookie.com/embed/' +
                    t.getAttribute('data-chat-embed-yt') +
                    '" allow="encrypted-media; picture-in-picture" allowfullscreen ' +
                    'referrerpolicy="no-referrer" loading="lazy"></iframe></span>';
                return;
            }
            t = e.target.closest('[data-chat-embed-img]');
            if (t) {
                t.outerHTML = '<img class="chat-embed-img" loading="lazy" referrerpolicy="no-referrer" src="' +
                    t.getAttribute('data-chat-embed-img').replace(/"/g, '&quot;') + '" ' +
                    'onerror="this.replaceWith(document.createTextNode(\'(image failed to load)\'))">';
                return;
            }
            t = e.target.closest('[data-chat-spoiler]');
            if (t) { t.classList.add('chat-spoiler--shown'); return; }
            t = e.target.closest('[data-chat-fmt]');
            if (t) { applyFormat(t.getAttribute('data-chat-fmt')); return; }
            t = e.target.closest('[data-chat-emoji-btn]');
            if (t) { toggleEmojiPicker(); return; }
            t = e.target.closest('[data-chat-emoji-pick]');
            if (t) { insertAtCursor(t.getAttribute('data-chat-emoji-pick')); toggleEmojiPicker(true); return; }
            t = e.target.closest('[data-chat-settings-btn]');
            if (t) { openSettings(); return; }
            t = e.target.closest('[data-chat-settings-save]');
            if (t) { saveSettings(); return; }
            t = e.target.closest('[data-chat-settings-cancel]');
            if (t) { var ov = q('[data-chat-settings-modal]'); if (ov) ov.hidden = true; return; }
            var ovl = e.target.closest('[data-chat-settings-modal]');
            if (ovl && e.target === ovl) { ovl.hidden = true; return; }   // click outside the card
            t = e.target.closest('[data-chat-filter]');
            if (t) {
                state.ssOnly = !state.ssOnly;
                try { localStorage.setItem('chat_ss_only', state.ssOnly ? '1' : '0'); } catch (err) { /* ignore */ }
                state.lastStamp = null;
                renderHead(); refresh();
                return;
            }
            t = e.target.closest('[data-chat-open-room]');
            if (t) { openRoom(); return; }
            t = e.target.closest('[data-chat-open-pm]');
            if (t) { openPm(t.getAttribute('data-chat-open-pm')); return; }
            t = e.target.closest('[data-chat-user]');
            if (t) { openPm(t.getAttribute('data-chat-user')); return; }
        });

        var form = q('[data-chat-composer]');
        if (form) form.addEventListener('submit', function (e) { e.preventDefault(); send(); });

        var gifIn = q('[data-chat-gif-search]');
        if (gifIn) {
            gifIn.addEventListener('input', function () {
                if (_gifTimer) clearTimeout(_gifTimer);
                _gifTimer = setTimeout(function () { gifSearch(gifIn.value.trim()); }, 400);
            });
        }

        var scroller = q('[data-chat-messages]');
        if (scroller) {
            scroller.addEventListener('scroll', function () {
                state.stickBottom =
                    scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 40;
            });
        }

        document.addEventListener('visibilitychange', function () {
            if (!document.hidden && pageVisible()) refresh();   // instant catch-up on return
        });
    }

    function open() {
        bind();
        if (state.configured !== true) {
            getJSON('/api/chat/status').then(function (res) {
                state.configured = !!(res.ok && res.body.configured);
                state.room = (res.body && res.body.room) || 'SoulSync';
                state.canSend = !!(res.body && res.body.can_send);
                state.isAdmin = !!(res.body && res.body.is_admin);
                renderSide([]); renderHead(); renderComposer();
                if (!state.configured) {
                    renderProblem('Soulseek (slskd) isn\'t configured — set it up in Settings ' +
                                  'to join the chat.');
                    return;
                }
                openRoom();
            });
        } else {
            refresh();
        }
        startPolling();
    }

    // Leaving the page: the poll gate (pageVisible) already goes quiet, but drop
    // the timer entirely so an idle session holds zero chat state.
    document.addEventListener('soulsync:video-page-shown', function (e) {
        if (e.detail !== 'video-chat') stopPolling();
        else open();
    });
    // Music-side navigation has no event bus — watch the page's class instead.
    var _observer = new MutationObserver(function () {
        var page = document.getElementById('chat-page');
        if (!page) return;
        if (!page.classList.contains('active')) stopPolling();
    });
    function _armObserver() {
        var page = document.getElementById('chat-page');
        if (page) _observer.observe(page, { attributes: true, attributeFilter: ['class'] });
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', _armObserver);
    } else {
        _armObserver();
    }

    // ── socket push (P3): nav badges + PM toasts, no page required ───────────
    var unread = { room: 0, pms: 0 };

    function updateBadges() {
        var total = unread.room + unread.pms;
        ['chat-nav-badge', 'video-chat-nav-badge'].forEach(function (id) {
            var b = document.getElementById(id);
            if (!b) return;
            if (total > 0) { b.textContent = total > 99 ? '99+' : String(total); b.classList.remove('hidden'); }
            else { b.classList.add('hidden'); }
        });
    }

    function onRoomMessages(d) {
        if (pageVisible() && state.view === 'room') {
            refresh();               // live update, nothing to badge
            return;
        }
        unread.room += (d && d.messages ? d.messages.length : 0);
        updateBadges();
    }

    function onUnread(d) {
        unread.pms = (d && d.pms) || 0;
        // Only a RISING count toasts (server sets grew; reads clearing the flag
        // stay quiet) — showToast journals it into the bell + history for free.
        if (d && d.grew && typeof showToast === 'function') {
            var who = (d.users || []).filter(Boolean).join(', ');
            showToast('New Soulseek message' + (who ? ' from ' + who : '') +
                      ' — open Chat to reply', 'info');
        }
        updateBadges();
        if (pageVisible()) refresh();   // conversation rail picks up the dot
    }

    // Opening the room clears its share of the badge (PM share clears through
    // slskd acknowledge when the conversation is actually read).
    var _openRoomBase = openRoom;
    openRoom = function () {
        unread.room = 0; updateBadges();
        _openRoomBase();
    };

    // ── message-this-user from anywhere (P4) ─────────────────────────────────
    // Any surface can render `<button data-chat-msg-user="name">` (download
    // rows, search results…) — this one delegated handler navigates to the
    // Chat page via the REAL nav link (both sides' routers do the rest) and
    // opens the conversation. No inline onclick = no inline-JS escaping traps.
    function messageUser(username) {
        if (!username) return;
        var onVideo = document.body.getAttribute('data-side') === 'video';
        var link = document.querySelector(onVideo
            ? '.nav-button[data-video-page="video-chat"]'
            : '.nav-button[data-page="chat"]');
        if (link) link.click();
        // let the page activate, then open the conversation
        setTimeout(function () { openPm(username); }, 120);
    }

    // CAPTURE phase: the username sits inside cards with their own click
    // handlers (album expand etc.) — messaging must win, not toggle the card.
    document.addEventListener('click', function (e) {
        var t = e.target.closest('[data-chat-msg-user]');
        if (!t) return;
        e.preventDefault(); e.stopPropagation();
        messageUser(t.getAttribute('data-chat-msg-user'));
    }, true);

    window.ChatPage = { open: open, openPm: openPm, messageUser: messageUser,
                        onRoomMessages: onRoomMessages, onUnread: onUnread,
                        // exported for the node render harness (XSS contract tests)
                        renderRich: renderRich, renderPlain: renderPlain,
                        renderGroups: renderGroups };
})();
