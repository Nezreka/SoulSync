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
    };

    function q(sel) {
        var page = document.getElementById('chat-page');
        return page ? page.querySelector(sel) : null;
    }
    function esc(s) {
        var d = document.createElement('div');
        d.textContent = (s == null ? '' : String(s));
        return d.innerHTML;
    }
    // esc() (innerHTML-based) does NOT escape double quotes — a Soulseek
    // username is REMOTE input and must never break out of an attribute.
    function attr(s) {
        return esc(s).replace(/"/g, '&quot;');
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

    function messageRow(m) {
        var user = m.username || m.user || '?';
        var self = m.self === true || m.direction === 'Out';
        return '<div class="chat-msg' + (self ? ' chat-msg--self' : '') + '">' +
            '<button class="chat-msg-user" type="button" data-chat-user="' + attr(user) + '" ' +
                'title="Message ' + attr(user) + '">' + esc(user) + '</button>' +
            '<span class="chat-msg-time">' + esc(fmtTime(m.timestamp)) + '</span>' +
            '<div class="chat-msg-text">' + esc(m.message) + '</div></div>';
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
        host.innerHTML = msgs.map(messageRow).join('');
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
                '" type="button" data-chat-open-room># ' + esc(state.room || 'soulsync') + '</button>';
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
              '<span class="chat-head-sub">the SoulSync community room on Soulseek</span>'
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
            var t = e.target.closest('[data-chat-open-room]');
            if (t) { openRoom(); return; }
            t = e.target.closest('[data-chat-open-pm]');
            if (t) { openPm(t.getAttribute('data-chat-open-pm')); return; }
            t = e.target.closest('[data-chat-user]');
            if (t) { openPm(t.getAttribute('data-chat-user')); return; }
        });

        var form = q('[data-chat-composer]');
        if (form) form.addEventListener('submit', function (e) { e.preventDefault(); send(); });

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
                state.room = (res.body && res.body.room) || 'soulsync';
                state.canSend = !!(res.body && res.body.can_send);
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
                        onRoomMessages: onRoomMessages, onUnread: onUnread };
})();
