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
        room: null,              // the ACTIVE room name
        homeRoom: null,          // the community room (from /status)
        rooms: [],               // joined rooms rail [{name, home}]
        canManage: false,        // admin: may join/leave rooms
        canSend: false,
        configured: null,        // null = unknown yet
        timer: null,
        lastStamp: null,         // newest message timestamp we've rendered
        stickBottom: true,       // autoscroll unless the user scrolled up
        started: false,
        ssOnly: false,           // room filter: show only SoulSync-app messages
        protocolLog: [],         // recent machine-coordination events (bounded)
        beaconed: {},            // rooms we've announced ourselves in this session
        isAdmin: false,          // shows the settings cog (from /status)
        newMarker: null,         // frozen last-seen ts for the NEW divider (per room open)
        renderedCount: 0,        // for the new-messages pill delta
        msgs: [],                // room message store: archive pages + live tail (merged)
        loadingOlder: false,     // scrollback fetch in flight
        historyDone: false,      // no more archive pages
        selfName: '',            // our slskd username (@mention highlighting)
        users: [],               // room user names (mention autocomplete)
        convos: [],              // latest PM conversation list (guild-rail DM badge)
        channel: 'general',      // active virtual channel (envelope `c` tag)
        chanSeen: {},            // channel slug → newest ts read there (unread badges)
        chanCatClosed: {},       // sidebar category → collapsed
        pingArmed: false,        // suppress mention pings while the archive loads
        thread: null,            // {id, name} while viewing a thread (null = channel)
        replyTo: null,           // {u, x} while composing a reply
        pendingReactions: {},    // "msgKey|emoji" self-reactions awaiting slskd echo
        jukebox: {               // shared room listening (reduced from protocolLog)
            open: false,         //   panel visible
            tunedIn: false,      //   player exists (requires a user gesture)
            player: null,        //   YT.Player instance while tuned in
            playingId: null,     //   video id the player was last pointed at
            playingNow: null,    //   the now-track the player is actually playing (display fallback)
            nowSeen: null,       //   {id, localStart, base} — elapsed on OUR clock, not the DJ's
            playerAlive: false,  //   iframe API fired onReady (safe to call methods)
            results: [],         //   resolve results awaiting a pick
            searchResults: [],   //   YouTube search modal results
            resolving: false,
            lastRendered: '',    //   reduced-state fingerprint (skip no-op renders)
            lastAdvanceAt: 0,    //   DJ double-fire guard (ms)
            starvedAt: 0,        //   queue-waiting-with-no-DJ clock (ms)
            histOpen: false,     //   recently-played list expanded
            lastAutoAt: 0,       //   auto-DJ top-up cooldown (ms)
            videoHidden: false,  //   audio-only mode (iframe hidden, audio plays)
            vol: 100,            //   local player volume (persisted)
            timer: null,         //   elapsed clock + DJ watchdog while open
            ytLoading: false, ytCbs: [],
        },
        typing: {},              // username → last typ-event receipt (ms, local clock)
        typingArmedAt: 0,        // ignore typ events replayed from the archive on room open
        lastTypSentAt: 0,        // our own typ throttle
        typingTimer: null,       // pending expiry repaint
        pinsOpen: false,         // pin board expanded
        topicEditing: false,     // head shows the topic input (renderHead pauses)
        pollDismissedAt: null,   // locally-dismissed closed poll (its start ts)
    };
    try { state.ssOnly = localStorage.getItem('chat_ss_only') === '1'; } catch (e) { /* ignore */ }
    try {
        var _ch = localStorage.getItem('chat_channel');
        if (_ch) state.channel = _ch;      // validated against the config on first render
    } catch (e) { /* ignore */ }
    try {
        state.jukebox.videoHidden = localStorage.getItem('chat_jbx_audio') === '1';
        var _v = parseInt(localStorage.getItem('chat_jbx_vol') || '100', 10);
        if (_v >= 0 && _v <= 100) state.jukebox.vol = _v;
    } catch (e) { /* ignore */ }

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

    var MENTION_RE = /@([A-Za-z0-9_.-]{2,32})\b/g;

    function _mentionify(s) {
        var selfLower = String(state.selfName || '').toLowerCase();
        return s.replace(MENTION_RE, function (m, name) {
            var me = selfLower && name.toLowerCase() === selfLower;
            return '<span class="chat-mention' + (me ? ' chat-mention--self' : '') +
                '" data-chat-user="' + name + '">@' + name + '</span>';
        });
    }

    function mentionsMe(text) {
        if (!state.selfName) return false;
        var re = new RegExp('@' + String(state.selfName).replace(/[.*+?^${}()|[\]\\]/g, '\\$&') +
            '(?![A-Za-z0-9_.-])', 'i');
        return re.test(String(text || ''));
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
        s = _mentionify(s);
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
        // 3) emoji shortcodes + @mentions
        s = s.replace(/:([a-z0-9_+-]+):/g, function (m, name) { return EMOJI[name] || m; });
        s = _mentionify(s);
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

    function _avatar(user, avMap) {
        // A chosen preset wins; otherwise the original hue-tinted initial. Used
        // by message groups, the user card and the mention picker, so upgrading
        // it here paints faces everywhere at once.
        var n = _avatarId((avMap || _avatarMap())[user]);
        if (n) {
            return '<span class="chat-avatar chat-avatar--img" aria-hidden="true">' +
                '<img src="/static/avatar/' + n + '.png" alt="" loading="lazy"></span>';
        }
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
        var self = m.self === true || m.direction === 'Out';
        var me = !self && state.view === 'room' && mentionsMe(m.message);
        var replyRef = (m.reply && m.reply.u)
            ? '<div class="chat-reply-ref">↩ <b>' + esc(m.reply.u) + '</b> ' +
              '<span>' + esc(m.reply.x || '') + '</span></div>'
            : '';
        var acts = '<button type="button" class="chat-line-reply" title="Copy text" ' +
            'data-chat-copy="' + attr(String(m.message || '')) + '">⧉</button>';
        if (state.view === 'room' && state.canSend && !self) {
            acts = '<button type="button" class="chat-line-reply" title="React" ' +
                'data-chat-react-user="' + attr(m.username || '') + '" ' +
                'data-chat-react-text="' + attr(String(m.message || '')) + '">🙂+</button>' +   // FULL text — the react key is a hash of it
                '<button type="button" class="chat-line-reply" title="Reply" ' +
                'data-chat-reply-user="' + attr(m.username || '') + '" ' +
                'data-chat-reply-x="' + attr(String(m.message || '').slice(0, 100)) + '">↩</button>' + acts;
        }
        if (state.view === 'room' && state.canSend && !state.thread && _chanRoom()) {
            acts += '<button type="button" class="chat-line-reply" title="Start a thread on this message" ' +
                'data-chat-thread-start="' + attr(_msgKey(m)) + '" ' +
                'data-chat-thread-title="' + attr(String(m.message || '').slice(0, 60)) + '">🧵</button>';
        }
        if (state.view === 'room' && state.canSend) {
            acts += '<button type="button" class="chat-line-reply" title="Pin to the room board" ' +
                'data-chat-pin-user="' + attr(m.username || '') + '" ' +
                'data-chat-pin-ts="' + attr(String(m.timestamp || '')) + '" ' +
                'data-chat-pin-x="' + attr(String(m.message || '').slice(0, 140)) + '">📌</button>';
        }
        var actions = '<span class="chat-line-acts">' + acts + '</span>';
        var chips = '';
        if (m.reactions && m.reactions.length) {
            chips = '<div class="chat-react-row">' + m.reactions.map(function (r) {
                return '<span class="chat-react-chip" title="' +
                    attr((r.users || []).join(', ')) + '">' + esc(r.e) +
                    (r.n > 1 ? ' <b>' + r.n + '</b>' : '') + '</span>';
            }).join('') + '</div>';
        }
        var bodyHtml = (m.file && m.file.n)
            ? _fileCardHtml(m)
            : (m.rich ? renderRich(m.message) : renderPlain(m.message));
        return '<div class="chat-line' + (me ? ' chat-line--me' : '') + '" title="' +
            attr(_fullTs(m.timestamp)) + '">' + replyRef +
            bodyHtml + actions + chips + '</div>';
    }

    // ── shared file card (filepost.dev links dressed by envelope 'f') ────
    var _AUDIO_EXT = /\.(flac|mp3|m4a|ogg|opus|wav|aiff?)$/i;
    var _VIDEO_EXT = /\.(mp4|mkv|webm|mov)$/i;
    var _IMAGE_EXT = /\.(jpe?g|png|gif|webp)$/i;

    function _fileCardHtml(m) {
        var url = String(m.message || '').trim();
        var f = m.file || {};
        var name = String(f.n || 'file');
        var mime = String(f.m || '');
        var isAudio = mime.indexOf('audio/') === 0 || _AUDIO_EXT.test(name);
        var isVideo = mime.indexOf('video/') === 0 || _VIDEO_EXT.test(name);
        var isImage = mime.indexOf('image/') === 0 || _IMAGE_EXT.test(name);
        if (!/^https:\/\//i.test(url)) {
            // hostile envelope: card metadata on a non-https 'link' — render
            // as plain text instead of a clickable trap
            return m.rich ? renderRich(m.message) : renderPlain(m.message);
        }
        var icon = isAudio ? '🎵' : isVideo ? '🎬' : isImage ? '🖼' : '📄';
        var preview = '';
        if (isAudio) {
            preview = '<button type="button" class="chat-embed-chip" data-chat-file-audio="' +
                attr(url) + '">▶ preview</button>';
        } else if (isVideo) {
            preview = '<button type="button" class="chat-embed-chip" data-chat-file-video="' +
                attr(url) + '">▶ preview</button>';
        } else if (isImage) {
            preview = '<button type="button" class="chat-embed-chip" data-chat-embed-img="' +
                attr(url) + '">🖼 show</button>';
        }
        return '<div class="chat-file-card">' +
            '<span class="chat-file-icon">' + icon + '</span>' +
            '<span class="chat-file-meta"><b class="chat-file-name">' + esc(name) + '</b>' +
            (f.s ? '<span class="chat-file-size">' + esc(_fmtBytes(f.s)) + '</span>' : '') +
            '</span>' +
            preview +
            (isAudio
                ? '<button type="button" class="chat-embed-chip chat-file-save" ' +
                    'data-chat-file-save="' + attr(url) + '" data-chat-file-name="' + attr(name) +
                    '" data-chat-file-mime="' + attr(mime) + '">➕ save to library</button>'
                : '') +
            '<a class="chat-embed-chip chat-file-dl" href="' + attr(url) +
                '" target="_blank" rel="noopener noreferrer" download>⬇ download</a>' +
            '<div class="chat-file-slot"></div></div>';
    }

    // Save a shared audio file into the library: hand the filepost link to the
    // server, which drops it in the import staging folder for the pipeline.
    function _saveFileToLibrary(btn) {
        if (!btn || btn.disabled) return;
        var url = btn.getAttribute('data-chat-file-save');
        var name = btn.getAttribute('data-chat-file-name') || '';
        var mime = btn.getAttribute('data-chat-file-mime') || '';
        btn.disabled = true;
        var was = btn.textContent;
        btn.textContent = 'saving…';
        postJSON('/api/chat/files/import', { url: url, name: name, mime: mime })
            .then(function (res) {
                if (res.ok && res.body && res.body.ok) {
                    btn.textContent = '✓ saved';
                    if (typeof showToast === 'function') {
                        showToast(res.body.auto_import
                            ? '➕ Saved — auto-import will pick it up'
                            : '➕ Saved to your Staging folder — import it from the Import page',
                            'success');
                    }
                    return;
                }
                btn.disabled = false;
                btn.textContent = was;
                if (typeof showToast === 'function') {
                    showToast((res.body && res.body.error) || 'Could not save that file', 'error');
                }
            })
            .catch(function () {
                btn.disabled = false;
                btn.textContent = was;
                if (typeof showToast === 'function') showToast('Could not save that file', 'error');
            });
    }

    // Consecutive messages from the same sender (same app-ness, <5 min apart)
    // fold under one avatar + name header, with day separators between dates.
    function renderGroups(msgs) {
        var html = '', group = null, lastDay = null, GAP = 5 * 60 * 1000;
        var avMap = _avatarMap();      // fold once per render, not per group
        function flush() { if (group) { html += group.html + '</div></div>'; group = null; } }
        for (var i = 0; i < msgs.length; i++) {
            var m = msgs[i];
            var user = m.username || m.user || '?';
            var self = m.self === true || m.direction === 'Out';
            // slskd stamps username = the CONVERSATION PARTNER on both
            // directions of a PM (live-verified) — our own messages must
            // wear our name, not theirs
            if (self && state.view === 'pm') user = state.selfName || 'you';
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
                _avatar(user, avMap) +
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

    var _pillCount = 0;

    function hideJumpPill() {
        _pillCount = 0;
        var pill = q('[data-chat-jump]');
        if (pill) pill.hidden = true;
    }

    function showJumpPill(added) {
        _pillCount += added;
        var pill = q('[data-chat-jump]');
        if (!pill) return;
        pill.textContent = (_pillCount > 1 ? _pillCount + ' new messages' : 'New messages') + ' ↓';
        pill.hidden = false;
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
        var shown = msgs, hidden = 0, muted = 0;
        if (state.view === 'room') {
            var ign = ignoredSet();
            if (ign.length) {
                shown = shown.filter(function (m) {
                    if (ign.indexOf(String(m.username || '')) > -1 &&
                            !(m.self === true || m.direction === 'Out')) { muted++; return false; }
                    return true;
                });
            }
            if (state.ssOnly) {
                var before = shown.length;
                shown = shown.filter(function (m) { return m.rich || m.self === true || m.direction === 'Out'; });
                hidden = before - shown.length;
            }
            // Virtual channels: show only the active one. Untagged / unknown-slug
            // messages fold into the default channel (never hidden everywhere),
            // so vanilla-Soulseek and old-client traffic still reads in #general.
            // Only the SoulSync room is channelled/threaded — every other room
            // shows its stream plainly, exactly as it did before.
            if (_chanRoom()) {
                shown = shown.filter(function (m) { return _msgChannel(m) === state.channel; });
                // Inside a thread: only its parent + its replies. Outside: thread
                // replies fold away so the channel stays readable (Discord-style).
                if (state.thread) {
                    var tid = state.thread.id;
                    shown = shown.filter(function (m) {
                        return _msgThread(m) === tid || _msgKey(m) === tid;
                    });
                } else {
                    shown = shown.filter(function (m) { return !_msgThread(m); });
                }
            }
        }
        // NEW divider: split at the frozen last-seen marker (set on room open).
        // Groups deliberately break at the divider, like Discord's red line.
        var body;
        if (state.view === 'room' && state.newMarker) {
            var seen = [], unseen = [];
            shown.forEach(function (m) {
                (String(m.timestamp || '') > state.newMarker ? unseen : seen).push(m);
            });
            body = renderGroups(seen) +
                (unseen.length && seen.length
                    ? '<div class="chat-new-sep"><span>NEW</span></div>' : '') +
                renderGroups(unseen);
        } else {
            body = renderGroups(shown);
        }
        host.innerHTML = body +
            (hidden ? '<button type="button" class="chat-hidden-note" data-chat-filter>' + hidden +
                ' message' + (hidden === 1 ? '' : 's') + ' from other Soulseek clients hidden — show</button>' : '') +
            (muted ? '<div class="chat-hidden-note">' + muted +
                ' message' + (muted === 1 ? '' : 's') + ' from muted users hidden</div>' : '');
        if (state.stickBottom) {
            host.scrollTop = host.scrollHeight;
            // deep-scrollback cleanup: once the reader is back at the bottom,
            // trim the store so steady-state renders stay light (they can
            // always page history again)
            if (state.view === 'room' && state.msgs.length > 300) {
                state.msgs = state.msgs.slice(-300);
                state.historyDone = false;
            }
        } else if (shown.length > state.renderedCount && state.renderedCount > 0) {
            showJumpPill(shown.length - state.renderedCount);   // arrivals while scrolled up
        }
        state.renderedCount = shown.length;
        // seen upkeep: reading at the bottom advances the stored marker (the
        // frozen divider position doesn't move until the next room open)
        if (state.view === 'room' && pageVisible() && state.stickBottom && msgs.length) {
            try {
                localStorage.setItem('chat_seen_' + (state.room || ''),
                    String(msgs[msgs.length - 1].timestamp || ''));
            } catch (e) { /* ignore */ }
        }
    }

    // ── ignore list (local mute — per browser, hides messages + greys the user) ──
    function ignoredSet() {
        try { return JSON.parse(localStorage.getItem('chat_ignored') || '[]'); }
        catch (e) { return []; }
    }
    function isIgnored(name) { return ignoredSet().indexOf(name) > -1; }
    function toggleIgnored(name) {
        if (!name) return;
        var list = ignoredSet();
        var i = list.indexOf(name);
        if (i > -1) list.splice(i, 1); else list.push(name);
        try { localStorage.setItem('chat_ignored', JSON.stringify(list)); } catch (e) { /* ignore */ }
        state.lastStamp = null;
        renderMessages(state.msgs);
        renderUsersList();
    }

    // Users who spoke through SoulSync (the envelope is the app signature) —
    // sourced from the loaded messages, so it's an approximation of "runs
    // SoulSync", not a directory.
    function _userClassification() {
        // {name: 'soulsync'|'vanilla'} — the assume-SoulSync flip: names
        // absent from this map never spoke and are treated as SoulSync.
        // Built with ChatProtocol.classifyUser (envelope conclusive forever,
        // protocol events count as envelopes; only bare text marks vanilla).
        var cls = {};
        var CP = window.ChatProtocol;
        (state.msgs || []).forEach(function (m) {
            if (!m.username) return;
            cls[m.username] = CP ? CP.classifyUser(cls[m.username], !!m.rich)
                                 : (m.rich ? 'soulsync' : (cls[m.username] || 'vanilla'));
        });
        (state.protocolLog || []).forEach(function (ev) {
            if (ev && ev.username) cls[ev.username] = 'soulsync';
        });
        return cls;
    }

    function _userBtn(n, extraClass, tunedMap, npMap, avMap) {
        // Discord-style member row: avatar + presence dot, name, and an activity
        // subline (the jukebox listen state doubles as "playing a game").
        var ign = isIgnored(n);
        var tuned = tunedMap && tunedMap[n];
        var np = npMap && npMap[n];
        return '<button class="chat-user' + (extraClass || '') + (ign ? ' chat-user--ignored' : '') +
            '" type="button" data-chat-user="' + attr(n) + '" title="' + attr(n) +
            (tuned ? ' — listening to the room jukebox' : '') + '">' +
            '<span class="chat-user-av">' +
                _avatarHtml(n, avMap && avMap[n], 'chat-av--fill') +
                '<span class="chat-user-dot' + (tuned ? ' chat-user-dot--tuned' : '') + '"></span>' +
            '</span>' +
            '<span class="chat-user-main">' +
                '<span class="chat-user-name">' + esc(n) + '</span>' +
                // the shared jukebox wins the line — it's what the room is doing
                // together; a personal now-playing shows otherwise
                (tuned
                    ? '<span class="chat-user-act chat-user-tuned">♫ Listening to the jukebox</span>'
                    : (np && np.t
                        ? '<span class="chat-user-act" title="' + attr(np.t + (np.a ? ' — ' + np.a : '')) +
                            '">♪ ' + esc(np.t) + (np.a ? ' · ' + esc(np.a) : '') + '</span>'
                        : '')) +
            '</span>' +
            (ign ? '<span class="chat-user-mute">muted</span>' : '') + '</button>';
    }

    function renderUsers(users) {
        var host = q('[data-chat-users]');
        if (!host) return;
        if (state.view !== 'room' || !users || !users.length) {
            host.innerHTML = ''; host.hidden = true; state.userFilter = ''; return;
        }
        host.hidden = false;
        state.users = users.map(function (u) { return String(u.username || u || ''); }).filter(Boolean);
        // static skeleton once — the search input must survive the 4s poll
        if (!host.querySelector('[data-chat-user-search]')) {
            host.innerHTML =
                '<input class="chat-user-search" data-chat-user-search type="text" ' +
                    'placeholder="Find a user…" autocomplete="off">' +
                '<div data-chat-user-list></div>';
        }
        renderUsersList();
    }

    function renderUsersList() {
        var listHost = q('[data-chat-user-list]');
        if (!listHost) return;
        var f = String(state.userFilter || '').toLowerCase();
        var names = state.users.slice().sort(function (a, b) {
            return a.toLowerCase().localeCompare(b.toLowerCase());
        });
        if (f) names = names.filter(function (n) { return n.toLowerCase().indexOf(f) > -1; });
        var cls = _userClassification();
        var self = [], apps = [], rest = [];
        names.forEach(function (n) {
            if (state.selfName && n === state.selfName) self.push(n);
            // the flip: unknown (never spoke) = assumed SoulSync
            else if (cls[n] !== 'vanilla') apps.push(n);
            else rest.push(n);
        });
        var _evs = window.ChatProtocol ? _roomEvents() : [];
        var tunedMap = window.ChatProtocol
            ? window.ChatProtocol.reduceTuned(_evs) : {};            // once, not per user
        var npMap = (window.ChatProtocol && window.ChatProtocol.reduceNowPlaying)
            ? window.ChatProtocol.reduceNowPlaying(_evs) : {};
        var avMap = _avatarMap();
        // Discord groups members by role with a "NAME — count" header.
        var html = '';
        if (self.length) {
            html += '<div class="chat-users-label chat-users-label--sub">You</div>' +
                self.map(function (n) { return _userBtn(n, ' chat-user--self', tunedMap, npMap, avMap); }).join('');
        }
        if (apps.length) {
            html += '<div class="chat-users-label chat-users-label--sub">SoulSync &mdash; ' + apps.length + '</div>' +
                apps.map(function (n) { return _userBtn(n, '', tunedMap, npMap, avMap); }).join('');
        }
        if (rest.length) {
            // NOT "Online" — the SoulSync bucket above is online too; this one
            // is specifically everyone on a non-SoulSync client.
            html += '<div class="chat-users-label chat-users-label--sub">Other clients &mdash; ' +
                rest.length + '</div>' +
                rest.map(function (n) { return _userBtn(n, '', tunedMap, npMap, avMap); }).join('');
        }
        if (!self.length && !apps.length && !rest.length) {
            html += '<div class="chat-side-none">No users match</div>';
        }
        listHost.innerHTML = html;
    }

    function renderSide(convos) {
        if (convos) state.convos = convos;   // guild-rail DM badge reads the latest list
        // Rooms are rendered by renderGuilds() into the guild rail — switching,
        // browsing and leaving all live there. They are deliberately not listed
        // in this sidebar too (that was two Browse-rooms buttons and two room
        // lists saying the same thing).
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
        renderGuilds();
        renderChannels();
        renderUserPanel();
    }

    // ── Discord-style shell: guild rail, channels, account strip ────────────
    // CHANNELS are a client-side VIEW over the one Soulseek room: each message
    // carries a channel slug in its envelope (see CHAT_CHANNELS / state.channel).
    // Untagged or unknown-slug messages always fall back to #general so nothing
    // is ever invisible — old clients and vanilla Soulseek users still land
    // somewhere. Categories are cosmetic grouping only.
    // No channel names a FEATURE. The jukebox is room-scoped — everyone shares
    // one queue regardless of which channel they're reading — so filing it under
    // a channel would imply a queue per channel. Tune-in is already its gate.
    // Media-agnostic on purpose: SoulSync is music AND movies/TV AND YouTube,
    // so nothing here is scoped to one side. Names avoid colliding with actual
    // app features too — a '#requests' channel would read as the video Requests
    // queue, and '#releases' as a SoulSync release rather than a new album.
    // Mirrors where the real community traffic already goes.
    var CHAT_CHANNELS = [
        { cat: 'Community', items: [
            { slug: 'general', name: 'general' },
            { slug: 'off-topic', name: 'off-topic' },
        ] },
        { cat: 'Support', items: [
            { slug: 'help', name: 'help' },
            { slug: 'bugs', name: 'bugs' },
            { slug: 'ideas', name: 'ideas' },
        ] },
    ];
    var CHAT_DEFAULT_CHANNEL = 'general';

    // Channels + threads are for the SoulSync community room ONLY. In any other
    // Soulseek room nobody tags anything, so a channel rail would file every
    // message under #general and strand the other channels empty — and the
    // thread fold would HIDE replies with no sidebar to find them again. Other
    // rooms therefore get plain, unfiltered chat (the jukebox / polls / pins
    // still work there, since those are additive folds that are simply empty
    // when nobody has used them).
    function _chanRoom() {
        return state.view === 'room' &&
               (!state.homeRoom || state.room === state.homeRoom);
    }

    function _chanKnown(slug) {
        for (var i = 0; i < CHAT_CHANNELS.length; i++) {
            for (var j = 0; j < CHAT_CHANNELS[i].items.length; j++) {
                if (CHAT_CHANNELS[i].items[j].slug === slug) return true;
            }
        }
        return false;
    }

    // The channel a message belongs to. Unknown/absent → the default, so a
    // message can never be swallowed by a channel nobody is looking at.
    function _msgChannel(m) {
        var c = m && typeof m.chan === 'string' ? m.chan : '';
        return _chanKnown(c) ? c : CHAT_DEFAULT_CHANNEL;
    }

    function _chanUnread() {
        // Unread per channel = messages after our last-read marker for that
        // channel, excluding our own. Cheap fold over the loaded message list.
        var counts = {};
        var seen = state.chanSeen || {};
        (state.msgs || []).forEach(function (m) {
            if (!m || m.username === state.selfName) return;
            var c = _msgChannel(m);
            if (c === state.channel) return;              // looking at it now
            var ts = m.timestamp || '';
            if (seen[c] && ts <= seen[c]) return;
            counts[c] = (counts[c] || 0) + 1;
        });
        return counts;
    }

    function renderGuilds() {
        var host = q('[data-chat-guilds]');
        if (!host) return;
        var rooms = (state.rooms.length ? state.rooms
            : [{ name: state.homeRoom || state.room || 'SoulSync', home: true }]);
        var html = rooms.map(function (r) {
            var on = state.view === 'room' && state.room === r.name;
            var initials = String(r.name || '?').replace(/[^A-Za-z0-9]/g, '').slice(0, 2).toUpperCase() || '#';
            // The rail is the ONLY room switcher now (the sidebar lists channels,
            // not rooms), so leaving has to live here too — × on hover, home room
            // excluded, same rule the old sidebar list used.
            return '<span class="chat-guild-wrap">' +
                '<button class="chat-guild' + (on ? ' chat-guild--on' : '') + '" type="button" ' +
                    'data-chat-open-room="' + attr(r.name) + '" title="' + attr(r.name) + '">' +
                    esc(initials) + '</button>' +
                (!r.home && state.canManage
                    ? '<button class="chat-guild-leave" type="button" data-chat-leave-room="' +
                        attr(r.name) + '" title="Leave ' + attr(r.name) + '">&times;</button>'
                    : '') +
            '</span>';
        }).join('');
        // PM puck — unread dot when any conversation is waiting
        var pmUnread = (state.convos || []).filter(function (c) {
            return c.hasUnAcknowledgedMessages || c.unAcknowledgedMessageCount > 0;
        }).length;
        html += '<div class="chat-guild-sep"></div>' +
            '<button class="chat-guild' + (state.view === 'pm' ? ' chat-guild--on' : '') + '" type="button" ' +
                'data-chat-guild-dm title="Direct messages">✉' +
                (pmUnread ? '<span class="chat-guild-badge">' + (pmUnread > 99 ? '99+' : pmUnread) + '</span>' : '') +
            '</button>';
        if (state.canManage) {
            html += '<button class="chat-guild chat-guild--add" type="button" data-chat-browse-rooms ' +
                'title="Browse Soulseek rooms">+</button>';
        }
        host.innerHTML = html;
    }

    function renderChannels() {
        // sidebar header = the "server" (the Soulseek room we're in)
        var nameEl = q('[data-chat-side-head-name]');
        if (nameEl) {
            nameEl.textContent = state.view === 'pm'
                ? 'Direct Messages'
                : (state.room || state.homeRoom || 'SoulSync');
        }
        var host = q('[data-chat-channels]');
        if (!host) return;
        if (!_chanRoom()) { host.innerHTML = ''; return; }   // plain chat elsewhere
        if (!_chanKnown(state.channel)) state.channel = CHAT_DEFAULT_CHANNEL;
        var unread = _chanUnread();
        var closed = state.chanCatClosed || {};
        host.innerHTML = CHAT_CHANNELS.map(function (group) {
            var isClosed = !!closed[group.cat];
            var rows = isClosed ? '' : group.items.map(function (ch) {
                var on = state.channel === ch.slug;
                var n = unread[ch.slug] || 0;
                var row = '<button class="chat-chan' + (on ? ' chat-chan--on' : '') +
                    (n ? ' chat-chan--unread' : '') + '" type="button" ' +
                    'data-chat-chan="' + attr(ch.slug) + '">' +
                    '<span class="chat-chan-hash">#</span>' +
                    '<span class="chat-chan-name">' + esc(ch.name) + '</span>' +
                    (n ? '<span class="chat-chan-unread">' + (n > 99 ? '99+' : n) + '</span>' : '') +
                '</button>';
                // Forum-style: the active channel's threads hang beneath it.
                if (on) {
                    row += _threadsForChannel().map(function (t) {
                        var tOn = state.thread && state.thread.id === t.id;
                        return '<button class="chat-thread' + (tOn ? ' chat-thread--on' : '') +
                            '" type="button" data-chat-thread="' + attr(t.id) + '" ' +
                            'data-chat-thread-name="' + attr(t.name) + '" title="' + attr(t.name) + '">' +
                            '<span class="chat-thread-branch"></span>' +
                            '<span class="chat-thread-name">' + esc(t.name) + '</span>' +
                        '</button>';
                    }).join('');
                }
                return row;
            }).join('');
            return '<button class="chat-cat' + (isClosed ? ' chat-cat--closed' : '') + '" type="button" ' +
                    'data-chat-cat="' + attr(group.cat) + '">' +
                    '<span class="chat-cat-caret">⌄</span>' + esc(group.cat) +
                '</button>' + rows;
        }).join('');
    }

    // ── threads ─────────────────────────────────────────────────────────────
    // A thread is messages tagged with a parent message key (`th`), folded out
    // of the channel stream. Same discipline as channels: purely a view over
    // the one room, and a thread's replies never vanish — they're still in the
    // room for vanilla clients, just grouped here.
    function _msgThread(m) {
        return (m && typeof m.th === 'string' && m.th) ? m.th : null;
    }

    // Threads that belong to the ACTIVE channel, newest activity first.
    function _threadsForChannel() {
        var byId = {};
        (state.msgs || []).forEach(function (m) {
            var th = _msgThread(m);
            if (!th) return;
            if (_msgChannel(m) !== state.channel) return;
            var t = byId[th] || (byId[th] = { id: th, name: '', count: 0, last: '' });
            t.count++;
            var ts = String(m.timestamp || '');
            if (ts > t.last) t.last = ts;
            if (!t.name && typeof m.tn === 'string' && m.tn) t.name = m.tn;
        });
        // Fall back to the parent message's text when no carried name survived.
        var out = [];
        for (var id in byId) {
            if (!Object.prototype.hasOwnProperty.call(byId, id)) continue;
            var t = byId[id];
            if (!t.name) {
                var parent = (state.msgs || []).filter(function (m) { return _msgKey(m) === id; })[0];
                t.name = parent ? String(parent.message || '').slice(0, 60) : 'Thread';
            }
            out.push(t);
        }
        out.sort(function (a, b) { return b.last.localeCompare(a.last); });
        return out;
    }

    function openThread(id, name) {
        if (!id) return;
        state.thread = { id: id, name: name || 'Thread' };
        state.lastStamp = null;
        state.newMarker = null;
        renderMessages(state.msgs);
        renderHead();
        renderComposer();
        renderChannels();
    }

    function closeThread() {
        if (!state.thread) return;
        state.thread = null;
        state.lastStamp = null;
        state.newMarker = null;
        renderMessages(state.msgs);
        renderHead();
        renderComposer();
        renderChannels();
    }

    function switchChannel(slug) {
        state.thread = null;      // leaving the channel leaves its thread
        if (!slug || !_chanKnown(slug) || slug === state.channel) return;
        state.channel = slug;
        try { localStorage.setItem('chat_channel', slug); } catch (e) { /* private mode */ }
        // Mark everything currently loaded in this channel as read.
        var newest = '';
        (state.msgs || []).forEach(function (m) {
            if (_msgChannel(m) === slug) {
                var ts = String(m.timestamp || '');
                if (ts > newest) newest = ts;
            }
        });
        if (newest) state.chanSeen[slug] = newest;
        state.lastStamp = null;      // force a repaint — the filter changed, not the data
        state.newMarker = null;
        renderMessages(state.msgs);
        renderHead();
        renderComposer();
        renderChannels();
    }

    function renderUserPanel() {
        var host = q('[data-chat-userpanel]');
        if (!host) return;
        var name = state.selfName || 'You';
        var initials = String(name).replace(/[^A-Za-z0-9]/g, '').slice(0, 2).toUpperCase() || '?';
        host.innerHTML =
            '<div class="chat-userpanel-av">' + esc(initials) + '</div>' +
            '<div class="chat-userpanel-main">' +
                '<div class="chat-userpanel-name">' + esc(name) + '</div>' +
                '<div class="chat-userpanel-sub">' +
                    (state.canSend ? 'Online' : 'Read-only') + '</div>' +
            '</div>' +
            (state.isAdmin
                ? '<button class="chat-userpanel-btn" type="button" data-chat-settings-btn ' +
                    'title="Chat settings">⚙</button>'
                : '');
    }

    function renderHead() {
        var head = q('[data-chat-head]');
        if (!head) return;
        if (state.topicEditing) return;   // don't clobber the open topic input
        var isHome = !state.homeRoom || state.room === state.homeRoom;
        var topic = (window.ChatProtocol && state.view === 'room')
            ? window.ChatProtocol.reduceTopic(_roomEvents()) : null;
        var subText = topic
            ? esc(topic.t)
            : (isHome ? 'the SoulSync community room on Soulseek'
                      : 'a public Soulseek room');
        // A stale/unknown persisted slug must never strand the user on an empty
        // view — snap back to the default before anything renders against it.
        if (!_chanKnown(state.channel)) state.channel = CHAT_DEFAULT_CHANNEL;
        head.innerHTML = state.view === 'room'
            ? (state.thread && _chanRoom()
                ? '<button class="chat-thread-back" type="button" data-chat-thread-close ' +
                      'title="Back to #' + attr(state.channel) + '">&larr;</button>' +
                  '<span class="chat-head-title">🧵 ' + esc(state.thread.name || 'Thread') + '</span>'
                : '<span class="chat-head-title">#' +
                      esc(_chanRoom() ? state.channel : (state.room || '')) + '</span>') +
              '<span class="chat-head-sub' + (topic ? ' chat-head-sub--topic' : '') + '"' +
                  (topic ? ' title="topic set by ' + attr(topic.by) + '"' : '') + '>' + subText +
                  (state.canSend
                      ? ' <button class="chat-topic-edit" type="button" data-chat-topic-edit ' +
                        'title="Set the room topic (SoulSync clients only)">✎</button>'
                      : '') + '</span>' +
              '<span class="chat-head-search' + (state.searchMode ? ' chat-head-search--on' : '') + '">' +
                  '<button class="chat-filter-btn" type="button" data-chat-search-btn title="Search this room\'s history">🔍</button>' +
                  '<input class="chat-head-search-in" data-chat-search-input type="text" ' +
                      'placeholder="Search history…" autocomplete="off"' +
                      (state.searchMode ? '' : ' hidden') + '>' +
              '</span>' +
              '<button class="chat-filter-btn' + (state.pinsOpen ? ' chat-filter-btn--on' : '') +
              '" type="button" data-chat-pins-toggle title="Pinned messages">📌' +
              (function () {
                  var n = window.ChatProtocol ? window.ChatProtocol.reducePins(_roomEvents()).length : 0;
                  return n ? ' ' + n : '';
              })() + '</button>' +
              '<button class="chat-filter-btn' + (state.jukebox.open ? ' chat-filter-btn--on' : '') +
              '" type="button" data-chat-jukebox-btn title="Room jukebox — listen together, vote on what plays next">♫ Jukebox</button>' +
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
        form.hidden = false;   // the join gate hides it; every normal render restores it
        form.classList.toggle('chat-composer--locked', !state.canSend);
        input.disabled = !state.canSend;
        input.placeholder = state.canSend
            ? (state.view === 'room'
                ? 'Message #' + (_chanRoom() ? (state.channel || CHAT_DEFAULT_CHANNEL)
                                             : (state.room || '')) + '…'
                : 'Message ' + (state.pmUser || '') + '…')
            : 'Read-only — chat sending is admin-only on this server';
        // Formatting only exists inside the envelope — the toolbar is a ROOM
        // thing (PMs are plaintext for non-SoulSync readers + the ProveIt bots).
        var bar = q('[data-chat-toolbar]');
        if (bar) bar.hidden = !(state.view === 'room' && state.canSend);
        // GIF = sending a CDN URL through the room pipeline — room-only. The
        // emoji button stays everywhere (plain unicode is fine in PMs).
        var gifBtn = q('[data-chat-gif-btn]');
        if (gifBtn) gifBtn.hidden = !(state.view === 'room' && state.canSend);
        // polls are a room thing (bus events mean nothing in a PM)
        var pollBtn = q('[data-chat-poll-btn]');
        if (pollBtn) pollBtn.hidden = !(state.view === 'room' && state.canSend);
        if (state.view !== 'room') { toggleEmojiPicker(true); toggleGifPicker(true); togglePollPop(true); }
    }

    // ── composer toolbar (room only) ─────────────────────────────────────────
    var _FMT = { bold: ['**', '**'], italic: ['*', '*'], strike: ['~~', '~~'],
                 code: ['`', '`'], codeblock: ['```\n', '\n```'],
                 spoiler: ['||', '||'], quote: ['> ', ''] };

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

    // ── reply composing (chatbic P3) ─────────────────────────────────────────
    function startReply(u, x) {
        if (state.view !== 'room' || !state.canSend || !u) return;
        state.replyTo = { u: u, x: x || '' };
        var bar = q('[data-chat-reply-bar]');
        var who = q('[data-chat-reply-who]');
        var ex = q('[data-chat-reply-excerpt]');
        if (who) who.textContent = u;
        if (ex) ex.textContent = x || '';
        if (bar) bar.hidden = false;
        var input = q('[data-chat-input]');
        if (input) input.focus();
    }

    function cancelReply() {
        state.replyTo = null;
        var bar = q('[data-chat-reply-bar]');
        if (bar) bar.hidden = true;
    }

    // ── reactions (chatbic P4) ───────────────────────────────────────────────
    var QUICK_REACTS = ['👍', '❤️', '😂', '🔥', '🎵', '👀', '💯'];

    function showReactRow(anchorBtn, user, text) {
        closeReactRow();
        var row = document.createElement('div');
        row.className = 'chat-react-pick';
        row.setAttribute('data-chat-react-pick-row', '1');
        row.innerHTML = QUICK_REACTS.map(function (e2) {
            return '<button type="button" class="chat-emoji" data-chat-react-do="' + e2 + '">' + e2 + '</button>';
        }).join('');
        row._target = { user: user, text: text };
        anchorBtn.parentNode.insertBefore(row, anchorBtn.nextSibling);
    }

    function closeReactRow() {
        var old = document.querySelector('[data-chat-react-pick-row]');
        if (old) old.remove();
    }

    function sendReaction(target, emoji) {
        closeReactRow();
        if (!target || !emoji) return;
        // Optimistic: show the reaction instantly instead of waiting for the
        // round-trip AND for slskd to echo our own reaction envelope back into
        // the room buffer (which can lag several seconds). The pending mark
        // keeps it from flickering away on the reconcile before the echo lands.
        var msg = null;
        for (var i = 0; i < state.msgs.length; i++) {
            var m = state.msgs[i];
            if (String(m.username || '') === String(target.user || '') &&
                    String(m.message || '') === String(target.text || '')) { msg = m; break; }
        }
        if (msg && state.selfName) {
            _addReactor(msg, emoji, state.selfName);
            state.pendingReactions[_msgKey(msg) + '|' + emoji] = 1;
            state.lastStamp = null;
            renderMessages(state.msgs);
        }
        postJSON('/api/chat/room/react', {
            target_user: target.user, target_text: target.text, e: emoji,
            room: state.room || '',
        }).then(function (res) {
            if (!res.ok) {
                if (typeof showToast === 'function') {
                    showToast(res.body && res.body.error || 'Reaction not sent', 'error');
                }
                // roll back the optimistic mark so a failed send doesn't stick
                if (msg) delete state.pendingReactions[_msgKey(msg) + '|' + emoji];
                return;
            }
            state.lastStamp = null;
            refresh();
        });
    }

    // ── user popover card ────────────────────────────────────────────────────
    function openUserCard(name) {
        if (!name) return;
        var overlay = q('[data-chat-user-card]');
        if (!overlay) { openPm(name); return; }
        var body = q('[data-chat-user-card-body]');
        if (body) {
            body.innerHTML = '<div class="chat-card-head">' + _avatar(name) +
                '<span class="chat-card-name">' + esc(name) + '</span></div>' +
                '<div class="chat-card-info">Loading…</div>';
        }
        overlay.hidden = false;
        overlay.setAttribute('data-chat-user-card-for', name);
        var ignBtn = overlay.querySelector('[data-chat-card-ignore]');
        if (ignBtn) {
            ignBtn.hidden = state.selfName && name === state.selfName;
            ignBtn.textContent = isIgnored(name) ? 'Unmute' : 'Mute';
            ignBtn.title = isIgnored(name)
                ? 'Show this user’s messages again'
                : 'Hide this user’s messages (this browser only)';
        }
        getJSON('/api/chat/user/' + encodeURIComponent(name)).then(function (res) {
            if (overlay.getAttribute('data-chat-user-card-for') !== name) return;
            var info = (res.ok && res.body.info) || {};
            var status = (res.ok && res.body.status) || {};
            var hist = (res.ok && res.body.history) || null;
            var note = (res.ok && typeof res.body.note === 'string') ? res.body.note : '';
            var rows = [];
            var pres = status.presence || status.status ||
                (status.isOnline === true ? 'Online' : (status.isOnline === false ? 'Offline' : null));
            if (pres != null) rows.push(['Status', String(pres)]);
            if (info.description) rows.push(['About', String(info.description).slice(0, 300)]);
            if (info.uploadSlots != null) rows.push(['Upload slots', String(info.uploadSlots)]);
            if (info.queueLength != null) rows.push(['Queue', String(info.queueLength)]);
            if (info.hasFreeUploadSlot != null) {
                rows.push(['Free slot', info.hasFreeUploadSlot ? 'yes' : 'no']);
            }
            var infoHost = overlay.querySelector('.chat-card-info');
            if (infoHost) {
                var html = rows.length
                    ? rows.map(function (r) {
                        return '<div class="chat-card-row"><span>' + esc(r[0]) +
                            '</span><b>' + esc(r[1]) + '</b></div>';
                    }).join('')
                    : '<div class="chat-card-row chat-card-none">No info available</div>';
                // OUR history with this peer — the card no other client has
                if (hist && hist.downloads > 0) {
                    html += '<div class="chat-card-hist">' +
                        '<div class="chat-card-row"><span>Downloads from them</span><b>' +
                            esc(String(hist.downloads)) + '</b></div>' +
                        (hist.success_rate != null
                            ? '<div class="chat-card-row"><span>Success rate</span><b>' +
                                esc(String(hist.success_rate)) + '%</b></div>' : '') +
                        (hist.total_bytes > 0
                            ? '<div class="chat-card-row"><span>Data pulled</span><b>' +
                                esc(_fmtBytes(hist.total_bytes)) + '</b></div>' : '') +
                        (hist.last_download
                            ? '<div class="chat-card-row"><span>Last download</span><b>' +
                                esc(String(hist.last_download).slice(0, 16)) + '</b></div>' : '') +
                        '</div>';
                }
                // private local note ("great jazz rips") — never leaves this install
                html += '<div class="chat-card-note">' +
                    '<textarea class="chat-card-note-input" data-chat-card-note ' +
                        'placeholder="Private note about ' + attr(name) + '\u2026" ' +
                        'maxlength="2000" rows="2">' + esc(note) + '</textarea>' +
                    '<button class="chat-fmt-btn chat-card-note-save" type="button" ' +
                        'data-chat-card-note-save hidden>Save note</button></div>';
                infoHost.innerHTML = html;
                var ta = infoHost.querySelector('[data-chat-card-note]');
                var saveBtn = infoHost.querySelector('[data-chat-card-note-save]');
                if (ta && saveBtn) {
                    ta.addEventListener('input', function () { saveBtn.hidden = false; });
                    saveBtn.addEventListener('click', function () {
                        postJSON('/api/chat/user/' + encodeURIComponent(name) + '/note',
                                 { note: ta.value }).then(function (r2) {
                            if (r2.ok) {
                                saveBtn.hidden = true;
                                if (typeof showToast === 'function') showToast('Note saved', 'success');
                            } else if (typeof showToast === 'function') {
                                showToast('Could not save note', 'error');
                            }
                        });
                    });
                }
            }
        });
    }

    function _fmtBytes(n) {
        n = Number(n) || 0;
        if (n >= 1024 * 1024 * 1024) return (n / (1024 * 1024 * 1024)).toFixed(1) + ' GB';
        if (n >= 1024 * 1024) return (n / (1024 * 1024)).toFixed(1) + ' MB';
        if (n >= 1024) return (n / 1024).toFixed(0) + ' KB';
        return n + ' B';
    }

    function closeUserCard() {
        var overlay = q('[data-chat-user-card]');
        if (overlay) overlay.hidden = true;
    }

    // ── share browser: a peer's files, downloadable in place ─────────────────
    var _browse = { user: null, dirs: [], dir: null, files: [] };

    function _fmtSize(bytes) {
        if (!bytes) return '';
        if (bytes < 1048576) return (bytes / 1024).toFixed(0) + ' KB';
        if (bytes < 1073741824) return (bytes / 1048576).toFixed(1) + ' MB';
        return (bytes / 1073741824).toFixed(2) + ' GB';
    }

    function _baseName(path) {
        var parts = String(path || '').split(/[\\/]/);
        return parts[parts.length - 1] || path;
    }

    function openBrowse(name) {
        if (!name) return;
        closeUserCard();
        var overlay = q('[data-chat-browse-modal]');
        if (!overlay) return;
        _browse = { user: name, dirs: [], dir: null, files: [] };
        overlay.hidden = false;
        var title = q('[data-chat-browse-title]');
        if (title) title.textContent = name + '’s files';
        var inp = q('[data-chat-browse-search]');
        if (inp) { inp.value = ''; inp.placeholder = 'Filter folders…'; }
        _browseChrome();
        var body = q('[data-chat-browse-body]');
        if (body) body.innerHTML = '<div class="chat-gif-hint">Browsing ' + esc(name) + '’s shares…</div>';
        getJSON('/api/chat/user/' + encodeURIComponent(name) + '/shares').then(function (res) {
            if (_browse.user !== name) return;
            if (!res.ok) {
                if (body) {
                    body.innerHTML = '<div class="chat-gif-hint">' +
                        esc(res.body && res.body.error || 'Could not browse') + '</div>' +
                        '<div class="chat-browse-retry-row">' +
                        '<button type="button" class="modal-button modal-button--primary" ' +
                            'data-chat-browse-retry>Try again</button></div>';
                }
                return;
            }
            _browse.dirs = res.body.directories || [];
            renderBrowseDirs('');
        });
    }

    function _browseChrome() {
        var back = q('[data-chat-browse-back]');
        var dl = q('[data-chat-browse-dl]');
        var inp = q('[data-chat-browse-search]');
        var inFiles = _browse.dir != null;
        if (back) back.hidden = !inFiles;
        if (dl) dl.hidden = !inFiles;
        if (inp) inp.placeholder = inFiles ? 'Filter files…' : 'Filter folders…';
    }

    function renderBrowseDirs(filter) {
        var body = q('[data-chat-browse-body]');
        if (!body) return;
        _browse.dir = null; _browse.files = [];
        _browseChrome();
        var f = String(filter || '').toLowerCase();
        var dirs = _browse.dirs.filter(function (d) {
            return !f || d.name.toLowerCase().indexOf(f) > -1;
        }).slice(0, 400);
        if (!dirs.length) {
            body.innerHTML = '<div class="chat-gif-hint">' +
                (_browse.dirs.length ? 'No folders match' : 'Nothing shared') + '</div>';
            return;
        }
        body.innerHTML = dirs.map(function (d) {
            return '<button type="button" class="chat-browse-row" data-chat-browse-dir="' +
                attr(d.name) + '" title="' + attr(d.name) + '">' +
                '<span class="chat-browse-icon">📁</span>' +
                '<span class="chat-browse-name">' + esc(_baseName(d.name)) + '</span>' +
                '<span class="chat-browse-meta">' + d.file_count + ' file' +
                    (d.file_count === 1 ? '' : 's') + '</span></button>';
        }).join('');
    }

    function openBrowseDir(dirName) {
        var body = q('[data-chat-browse-body]');
        if (!body) return;
        _browse.dir = dirName;
        _browseChrome();
        body.innerHTML = '<div class="chat-gif-hint">Loading files…</div>';
        var name = _browse.user;
        getJSON('/api/chat/user/' + encodeURIComponent(name) + '/shares/files?dir=' +
                encodeURIComponent(dirName)).then(function (res) {
            if (_browse.user !== name || _browse.dir !== dirName) return;
            if (!res.ok) {
                body.innerHTML = '<div class="chat-gif-hint">' +
                    esc(res.body && res.body.error || 'Could not read that folder') + '</div>';
                return;
            }
            _browse.files = res.body.files || [];
            renderBrowseFiles('');
        });
    }

    function renderBrowseFiles(filter) {
        var body = q('[data-chat-browse-body]');
        if (!body) return;
        var f = String(filter || '').toLowerCase();
        var files = _browse.files.filter(function (x) {
            return !f || x.filename.toLowerCase().indexOf(f) > -1;
        }).slice(0, 500);
        if (!files.length) {
            body.innerHTML = '<div class="chat-gif-hint">No files here</div>';
            return;
        }
        body.innerHTML =
            '<label class="chat-browse-row chat-browse-row--all">' +
                '<input type="checkbox" data-chat-browse-all checked>' +
                '<span class="chat-browse-name">Select all (' + files.length + ')</span>' +
            '</label>' +
            files.map(function (x, i) {
                return '<label class="chat-browse-row">' +
                    '<input type="checkbox" data-chat-browse-file="' + i + '" checked>' +
                    '<span class="chat-browse-name" title="' + attr(x.filename) + '">' +
                        esc(_baseName(x.filename)) + '</span>' +
                    '<span class="chat-browse-meta">' + _fmtSize(x.size) + '</span></label>';
            }).join('');
        body._files = files;
    }

    function browseDownloadSelected() {
        var body = q('[data-chat-browse-body]');
        var dl = q('[data-chat-browse-dl]');
        if (!body || !body._files) return;
        var picked = [];
        body.querySelectorAll('[data-chat-browse-file]').forEach(function (cb) {
            if (cb.checked) {
                var x = body._files[Number(cb.getAttribute('data-chat-browse-file'))];
                if (x) picked.push({ filename: x.filename, size: x.size });
            }
        });
        if (!picked.length) {
            if (typeof showToast === 'function') showToast('Nothing selected', 'info');
            return;
        }
        if (dl) { dl.disabled = true; dl.textContent = 'Queueing…'; }
        postJSON('/api/chat/user/' + encodeURIComponent(_browse.user) + '/download',
                 { files: picked }).then(function (res) {
            if (dl) { dl.disabled = false; dl.textContent = 'Download selected'; }
            if (!res.ok) {
                if (typeof showToast === 'function') {
                    showToast(res.body && res.body.error || 'Could not queue downloads', 'error');
                }
                return;
            }
            var n = res.body.queued || 0;
            if (typeof showToast === 'function') {
                showToast('Queued ' + n + ' file' + (n === 1 ? '' : 's') + ' from ' +
                          _browse.user + ' — check Downloads', 'success');
            }
        });
    }

    // ── @mention autocomplete ────────────────────────────────────────────────
    function _mentionQuery(input) {
        var upto = input.value.slice(0, input.selectionStart || input.value.length);
        var m = upto.match(/(^|\s)@([A-Za-z0-9_.-]*)$/);
        return m ? m[2] : null;
    }

    function updateMentionPop(input) {
        var pop = q('[data-chat-mention-pop]');
        if (!pop) return;
        var qstr = state.view === 'room' ? _mentionQuery(input) : null;
        if (qstr === null || !state.users.length) { pop.hidden = true; return; }
        var ql = qstr.toLowerCase();
        var hits = state.users.filter(function (u) {
            return u.toLowerCase().indexOf(ql) === 0 && u !== state.selfName;
        }).slice(0, 8);
        if (!hits.length) { pop.hidden = true; return; }
        pop.innerHTML = hits.map(function (u) {
            return '<button type="button" class="chat-mention-opt" data-chat-mention-pick="' +
                attr(u) + '">' + _avatar(u) + '<span>' + esc(u) + '</span></button>';
        }).join('');
        pop.hidden = false;
    }

    // ── slash commands (room only — power-user glue over existing features)
    var SLASH_COMMANDS = [
        { c: '/play',  a: '<song or link>', d: 'queue it on the jukebox' },
        { c: '/skip',  a: '', d: 'vote to skip the current track' },
        { c: '/tune',  a: '', d: 'tune in or out of the jukebox' },
        { c: '/topic', a: '<text>', d: 'set the room topic' },
        { c: '/poll',  a: '<question>', d: 'start a room poll' },
        { c: '/pin',   a: '', d: 'pin the latest message' },
        { c: '/gif',   a: '<search>', d: 'find a GIF' },
        { c: '/shrug', a: '[message]', d: 'appends \u00af\\_(\u30c4)_/\u00af' },
    ];

    function updateSlashPop(input) {
        var pop = q('[data-chat-mention-pop]');
        if (!pop) return;
        var v = String(input.value || '');
        var active = state.view === 'room' && state.canSend &&
            v[0] === '/' && v.length <= 12 && !/\s/.test(v);
        if (!active) {
            // only clear the pop when WE own it (mentions share the host)
            if (pop.querySelector('[data-chat-slash-pick]')) { pop.hidden = true; pop.innerHTML = ''; }
            return;
        }
        var hits = SLASH_COMMANDS.filter(function (sc) { return sc.c.indexOf(v) === 0; });
        if (!hits.length) { pop.hidden = true; return; }
        pop.innerHTML = hits.map(function (sc) {
            return '<button type="button" class="chat-mention-opt chat-slash-opt" ' +
                'data-chat-slash-pick="' + attr(sc.c) + '">' +
                '<span class="chat-slash-cmd">' + esc(sc.c) +
                    (sc.a ? ' <i>' + esc(sc.a) + '</i>' : '') + '</span>' +
                '<span class="chat-slash-desc">' + esc(sc.d) + '</span></button>';
        }).join('');
        pop.hidden = false;
    }

    function pickSlash(cmd) {
        var input = q('[data-chat-input]');
        var pop = q('[data-chat-mention-pop]');
        if (pop) { pop.hidden = true; pop.innerHTML = ''; }
        if (!input || !cmd) return;
        var meta = null;
        SLASH_COMMANDS.forEach(function (sc) { if (sc.c === cmd) meta = sc; });
        if (meta && !meta.a) {                     // no-arg commands run on click
            input.value = '';
            _runSlash(cmd);
            return;
        }
        input.value = cmd + ' ';
        input.focus();
    }

    function _runSlash(text) {
        // true = handled; a string = transformed message text; false = not a command
        var m = text.match(/^\/([a-z]+)\s*([\s\S]*)$/);
        if (!m) return false;
        var cmd = m[1], arg = (m[2] || '').trim();
        var toast = function (msg, kind) {
            if (typeof showToast === 'function') showToast(msg, kind || 'info');
        };
        if (cmd === 'shrug') {
            return (arg ? arg + ' ' : '') + '\u00af\\_(\u30c4)_/\u00af';
        }
        if (cmd === 'skip') {
            var stS = _jbxState();
            if (stS.now) sendProtocol('jbx.skip', { o: stS.now.id });
            else toast('Nothing is playing');
            return true;
        }
        if (cmd === 'tune') {
            if (state.jukebox.tunedIn) { _jbxTuneOut(); renderJukebox(); }
            else if (_jbxState().now) { if (!state.jukebox.open) toggleJukebox(); _jbxTuneIn(); }
            else toast('Nothing is playing');
            return true;
        }
        if (cmd === 'topic') {
            sendProtocol('topic.set', { t: arg });
            return true;
        }
        if (cmd === 'play') {
            if (!arg) {
                var hb = q('[data-chat-jbx-input]');
                if (hb) hb.focus();
                return true;
            }
            postJSON('/api/chat/jukebox/resolve', { q: arg }).then(function (res) {
                var r0 = res.ok && res.body.results && res.body.results[0];
                if (r0) _jbxPick(r0);
                else toast((res.body && res.body.error) || 'Nothing found for that', 'error');
            });
            return true;
        }
        if (cmd === 'poll') {
            togglePollPop();
            var qEl = q('[data-chat-poll-q]');
            if (qEl && arg) { qEl.value = arg.slice(0, 160); }
            var o1 = q('[data-chat-poll-o1]');
            if (o1 && arg) o1.focus();
            return true;
        }
        if (cmd === 'pin') {
            var last = (state.msgs || [])[state.msgs.length - 1];
            if (last && last.username) {
                sendProtocol('pin.add', { u: last.username, ts: String(last.timestamp || ''),
                                          x: String(last.message || '').slice(0, 140) });
                toast('\ud83d\udccc Pinned for the room', 'success');
            } else toast('Nothing to pin yet');
            return true;
        }
        if (cmd === 'gif') {
            toggleGifPicker();
            var gs = q('[data-chat-gif-search]');
            if (gs) {
                gs.value = arg;
                gs.focus();
                if (arg) gs.dispatchEvent(new Event('input', { bubbles: true }));
            }
            return true;
        }
        return false;                              // unknown /word → plain message
    }

    function pickMention(name) {
        var input = q('[data-chat-input]');
        var pop = q('[data-chat-mention-pop]');
        if (pop) pop.hidden = true;
        if (!input || !name) return;
        var caret = input.selectionStart || input.value.length;
        var upto = input.value.slice(0, caret);
        var rest = input.value.slice(caret);
        // usernames with spaces can't ride the @grammar — mention the safe prefix
        var safe = name.split(/\s/)[0];
        var replaced = upto.replace(/(^|\s)@[A-Za-z0-9_.-]*$/, '$1@' + safe + ' ');
        input.value = replaced + rest;
        input.focus();
        input.setSelectionRange(replaced.length, replaced.length);
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
            el = q('[data-chat-set-filepost]');
            if (el) { el.value = ''; el.placeholder = b.filepost_key_set ? '••••••••  (configured)' : 'not set'; }
            el = q('[data-chat-set-filepost-expiry]'); if (el) el.value = b.filepost_expiry || '';
            el = q('[data-chat-set-autojoin]'); if (el) el.checked = !!b.auto_join;
            el = q('[data-chat-set-membersend]'); if (el) el.checked = !!b.member_send;
            el = q('[data-chat-set-autoprove]'); if (el) el.checked = !!b.auto_prove;
            // ping is a LOCAL preference (this browser only) — not server state
            el = q('[data-chat-set-ping]');
            if (el) {
                var pOn = false;
                try { pOn = localStorage.getItem('chat_ping') === '1'; } catch (err) { /* ignore */ }
                el.checked = pOn;
            }
            el = q('[data-chat-set-np]');
            if (el) {
                var nOn = false;
                try { nOn = localStorage.getItem('chat_np') === '1'; } catch (err) { /* ignore */ }
                el.checked = nOn;
            }
            // server copy wins on open — it's the one that followed the account
            if (typeof b.avatar !== 'undefined') {
                try { localStorage.setItem('chat_avatar', String(_avatarId(b.avatar))); } catch (err) { /* ignore */ }
            }
            renderAvatarPicker();
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
        var fEl = q('[data-chat-set-filepost]');
        if (fEl && fEl.value.trim()) payload.filepost_key = fEl.value.trim();
        var xEl = q('[data-chat-set-filepost-expiry]');
        if (xEl) payload.filepost_expiry = xEl.value || '';
        // local-only: the mention ping never leaves this browser
        var pEl = q('[data-chat-set-ping]');
        if (pEl) {
            try { localStorage.setItem('chat_ping', pEl.checked ? '1' : '0'); } catch (err) { /* ignore */ }
        }
        var nEl = q('[data-chat-set-np]');
        if (nEl) {
            try { localStorage.setItem('chat_np', nEl.checked ? '1' : '0'); } catch (err) { /* ignore */ }
            // Turning it OFF must retract what the room already sees.
            if (!nEl.checked && state.canSend && state.view === 'room') {
                try { sendProtocol('np.set', {}); } catch (err) { /* not in a room */ }
            }
        }
        postJSON('/api/chat/settings', payload).then(function (res) {
            if (!res.ok) {
                if (typeof showToast === 'function') {
                    showToast(res.body && res.body.error || 'Settings not saved', 'error');
                }
                return;
            }
            if (overlay) overlay.hidden = true;
            // a home-room rename moves the active view with it when the home
            // room WAS the active room; an extra room stays put
            var wasHome = state.room === state.homeRoom;
            state.homeRoom = res.body.room || state.homeRoom;
            if (wasHome) state.room = state.homeRoom;
            state.lastStamp = null;
            loadRooms();
            renderHead();
            refresh();
            if (typeof showToast === 'function') showToast('Chat settings saved', 'success');
        });
    }

    // ── file sharing (filepost.dev) ─────────────────────────────────────
    function toggleAttachPanel(forceClose) {
        var pop = q('[data-chat-attach-pop]');
        if (!pop) return;
        if (forceClose === true) { pop.hidden = true; return; }
        pop.hidden = !pop.hidden;
        if (!pop.hidden) {
            toggleGifPicker(true); toggleEmojiPicker(true);
            var inp = q('[data-chat-attach-search]');
            if (inp) inp.focus();
        }
    }

    function _attachStatus(text, isError) {
        var el = q('[data-chat-attach-status]');
        if (!el) return;
        el.hidden = !text;
        el.textContent = text || '';
        el.classList.toggle('chat-attach-status--err', !!isError);
    }

    function _sendFileMessage(meta) {
        var url = String(meta.url || '');
        if (!url) return;
        var done = function (res) {
            _attachStatus('');
            toggleAttachPanel(true);
            if (res.ok) refresh();
            else if (typeof showToast === 'function') {
                showToast(res.body && res.body.error || 'Could not send the file link', 'error');
            }
        };
        if (state.view === 'room') {
            postJSON('/api/chat/room/message', {
                message: url, room: state.room,
                file: { n: meta.name || 'file', s: meta.size || 0, m: meta.mime || '' },
            }).then(done);
        } else if (state.pmUser) {
            // PMs are plaintext by design — the recipient gets a usable URL
            postJSON('/api/chat/conversations/' + encodeURIComponent(state.pmUser),
                     { message: url }).then(done);
        }
    }

    function attachUploadFile(file) {
        if (!file) return;
        if (file.size > 50 * 1024 * 1024) {
            _attachStatus('Too big — filepost.dev caps uploads at 50 MB', true);
            return;
        }
        _attachStatus('Uploading ' + file.name + '\u2026');
        var fd = new FormData();
        fd.append('file', file, file.name);
        fetch('/api/chat/files/upload', { method: 'POST', body: fd })
            .then(function (r) { return r.json().then(function (b) { return { ok: r.ok, body: b }; }); })
            .then(function (res) {
                if (!res.ok || !res.body.ok) {
                    _attachStatus(res.body && res.body.error || 'Upload failed', true);
                    return;
                }
                _sendFileMessage(res.body);
            })
            .catch(function () { _attachStatus('Upload failed', true); });
    }

    function attachSendTrack(trackId, label) {
        _attachStatus('Uploading ' + (label || 'track') + '\u2026');
        postJSON('/api/chat/files/upload', { track_id: trackId }).then(function (res) {
            if (!res.ok || !res.body.ok) {
                _attachStatus(res.body && res.body.error || 'Upload failed', true);
                return;
            }
            _sendFileMessage(res.body);
        });
    }

    var _attachSearchTimer = null;
    function attachLibrarySearch(qstr) {
        var host = q('[data-chat-attach-results]');
        if (!host) return;
        if (!qstr || qstr.length < 2) { host.innerHTML = ''; return; }
        getJSON('/api/chat/files/library-search?q=' + encodeURIComponent(qstr))
            .then(function (res) {
                if (!res.ok) return;
                var tracks = res.body.tracks || [];
                host.innerHTML = tracks.length ? tracks.map(function (t) {
                    var label = (t.artist ? t.artist + ' — ' : '') + t.title;
                    return '<button type="button" class="chat-browse-row" ' +
                        'data-chat-attach-track="' + attr(String(t.id)) + '" ' +
                        'data-chat-attach-label="' + attr(label) + '">' +
                        '<span class="chat-browse-icon">🎵</span>' +
                        '<span class="chat-browse-name">' + esc(label) + '</span>' +
                        (t.size ? '<span class="chat-browse-meta">' + esc(_fmtBytes(t.size)) + '</span>' : '') +
                        '</button>';
                }).join('') : '<div class="chat-side-none">No matches with files on disk</div>';
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
        postJSON('/api/chat/room/message', { message: url, room: state.room || '' }).then(function (res) {
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

    // Auto-join is off: the user left the room and stays out until THEY say
    // otherwise. Join flips the setting back on; the next poll joins + renders.
    function renderJoinGate() {
        renderHead();
        var comp = q('[data-chat-composer]');
        if (comp) comp.hidden = true;
        var host = q('[data-chat-messages]');
        if (host && !host.querySelector('[data-chat-join-gate]')) {
            host.innerHTML =
                '<div class="chat-problem" data-chat-join-gate>' +
                    'You’ve left the ' + esc(state.room || 'SoulSync') + ' room.' +
                    '<div style="margin-top:10px;">' +
                        '<button class="chat-join-btn" type="button" data-chat-join>Join room</button>' +
                    '</div>' +
                '</div>';
            var btn = host.querySelector('[data-chat-join]');
            if (btn) btn.addEventListener('click', function () {
                btn.disabled = true;
                postJSON('/api/chat/settings', { auto_join: true }).then(function (res) {
                    if (!res.ok) {
                        btn.disabled = false;
                        if (typeof showToast === 'function') showToast('Could not join the room', 'error');
                        return;
                    }
                    state.msgs = [];
                    refresh();
                });
            });
        }
        renderUsers(null);
    }

    // ── room message store (archive pages + live tail) ───────────────────────
    function _msgKey(m) {
        return (m.username || '') + '|' + (m.timestamp || '') + '|' + (m.message || '');
    }

    // ── preset avatars ─────────────────────────────────────────────────────
    // webui/static/avatar/1.png .. N.png. The id is an INDEX into that fixed
    // set and is bounds-checked everywhere it crosses the wire — it must never
    // be interpolated into a path. Unknown/absent falls back to initials, so a
    // missing file or an old client never renders broken.
    var CHAT_AVATARS = 100;
    // Avatars only their owner may wear (id -> slskd username, casefolded).
    // Hidden from everyone else's picker AND refused at render, because the
    // envelope is client-controlled — otherwise anyone could forge the id and
    // wear someone else's face. Mirrored in api/chat.py (RESERVED_AVATARS).
    var RESERVED_AVATARS = { 100: 'boulderbadgedad' };

    function _avatarId(raw) {
        var n = parseInt(raw, 10);
        return (n >= 1 && n <= CHAT_AVATARS) ? n : 0;      // 0 = none
    }

    function _avatarAllowed(id, username) {
        var owner = RESERVED_AVATARS[_avatarId(id)];
        if (!owner) return true;
        return String(username || '').trim().toLowerCase() === owner;
    }

    function _myAvatar() {
        try { return _avatarId(localStorage.getItem('chat_avatar')); } catch (e) { return 0; }
    }

    // username -> avatar id, from the hello beacons AND from anything they've
    // said (messages carry the id, so history alone is enough to paint faces).
    function _avatarMap() {
        var out = {};
        if (window.ChatProtocol && window.ChatProtocol.reduceAvatars) {
            out = window.ChatProtocol.reduceAvatars(_roomEvents(), CHAT_AVATARS);
        }
        (state.msgs || []).forEach(function (m) {
            var n = _avatarId(m && m.av);
            if (n && typeof m.username === 'string') out[m.username] = n;
        });
        if (state.selfName && _myAvatar()) out[state.selfName] = _myAvatar();
        // Drop any reserved avatar claimed by someone who doesn't own it —
        // they fall back to initials rather than wearing another user's face.
        Object.keys(out).forEach(function (u) {
            if (!_avatarAllowed(out[u], u)) delete out[u];
        });
        return out;
    }

    // The avatar element for a user: the chosen picture, else initials.
    function _avatarHtml(name, avId, extraClass) {
        var initials = String(name || '?').replace(/[^A-Za-z0-9]/g, '').slice(0, 2).toUpperCase() || '?';
        var cls = 'chat-av' + (extraClass ? ' ' + extraClass : '');
        var n = _avatarId(avId);
        if (n) {
            return '<span class="' + cls + ' chat-av--img">' +
                '<img src="/static/avatar/' + n + '.png" alt="" loading="lazy" ' +
                    'onerror="this.parentElement.classList.remove(\'chat-av--img\');' +
                    'this.parentElement.textContent=' + attr(JSON.stringify(initials)) + ';">' +
            '</span>';
        }
        return '<span class="' + cls + '">' + esc(initials) + '</span>';
    }

    function renderAvatarPicker() {
        var host = q('[data-chat-avpicker]');
        if (!host) return;
        // Reserved avatars are gated on our slskd name, so if it hasn't loaded
        // yet, fetch it and repaint — otherwise the owner's own avatar would be
        // hidden from them on a cold open.
        if (!state.selfName) {
            getJSON('/api/chat/status').then(function (res) {
                if (res.ok && res.body && res.body.username) {
                    state.selfName = String(res.body.username);
                    renderAvatarPicker();
                }
            });
        }
        var cur = _myAvatar();
        var cells = ['<button type="button" class="chat-avpick' + (cur ? '' : ' chat-avpick--on') +
            ' chat-avpick--none" data-chat-avpick="0" title="No avatar (use initials)">&times;</button>'];
        for (var i = 1; i <= CHAT_AVATARS; i++) {
            // reserved avatars only appear for the account they belong to
            if (!_avatarAllowed(i, state.selfName)) continue;
            cells.push('<button type="button" class="chat-avpick' + (i === cur ? ' chat-avpick--on' : '') +
                '" data-chat-avpick="' + i + '" title="Avatar ' + i + '">' +
                // lazy so opening settings doesn't pull them all at once
                '<img src="/static/avatar/' + i + '.png" alt="" loading="lazy"></button>');
        }
        host.innerHTML = cells.join('');
    }

    function pickAvatar(raw) {
        var n = _avatarId(raw);                    // 0 clears
        // localStorage is the fast local cache every send reads; the server copy
        // is the source of truth so the choice follows the account to another
        // browser. Write both — the local one first so nothing waits on a fetch.
        try { localStorage.setItem('chat_avatar', String(n)); } catch (e) { /* private mode */ }
        postJSON('/api/chat/settings', { avatar: n }).catch(function () { /* local still applies */ });
        renderAvatarPicker();
        // Announce it now so the room repaints without waiting for us to talk.
        if (state.canSend && state.view === 'room') {
            try { sendProtocol('hello', n ? { av: n } : {}); } catch (e) { /* not in a room */ }
        }
        state.lastRendered = '';
        renderUsersList();
    }

    // ── now-playing sharing (opt-in) ───────────────────────────────────────
    // The media player calls this whenever the local track changes; we relay it
    // to the room as np.set so the member list can show what everyone's on.
    // OFF by default and gated behind chat_np — this is a PUBLIC Soulseek room,
    // and what you listen to is nobody's business unless you say so.
    var _npLast = '';
    var _npLastAt = 0;

    function _npEnabled() {
        try { return localStorage.getItem('chat_np') === '1'; } catch (e) { return false; }
    }

    window.__ssNowPlaying = function (track) {
        if (!_npEnabled() || !state.canSend || state.view !== 'room') return;
        var t = String((track && (track.title || track.name)) || '').slice(0, 120);
        var a = String((track && track.artist) || '').slice(0, 80);
        var sig = t + ' | ' + a;
        if (sig === _npLast) return;                       // same track, no chatter
        if (t && Date.now() - _npLastAt < 5000) return;    // rapid skipping: don't spam
        _npLast = sig;
        _npLastAt = Date.now();
        sendProtocol('np.set', t ? { t: t, a: a } : {});    // empty payload = stopped
    };

    // ── mention/reply ping (opt-in) ────────────────────────────────────────
    // Fires only for someone ELSE @-mentioning us or replying to one of our
    // messages. Never our own text, throttled so a burst can't machine-gun,
    // and silent until the user turns it on (chat_ping localStorage).
    var _lastPingAt = 0;

    function _pingWorthy(m) {
        // Armed only AFTER the first merge for a room: opening a room (and
        // paging scrollback) replays the archive through here, and every old
        // mention would fire a ping.
        if (!state.pingArmed || state.loadingOlder) return false;
        if (!m || !state.selfName) return false;
        if (m.username === state.selfName || m.self === true || m.direction === 'Out') return false;
        if (mentionsMe(m.message)) return true;
        return !!(m.reply && m.reply.u && m.reply.u === state.selfName);
    }

    function _chatPing() {
        var on = false;
        try { on = localStorage.getItem('chat_ping') === '1'; } catch (e) { /* private mode */ }
        if (!on) return;
        var now = Date.now();
        if (now - _lastPingAt < 4000) return;         // one ping per burst
        _lastPingAt = now;
        // Synthesized two-tone blip — no asset to ship, no autoplay policy fight
        // (the user has already interacted with the page by the time this fires).
        try {
            var Ctx = window.AudioContext || window.webkitAudioContext;
            if (!Ctx) return;
            var ctx = _chatPing._ctx || (_chatPing._ctx = new Ctx());
            if (ctx.state === 'suspended' && ctx.resume) ctx.resume();
            [[880, 0], [1245, 0.09]].forEach(function (pair) {
                var osc = ctx.createOscillator(), gain = ctx.createGain();
                osc.type = 'sine';
                osc.frequency.value = pair[0];
                var t0 = ctx.currentTime + pair[1];
                gain.gain.setValueAtTime(0.0001, t0);
                gain.gain.exponentialRampToValueAtTime(0.12, t0 + 0.012);
                gain.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.16);
                osc.connect(gain); gain.connect(ctx.destination);
                osc.start(t0); osc.stop(t0 + 0.18);
            });
        } catch (e) { /* audio unavailable — stay silent */ }
    }

    function mergeMessages(incoming) {
        var byKey = {};
        state.msgs.forEach(function (m) { byKey[_msgKey(m)] = m; });
        var added = 0, reactionsChanged = false;
        (incoming || []).forEach(function (m) {
            var k = _msgKey(m);
            var existing = byKey[k];
            if (!existing) {
                byKey[k] = m; state.msgs.push(m); added++;
                if (_pingWorthy(m)) _chatPing();
            } else {
                // Reactions are server-side aggregate state that changes over a
                // message's life. mergeMessages used to only ADD new messages,
                // so a reaction added after we first saw a message never showed
                // without a full page reload. Reconcile the authoritative server
                // reactions onto the copy we already hold, then re-assert our own
                // just-sent reaction until slskd echoes it (avoids a flicker
                // where the optimistic chip vanishes then returns).
                var was = JSON.stringify(existing.reactions || []);
                existing.reactions = m.reactions || [];
                _reapplyPendingReactions(existing);
                if (JSON.stringify(existing.reactions || []) !== was) reactionsChanged = true;
            }
        });
        if (added) {
            state.msgs.sort(function (a, b) {
                return String(a.timestamp || '').localeCompare(String(b.timestamp || ''));
            });
        }
        state.pingArmed = true;   // the archive is in; from here on, pings are real
        // renderMessages skips a repaint when the newest-timestamp+count is
        // unchanged — a reaction change moves neither, so force the repaint.
        if (reactionsChanged) state.lastStamp = null;
        return added;
    }

    // Add `me` to a message's reaction chip for `emoji` (creating it if new).
    function _addReactor(m, emoji, me) {
        m.reactions = m.reactions || [];
        var chip = null;
        m.reactions.forEach(function (r) { if (r.e === emoji) chip = r; });
        if (!chip) {
            m.reactions.push({ e: emoji, n: 1, users: me ? [me] : [] });
        } else if (me && (chip.users || []).indexOf(me) === -1) {
            chip.users = (chip.users || []).concat([me]);
            chip.n = (chip.n || 0) + 1;
        }
    }

    // Re-assert self-reactions the server hasn't echoed yet; drop each from the
    // pending set once the authoritative copy contains it.
    function _reapplyPendingReactions(m) {
        var me = state.selfName;
        if (!me) return;
        var k = _msgKey(m);
        Object.keys(state.pendingReactions).forEach(function (pk) {
            var sep = pk.lastIndexOf('|');
            if (pk.slice(0, sep) !== k) return;
            var emoji = pk.slice(sep + 1);
            var chip = null;
            (m.reactions || []).forEach(function (r) { if (r.e === emoji) chip = r; });
            if (chip && (chip.users || []).indexOf(me) > -1) {
                delete state.pendingReactions[pk];      // server confirmed it
            } else {
                _addReactor(m, emoji, me);              // keep it visible meanwhile
            }
        });
    }

    function loadOlder() {
        if (state.view !== 'room' || state.loadingOlder || state.historyDone || !state.msgs.length) return;
        state.loadingOlder = true;
        var oldest = String(state.msgs[0].timestamp || '');
        getJSON('/api/chat/room/history?room=' + encodeURIComponent(state.room || '') +
                '&before=' + encodeURIComponent(oldest) + '&limit=100')
            .then(function (res) {
                state.loadingOlder = false;
                if (!res.ok) return;
                if (res.body.done) state.historyDone = true;
                var older = res.body.messages || [];
                if (!older.length) return;
                mergeMessages(older);
                // re-render, keeping the reader anchored where they were
                var host = q('[data-chat-messages]');
                var prevH = host ? host.scrollHeight : 0;
                var prevTop = host ? host.scrollTop : 0;
                state.lastStamp = null;
                renderMessages(state.msgs);
                if (host) host.scrollTop = host.scrollHeight - prevH + prevTop;
            })
            .catch(function () { state.loadingOlder = false; });
    }

    // ── archive search (local history — Soulseek has no server-side search) ──
    function enterSearch() {
        state.searchMode = true;
        renderHead();
        var inp = q('[data-chat-search-input]');
        if (inp) { inp.hidden = false; inp.focus(); }
    }

    function exitSearch() {
        if (!state.searchMode) return;
        state.searchMode = false;
        state.lastStamp = null;
        renderHead();
        renderMessages(state.msgs);
        var host = q('[data-chat-messages]');
        if (host) host.scrollTop = host.scrollHeight;
    }

    function runSearch(qstr) {
        qstr = String(qstr || '').trim();
        var host = q('[data-chat-messages]');
        if (!qstr || !host) return;
        host.innerHTML = '<div class="chat-empty">Searching…</div>';
        getJSON('/api/chat/room/search?room=' + encodeURIComponent(state.room || '') +
                '&q=' + encodeURIComponent(qstr)).then(function (res) {
            if (!state.searchMode || !res.ok) return;
            var msgs = (res.body.messages || []).slice().reverse();   // oldest-first for render
            host.innerHTML =
                '<div class="chat-search-banner">' + msgs.length + ' result' +
                    (msgs.length === 1 ? '' : 's') + ' for “' + esc(qstr) + '”' +
                    '<button type="button" class="chat-filter-btn" data-chat-search-exit>Back to live</button>' +
                '</div>' +
                (msgs.length ? renderGroups(msgs)
                             : '<div class="chat-empty">Nothing in the archive matches.</div>');
            host.scrollTop = 0;
        });
    }

    // ── refresh loop ─────────────────────────────────────────────────────────
    function refresh() {
        if (!pageVisible()) return Promise.resolve();
        if (state.searchMode && state.view === 'room') {
            // search results are a frozen snapshot — don't repaint over them;
            // the side rails still refresh below
            return getJSON('/api/chat/conversations').then(function (res) {
                if (res.ok) renderSide(res.body.conversations);
            }).catch(function () { /* next tick retries */ });
        }
        var work;
        if (state.view === 'room') {
            work = getJSON('/api/chat/room?room=' + encodeURIComponent(state.room || '')).then(function (res) {
                if (!res.ok) {
                    renderProblem(res.body && res.body.error
                        ? res.body.error
                        : 'Chat is unavailable right now.');
                    return;
                }
                state.canSend = !!res.body.can_send;
                // auto-join OFF → the server no longer joins for us; show the
                // join gate instead of the room (popwaffle9000's leave fix).
                if (res.body.joined === false) {
                    renderJoinGate();
                    return;
                }
                renderHead(); renderComposer();
                mergeMessages(res.body.messages);
                _clearTypingFor(res.body.messages);
                renderMessages(state.msgs);
                renderUsers(res.body.users);
                _ingestProtocol(res.body.protocol);
                _sendJoinBeacon();
                _jbxWatchdog();   // drive the queue even with the panel closed
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
    function openRoom(name) {
        state.view = 'room'; state.pmUser = null; state.lastStamp = null; state.stickBottom = true;
        state.renderedCount = 0; hideJumpPill();
        var nextRoom = name || state.room || state.homeRoom || 'SoulSync';
        if (state.room && state.room !== nextRoom) {
            _jbxTuneOut();               // BEFORE the flip: the off event goes to the OLD room
            state.jukebox.lastRendered = '';
            state.jukebox.nowSeen = null;   // new room, new event stream, new clock base
            state.pinsOpen = false;
            state.pollDismissedAt = null;
        }
        state.room = nextRoom;
        state.thread = null;         // threads are per-room (and home-room only)
        state.topicEditing = false;
        state.typing = {};
        state.typingArmedAt = Date.now() + 2000;   // archive replay isn't live typing
        state.pingArmed = false;                   // ...and archive mentions aren't new pings
        renderTyping();
        renderBusUI();
        state.msgs = []; state.loadingOlder = false; state.historyDone = false;
        cancelReply();
        try {
            state.newMarker = localStorage.getItem('chat_seen_' + (state.room || '')) || null;
        } catch (e) { state.newMarker = null; }
        renderHead(); renderComposer(); renderSide(null);
        var host = q('[data-chat-messages]');
        if (host) host.innerHTML = '<div class="chat-empty">Loading…</div>';
        refresh();
    }

    function loadRooms() {
        return getJSON('/api/chat/rooms').then(function (res) {
            if (!res.ok) return;
            state.homeRoom = res.body.home || state.homeRoom;
            state.rooms = res.body.rooms || [];
            state.canManage = !!res.body.can_manage;
            renderSide(null);
        });
    }

    // ── room browser (join any public Soulseek room) ─────────────────────────
    var _availRooms = null;

    function openRoomBrowser() {
        var overlay = q('[data-chat-rooms-modal]');
        if (!overlay) return;
        overlay.hidden = false;
        var listEl = q('[data-chat-rooms-list]');
        if (listEl) listEl.innerHTML = '<div class="chat-gif-hint">Loading rooms…</div>';
        var inp = q('[data-chat-rooms-search]');
        if (inp) { inp.value = ''; inp.focus(); }
        getJSON('/api/chat/rooms/available').then(function (res) {
            if (!res.ok) {
                if (listEl) {
                    listEl.innerHTML = '<div class="chat-gif-hint">' +
                        esc(res.body && res.body.error || 'Room list unavailable') + '</div>';
                }
                return;
            }
            _availRooms = { rooms: res.body.rooms || [], joined: res.body.joined || [] };
            renderRoomBrowser('');
        });
    }

    function renderRoomBrowser(filter) {
        var listEl = q('[data-chat-rooms-list]');
        if (!listEl || !_availRooms) return;
        var f = String(filter || '').toLowerCase();
        var joined = {};
        _availRooms.joined.forEach(function (r) { joined[r] = 1; });
        var rooms = _availRooms.rooms.filter(function (r) {
            return !r.private && (!f || r.name.toLowerCase().indexOf(f) > -1);
        }).slice(0, 200);
        if (!rooms.length) {
            listEl.innerHTML = '<div class="chat-gif-hint">No rooms match</div>';
            return;
        }
        listEl.innerHTML = rooms.map(function (r) {
            var isJoined = !!joined[r.name];
            return '<div class="chat-room-row">' +
                '<span class="chat-room-name" title="' + attr(r.name) + '"># ' + esc(r.name) + '</span>' +
                '<span class="chat-room-count">' + r.users + ' online</span>' +
                (isJoined
                    ? '<span class="chat-room-joined">joined</span>'
                    : (state.canManage
                        ? '<button type="button" class="chat-room-join" data-chat-join-room="' +
                            attr(r.name) + '">Join</button>'
                        : '')) +
            '</div>';
        }).join('');
    }

    function joinRoom(name, btn) {
        if (!name) return;
        if (btn) { btn.disabled = true; btn.textContent = 'Joining…'; }
        postJSON('/api/chat/rooms/join', { room: name }).then(function (res) {
            if (!res.ok) {
                if (btn) { btn.disabled = false; btn.textContent = 'Join'; }
                if (typeof showToast === 'function') {
                    showToast(res.body && res.body.error || 'Could not join', 'error');
                }
                return;
            }
            if (_availRooms && _availRooms.joined.indexOf(name) < 0) _availRooms.joined.push(name);
            var overlay = q('[data-chat-rooms-modal]');
            if (overlay) overlay.hidden = true;
            loadRooms().then(function () { openRoom(name); });
            if (typeof showToast === 'function') showToast('Joined # ' + name, 'success');
        });
    }

    function leaveRoom(name) {
        if (!name) return;
        var go = function () {
            postJSON('/api/chat/rooms/leave', { room: name }).then(function (res) {
                if (!res.ok) {
                    if (typeof showToast === 'function') {
                        showToast(res.body && res.body.error || 'Could not leave', 'error');
                    }
                    return;
                }
                if (_availRooms) {
                    _availRooms.joined = _availRooms.joined.filter(function (r) { return r !== name; });
                }
                loadRooms().then(function () {
                    if (state.view === 'room' && state.room === name) openRoom(state.homeRoom);
                });
            });
        };
        if (typeof showConfirmDialog === 'function') {
            showConfirmDialog({
                title: 'Leave Room',
                message: 'Leave # ' + name + '? You can rejoin any time from Browse rooms.',
                confirmText: 'Leave', destructive: false,
            }).then(function (yes) { if (yes) go(); });
        } else { go(); }
    }

    function openPm(username) {
        if (!username) return;
        state.view = 'pm'; state.pmUser = username; state.lastStamp = null; state.stickBottom = true;
        state.searchMode = false;
        state.renderedCount = 0; hideJumpPill(); state.newMarker = null;
        cancelReply();
        state.topicEditing = false;
        renderHead(); renderComposer(); renderBusUI();   // hides the panels (audio keeps playing)
        var host = q('[data-chat-messages]');
        if (host) host.innerHTML = '<div class="chat-empty">Loading…</div>';
        refresh();
    }

    function send() {
        var input = q('[data-chat-input]');
        if (!input) return;
        var text = (input.value || '').trim();
        if (!text || !state.canSend) return;
        state.lastTypSentAt = 0;
        if (state.view === 'room' && text[0] === '/') {
            var slash = _runSlash(text);
            if (slash === true) {
                input.value = '';
                input.style.height = 'auto';
                var spop = q('[data-chat-mention-pop]');
                if (spop) { spop.hidden = true; spop.innerHTML = ''; }
                return;
            }
            if (typeof slash === 'string') text = slash;
        }
        input.value = '';
        input.style.height = 'auto';
        var url = state.view === 'room'
            ? '/api/chat/room/message'
            : '/api/chat/conversations/' + encodeURIComponent(state.pmUser);
        var payload = { message: text };
        if (state.view === 'room') {
            payload.room = state.room || '';
            if (_myAvatar()) payload.avatar = _myAvatar();   // rides the envelope
            // Only the SoulSync room carries channel/thread tags — a message in
            // any other room stays a plain room message.
            if (_chanRoom()) {
                payload.chan = state.channel || CHAT_DEFAULT_CHANNEL;
                if (state.thread) {
                    payload.thread = state.thread.id;
                    payload.thread_name = state.thread.name || '';
                }
            }
        }
        var sentReply = null;
        if (state.view === 'room' && state.replyTo) {
            payload.reply = state.replyTo;
            sentReply = state.replyTo;
        }
        postJSON(url, payload).then(function (res) {
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
                    reply: sentReply || undefined,
                    // room sends ride the envelope → render the echo rich too
                    rich: state.view === 'room',
                }]));
                host.scrollTop = host.scrollHeight;
                state.lastStamp = null;
            }
            state.stickBottom = true;
            cancelReply();
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
            if (!e.target.closest('[data-chat-poll-btn]') &&
                    !e.target.closest('[data-chat-poll-pop]')) {
                togglePollPop(true);
            }
            if (state.pinsOpen && !e.target.closest('[data-chat-pins-toggle]') &&
                    !e.target.closest('[data-chat-pinbar]')) {
                state.pinsOpen = false;
                renderPinbar();
                if (!state.searchMode) renderHead();
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
            t = e.target.closest('[data-chat-file-audio]');
            if (t) {
                var card = t.closest('.chat-file-card');
                var slot = card && card.querySelector('.chat-file-slot');
                if (slot) {
                    slot.innerHTML = '<audio class="chat-file-player" controls preload="none" src="' +
                        t.getAttribute('data-chat-file-audio').replace(/"/g, '&quot;') + '"></audio>';
                    slot.querySelector('audio').play().catch(function () {});
                    t.remove();
                }
                return;
            }
            t = e.target.closest('[data-chat-file-video]');
            if (t) {
                var vcard = t.closest('.chat-file-card');
                var vslot = vcard && vcard.querySelector('.chat-file-slot');
                if (vslot) {
                    vslot.innerHTML = '<video class="chat-file-player chat-file-player--video" controls preload="metadata" src="' +
                        t.getAttribute('data-chat-file-video').replace(/"/g, '&quot;') + '"></video>';
                    t.remove();
                }
                return;
            }
            t = e.target.closest('[data-chat-file-save]');
            if (t) { _saveFileToLibrary(t); return; }
            t = e.target.closest('[data-chat-attach-btn]');
            if (t) { toggleAttachPanel(); return; }
            t = e.target.closest('[data-chat-spoiler]');
            if (t) { t.classList.add('chat-spoiler--shown'); return; }
            t = e.target.closest('[data-chat-fmt]');
            if (t) { applyFormat(t.getAttribute('data-chat-fmt')); return; }
            t = e.target.closest('[data-chat-emoji-btn]');
            if (t) { toggleEmojiPicker(); return; }
            t = e.target.closest('[data-chat-emoji-pick]');
            if (t) { insertAtCursor(t.getAttribute('data-chat-emoji-pick')); toggleEmojiPicker(true); return; }
            t = e.target.closest('[data-chat-reply-user]');
            if (t) {
                startReply(t.getAttribute('data-chat-reply-user'),
                           t.getAttribute('data-chat-reply-x'));
                return;
            }
            t = e.target.closest('[data-chat-reply-cancel]');
            if (t) { cancelReply(); return; }
            t = e.target.closest('[data-chat-slash-pick]');
            if (t) { pickSlash(t.getAttribute('data-chat-slash-pick')); return; }
            t = e.target.closest('[data-chat-mention-pick]');
            if (t) { pickMention(t.getAttribute('data-chat-mention-pick')); return; }
            t = e.target.closest('[data-chat-settings-btn]');
            if (t) { openSettings(); return; }
            t = e.target.closest('[data-chat-avpick]');
            if (t) { pickAvatar(t.getAttribute('data-chat-avpick')); return; }
            // ── Discord shell: channel switch, category collapse, DM puck ──
            t = e.target.closest('[data-chat-thread]');
            if (t) {
                openThread(t.getAttribute('data-chat-thread'),
                           t.getAttribute('data-chat-thread-name'));
                return;
            }
            t = e.target.closest('[data-chat-thread-start]');
            if (t) {
                openThread(t.getAttribute('data-chat-thread-start'),
                           t.getAttribute('data-chat-thread-title') || 'Thread');
                return;
            }
            t = e.target.closest('[data-chat-thread-close]');
            if (t) { closeThread(); return; }
            t = e.target.closest('[data-chat-chan]');
            if (t) { switchChannel(t.getAttribute('data-chat-chan')); return; }
            t = e.target.closest('[data-chat-cat]');
            if (t) {
                var cat = t.getAttribute('data-chat-cat');
                state.chanCatClosed[cat] = !state.chanCatClosed[cat];
                renderChannels();
                return;
            }
            t = e.target.closest('[data-chat-guild-dm]');
            if (t) {
                // Jump to the most recent conversation; otherwise just surface the list.
                var first = (state.convos || [])[0];
                if (first && (first.username || first.name)) openPm(first.username || first.name);
                return;
            }
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
                state.renderedCount = 0; hideJumpPill();   // a filter flip isn't 'new messages'
                renderHead(); refresh();
                return;
            }
            t = e.target.closest('[data-chat-browse-retry]');
            if (t) { if (_browse.user) openBrowse(_browse.user); return; }
            t = e.target.closest('[data-chat-pins-toggle]');
            if (t) {
                state.pinsOpen = !state.pinsOpen;
                renderPinbar();
                if (!state.searchMode) renderHead();
                return;
            }
            t = e.target.closest('[data-chat-pin-del-u]');
            if (t) {
                sendProtocol('pin.del', { u: t.getAttribute('data-chat-pin-del-u'),
                                          ts: t.getAttribute('data-chat-pin-del-ts') });
                return;
            }
            t = e.target.closest('[data-chat-pin-user]');
            if (t) {
                sendProtocol('pin.add', { u: t.getAttribute('data-chat-pin-user'),
                                          ts: t.getAttribute('data-chat-pin-ts'),
                                          x: t.getAttribute('data-chat-pin-x') });
                if (typeof showToast === 'function') showToast('📌 Pinned for the room', 'success');
                return;
            }
            t = e.target.closest('[data-chat-poll-btn]');
            if (t) { togglePollPop(); return; }
            t = e.target.closest('[data-chat-poll-start]');
            if (t) { _pollStart(); return; }
            t = e.target.closest('[data-chat-poll-vote]');
            if (t) { sendProtocol('poll.vote', { o: t.getAttribute('data-chat-poll-vote') }); return; }
            t = e.target.closest('[data-chat-poll-end]');
            if (t) { sendProtocol('poll.end', {}); return; }
            t = e.target.closest('[data-chat-poll-dismiss]');
            if (t) {
                var pd = window.ChatProtocol ? window.ChatProtocol.reducePoll(_roomEvents()) : null;
                state.pollDismissedAt = pd ? pd.at : null;
                renderPoll();
                return;
            }
            t = e.target.closest('[data-chat-topic-edit]');
            if (t) {
                state.topicEditing = true;
                var headEl = q('[data-chat-head]');
                var cur = (window.ChatProtocol ? window.ChatProtocol.reduceTopic(_roomEvents()) : null);
                if (headEl) {
                    headEl.innerHTML = '<span class="chat-head-title"># ' + esc(state.room || '') + '</span>' +
                        '<input class="chat-input chat-topic-in" data-chat-topic-input type="text" maxlength="160" ' +
                        'placeholder="Set a room topic… (Enter to save, Esc to cancel)" autocomplete="off" value="' +
                        attr(cur ? cur.t : '') + '">';
                    var ti = q('[data-chat-topic-input]');
                    if (ti) { ti.focus(); ti.select(); }
                }
                return;
            }
            t = e.target.closest('[data-chat-jukebox-btn]');
            if (t) { toggleJukebox(); return; }
            t = e.target.closest('[data-chat-jbx-tunein]');
            if (t) { _jbxTuneIn(); return; }
            t = e.target.closest('[data-chat-jbx-tuneout]');
            if (t) { _jbxTuneOut(); renderJukebox(); return; }
            t = e.target.closest('[data-chat-jbx-vote]');
            if (t) { sendProtocol('jbx.vote', { o: t.getAttribute('data-chat-jbx-vote') }); return; }
            t = e.target.closest('[data-chat-jbx-pick]');
            if (t) { _jbxPick(state.jukebox.results[parseInt(t.getAttribute('data-chat-jbx-pick'), 10)]); return; }
            t = e.target.closest('[data-chat-jbx-vpick]');
            if (t) {
                _jbxPick(state.jukebox.searchResults[parseInt(t.getAttribute('data-chat-jbx-vpick'), 10)]);
                _closeJbxSearchModal();
                return;
            }
            t = e.target.closest('[data-chat-jbx-searchclose]');
            if (t) { _closeJbxSearchModal(); return; }
            var jbxSearchOv = e.target.closest('[data-chat-jbx-search-modal]');
            if (jbxSearchOv && e.target === jbxSearchOv) { _closeJbxSearchModal(); return; }
            t = e.target.closest('[data-chat-jbx-skip]');
            if (t) { sendProtocol('jbx.skip', { o: t.getAttribute('data-chat-jbx-skip') }); return; }
            t = e.target.closest('[data-chat-jbx-unsub]');
            if (t) { sendProtocol('jbx.unsub', { id: t.getAttribute('data-chat-jbx-unsub') }); return; }
            t = e.target.closest('[data-chat-jbx-resub]');
            if (t) {
                var rp = { id: t.getAttribute('data-chat-jbx-resub'),
                           title: t.getAttribute('data-chat-jbx-resub-ti') || '' };
                var rd = parseInt(t.getAttribute('data-chat-jbx-resub-d') || '', 10);
                if (rd > 0) rp.duration = rd;
                _jbxPick(rp);
                return;
            }
            t = e.target.closest('[data-chat-jbx-hist]');
            if (t) {
                state.jukebox.histOpen = !state.jukebox.histOpen;
                state.jukebox.lastRendered = '';
                renderJukebox();
                return;
            }
            t = e.target.closest('[data-chat-jbx-radio]');
            if (t) {
                var rSt = _jbxState();
                sendProtocol('jbx.radio', { on: rSt.radio ? 0 : 1 });
                if (typeof showToast === 'function') {
                    showToast(rSt.radio ? '📻 Auto-DJ off' : '📻 Auto-DJ on — the queue keeps itself fed', 'info');
                }
                return;
            }
            t = e.target.closest('[data-chat-jbx-video]');
            if (t) {
                state.jukebox.videoHidden = !state.jukebox.videoHidden;
                try { localStorage.setItem('chat_jbx_audio', state.jukebox.videoHidden ? '1' : '0'); } catch (err) { /* ignore */ }
                var ph = q('[data-chat-jbx-player]');
                if (ph) ph.classList.toggle('chat-jbx-player--audio', state.jukebox.videoHidden);
                state.jukebox.lastRendered = '';
                renderJukebox();
                return;
            }
            t = e.target.closest('[data-chat-search-btn]');
            if (t) { state.searchMode ? exitSearch() : enterSearch(); return; }
            t = e.target.closest('[data-chat-search-exit]');
            if (t) { exitSearch(); return; }
            t = e.target.closest('[data-chat-copy]');
            if (t) {
                var txt = t.getAttribute('data-chat-copy') || '';
                try {
                    navigator.clipboard.writeText(txt).then(function () {
                        if (typeof showToast === 'function') showToast('Copied', 'success');
                    });
                } catch (err) { /* clipboard unavailable */ }
                return;
            }
            t = e.target.closest('[data-chat-open-room]');
            if (t) { state.searchMode = false; openRoom(t.getAttribute('data-chat-open-room') || undefined); return; }
            t = e.target.closest('[data-chat-browse-rooms]');
            if (t) { openRoomBrowser(); return; }
            t = e.target.closest('[data-chat-join-room]');
            if (t) { joinRoom(t.getAttribute('data-chat-join-room'), t); return; }
            t = e.target.closest('[data-chat-leave-room]');
            if (t) { leaveRoom(t.getAttribute('data-chat-leave-room')); return; }
            t = e.target.closest('[data-chat-rooms-close]');
            if (t) { var rm = q('[data-chat-rooms-modal]'); if (rm) rm.hidden = true; return; }
            var rmo = e.target.closest('[data-chat-rooms-modal]');
            if (rmo && e.target === rmo) { rmo.hidden = true; return; }
            t = e.target.closest('[data-chat-open-pm]');
            if (t) { openPm(t.getAttribute('data-chat-open-pm')); return; }
            t = e.target.closest('[data-chat-react-user]');
            if (t) {
                showReactRow(t, t.getAttribute('data-chat-react-user'),
                             t.getAttribute('data-chat-react-text'));
                return;
            }
            t = e.target.closest('[data-chat-react-do]');
            if (t) {
                var rowEl = t.closest('[data-chat-react-pick-row]');
                sendReaction(rowEl && rowEl._target, t.getAttribute('data-chat-react-do'));
                return;
            }
            if (!e.target.closest('[data-chat-react-pick-row]')) closeReactRow();
            t = e.target.closest('[data-chat-card-message]');
            if (t) {
                var ov = q('[data-chat-user-card]');
                closeUserCard();
                if (ov) openPm(ov.getAttribute('data-chat-user-card-for'));
                return;
            }
            t = e.target.closest('[data-chat-card-browse]');
            if (t) {
                var bOv = q('[data-chat-user-card]');
                openBrowse(bOv && bOv.getAttribute('data-chat-user-card-for'));
                return;
            }
            t = e.target.closest('[data-chat-browse-dir]');
            if (t) { openBrowseDir(t.getAttribute('data-chat-browse-dir')); return; }
            t = e.target.closest('[data-chat-browse-back]');
            if (t) {
                var bsIn = q('[data-chat-browse-search]');
                if (bsIn) bsIn.value = '';
                renderBrowseDirs('');
                return;
            }
            t = e.target.closest('[data-chat-browse-dl]');
            if (t) { browseDownloadSelected(); return; }
            t = e.target.closest('[data-chat-browse-close]');
            if (t) { var bm = q('[data-chat-browse-modal]'); if (bm) bm.hidden = true; return; }
            var bmo = e.target.closest('[data-chat-browse-modal]');
            if (bmo && e.target === bmo) { bmo.hidden = true; return; }
            t = e.target.closest('[data-chat-browse-all]');
            if (t) {
                var bBody = q('[data-chat-browse-body]');
                if (bBody) {
                    bBody.querySelectorAll('[data-chat-browse-file]').forEach(function (cb) {
                        cb.checked = t.checked;
                    });
                }
                return;
            }
            t = e.target.closest('[data-chat-card-ignore]');
            if (t) {
                var cardOv = q('[data-chat-user-card]');
                toggleIgnored(cardOv && cardOv.getAttribute('data-chat-user-card-for'));
                closeUserCard();
                return;
            }
            t = e.target.closest('[data-chat-card-close]');
            if (t) { closeUserCard(); return; }
            var uc = e.target.closest('[data-chat-user-card]');
            if (uc && e.target === uc) { closeUserCard(); return; }
            t = e.target.closest('[data-chat-user]');
            if (t) { openUserCard(t.getAttribute('data-chat-user')); return; }
        });

        var form = q('[data-chat-composer]');
        if (form) form.addEventListener('submit', function (e) { e.preventDefault(); send(); });
        var jbxForm = q('[data-chat-jbx-form]');
        if (jbxForm) jbxForm.addEventListener('submit', function (e) { e.preventDefault(); _jbxSubmit(); });
        var jbxSearchForm = q('[data-chat-jbx-searchform]');
        if (jbxSearchForm) jbxSearchForm.addEventListener('submit', function (e) { e.preventDefault(); _jbxSearchModalSubmit(); });

        var inputEl = q('[data-chat-input]');
        if (inputEl) {
            // Discord composer: Enter sends, Shift+Enter newlines (the block
            // syntax — code fences, quotes, lists — NEEDS real newlines)
            inputEl.addEventListener('keydown', function (e) {
                if (e.key === 'Enter' && !e.shiftKey) {
                    // inside an unclosed ``` fence Enter newlines (Discord
                    // behavior) — otherwise typing a code block is impossible
                    var fences = (inputEl.value.match(/```/g) || []).length;
                    if (fences % 2 === 1) return;
                    e.preventDefault(); send();
                }
                if (e.key === 'Escape') {
                    cancelReply();
                    var mp = q('[data-chat-mention-pop]');
                    if (mp) mp.hidden = true;
                }
                if (e.key === 'Tab') {
                    var sp = q('[data-chat-mention-pop]');
                    var first = sp && !sp.hidden && sp.querySelector('[data-chat-slash-pick]');
                    if (first) {
                        e.preventDefault();
                        pickSlash(first.getAttribute('data-chat-slash-pick'));
                    }
                }
            });
            inputEl.addEventListener('input', function () {
                inputEl.style.height = 'auto';
                inputEl.style.height = Math.min(inputEl.scrollHeight, 132) + 'px';
                updateMentionPop(inputEl);
                updateSlashPop(inputEl);
                _maybeSendTyping(inputEl);
            });
        }

        // user-list search: delegated ('input' bubbles; the input is re-created
        // only when the whole panel resets, so direct binding would go stale)
        // history-search input is re-created by every renderHead → delegate
        page.addEventListener('keydown', function (e) {
            if (e.target && e.target.matches('[data-chat-search-input]')) {
                if (e.key === 'Enter') { e.preventDefault(); runSearch(e.target.value); }
                if (e.key === 'Escape') exitSearch();
            }
            if (e.target && e.target.matches('[data-chat-topic-input]')) {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    sendProtocol('topic.set', { t: String(e.target.value || '').trim() });
                    state.topicEditing = false;
                    renderHead();
                }
                if (e.key === 'Escape') { state.topicEditing = false; renderHead(); }
            }
        });

        page.addEventListener('input', function (e) {
            if (e.target && e.target.matches('[data-chat-user-search]')) {
                state.userFilter = e.target.value.trim();
                renderUsersList();
            }
            if (e.target && e.target.matches('[data-chat-jbx-vol]')) {
                var vv = parseInt(e.target.value, 10);
                if (vv >= 0 && vv <= 100) {
                    state.jukebox.vol = vv;
                    try { localStorage.setItem('chat_jbx_vol', String(vv)); } catch (err) { /* ignore */ }
                    if (state.jukebox.player && state.jukebox.playerAlive) {
                        try { state.jukebox.player.setVolume(vv); } catch (err) { /* gone */ }
                    }
                }
            }
            if (e.target && e.target.matches('[data-chat-browse-search]')) {
                var v = e.target.value.trim();
                if (_browse.dir != null) renderBrowseFiles(v);
                else renderBrowseDirs(v);
            }
        });

        var roomsIn = q('[data-chat-rooms-search]');
        if (roomsIn) {
            roomsIn.addEventListener('input', function () {
                renderRoomBrowser(roomsIn.value.trim());
            });
        }

        var gifIn = q('[data-chat-gif-search]');
        if (gifIn) {
            gifIn.addEventListener('input', function () {
                if (_gifTimer) clearTimeout(_gifTimer);
                _gifTimer = setTimeout(function () { gifSearch(gifIn.value.trim()); }, 400);
            });
        }

        var attIn = q('[data-chat-attach-search]');
        if (attIn) {
            attIn.addEventListener('input', function () {
                if (_attachSearchTimer) clearTimeout(_attachSearchTimer);
                _attachSearchTimer = setTimeout(function () {
                    attachLibrarySearch(attIn.value.trim());
                }, 350);
            });
        }
        var attFile = q('[data-chat-attach-file]');
        if (attFile) {
            attFile.addEventListener('change', function () {
                if (attFile.files && attFile.files[0]) attachUploadFile(attFile.files[0]);
                attFile.value = '';
            });
        }
        document.addEventListener('click', function (e) {
            var up = e.target.closest('[data-chat-attach-upload]');
            if (up) { var fi = q('[data-chat-attach-file]'); if (fi) fi.click(); return; }
            var tr = e.target.closest('[data-chat-attach-track]');
            if (tr) {
                attachSendTrack(tr.getAttribute('data-chat-attach-track'),
                                tr.getAttribute('data-chat-attach-label'));
            }
        });

        var scroller = q('[data-chat-messages]');
        if (scroller) {
            scroller.addEventListener('scroll', function () {
                state.stickBottom =
                    scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 40;
                if (state.stickBottom) hideJumpPill();
                if (scroller.scrollTop < 60) loadOlder();   // reach the top → page older
            });
        }

        var jump = q('[data-chat-jump]');
        if (jump) {
            jump.addEventListener('click', function () {
                var sc = q('[data-chat-messages]');
                if (sc) sc.scrollTop = sc.scrollHeight;
                state.stickBottom = true;
                hideJumpPill();
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
                state.homeRoom = (res.body && res.body.room) || 'SoulSync';
                state.room = state.room || state.homeRoom;
                state.canSend = !!(res.body && res.body.can_send);
                state.isAdmin = !!(res.body && res.body.is_admin);
                state.selfName = String((res.body && res.body.username) || '');
                renderSide([]); renderHead(); renderComposer();
                loadRooms();
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

    var _selfFetched = false;

    function _ensureSelf() {
        // mention pings must work even if the chat page was never opened this
        // session — one lazy status fetch on the first pushed room message
        if (_selfFetched || state.selfName) return;
        _selfFetched = true;
        getJSON('/api/chat/status').then(function (res) {
            if (res.ok) state.selfName = String(res.body.username || '');
        });
    }

    // ── protocol bus (the hidden coordination channel) ──────────────────
    function _ingestProtocol(events) {
        if (!events || !events.length) return;
        var log = state.protocolLog;
        var room = state.room || '';
        var seen = {};
        log.forEach(function (e) { seen[e.room + '|' + e.username + '|' + e.timestamp + '|' + (e.p && e.p.k)] = 1; });
        var fresh = [];
        events.forEach(function (ev) {
            if (!ev || !ev.p || !window.ChatProtocol) return;
            var p = window.ChatProtocol.parseProtocol({ p: ev.p });
            if (!p) return;
            var key = room + '|' + ev.username + '|' + ev.timestamp + '|' + p.k;
            if (seen[key]) return;
            seen[key] = 1;
            // room-tagged: reducers (jukebox) must never mix rooms' events
            var entry = { username: String(ev.username || ''), timestamp: ev.timestamp,
                          room: room, p: p };
            log.push(entry);
            fresh.push(entry);
            if (p.k === 'typ' && entry.username && entry.username !== state.selfName &&
                    state.typingArmedAt && Date.now() > state.typingArmedAt) {
                state.typing[entry.username] = Date.now();
                renderTyping();
            }
        });
        if (log.length > 300) state.protocolLog = log.slice(-300);
        if (fresh.length) {
            // presence: a protocol event proves SoulSync — refresh buckets
            renderUsersList();
            renderBusUI();
            try {
                document.dispatchEvent(new CustomEvent('soulsync:chat-protocol',
                    { detail: { events: fresh } }));
            } catch (e) { /* older browsers: features just poll the log */ }
        }
    }

    function sendProtocol(kind, fields) {
        // One-liner for features: fire a coordination event into the room.
        // Respects the send gate server-side; failures are silent (fun-grade).
        var p = Object.assign({ k: kind }, fields || {});
        return postJSON('/api/chat/room/protocol', { room: state.room, p: p });
    }

    function _sendJoinBeacon() {
        // Announce capability ONCE per room per session — powers the
        // assume-SoulSync presence for users who haven't typed anything.
        if (!state.canSend || !state.room || state.beaconed[state.room]) return;
        state.beaconed[state.room] = 1;
        // carry the avatar so we get a face before we've said anything
        sendProtocol('hello', _myAvatar() ? { av: _myAvatar() } : {}).then(function (r) {
            if (!r.ok) state.beaconed[state.room] = 0;   // retry next refresh
        });
    }

    function onRoomProtocol(d) {
        if (!d || d.room !== state.room) return;
        _ingestProtocol(d.events || []);
    }

    // ── typing indicators (typ events on the bus, deliberately frugal:
    // every carrier is a visible noise line for vanilla clients, so we emit
    // on composition start + at most one refresh per 20s, never per-key) ──
    var _TYP_TTL = 25000;      // matches the ≤20s re-emit cadence + slack

    function _maybeSendTyping(input) {
        if (state.view !== 'room' || !state.canSend) return;
        if (!input || !(input.value || '').trim()) return;
        if ((input.value || '')[0] === '/') return;      // commands aren't messages
        if (Date.now() - state.lastTypSentAt < 20000) return;
        state.lastTypSentAt = Date.now();
        sendProtocol('typ', {});
    }

    function renderTyping() {
        var host = q('[data-chat-typing]');
        if (!host) return;
        var cut = Date.now() - _TYP_TTL;
        var names = [];
        Object.keys(state.typing).forEach(function (n) {
            if (state.typing[n] < cut) delete state.typing[n];
            else names.push(n);
        });
        if (!names.length || state.view !== 'room') { host.hidden = true; host.innerHTML = ''; return; }
        names.sort();
        var who = names.length === 1 ? '<b>' + esc(names[0]) + '</b> is'
            : names.length === 2 ? '<b>' + esc(names[0]) + '</b> and <b>' + esc(names[1]) + '</b> are'
            : names.length + ' people are';
        host.innerHTML = '<span class="chat-typing-dots"><i></i><i></i><i></i></span> ' + who + ' typing…';
        host.hidden = false;
        if (state.typingTimer) clearTimeout(state.typingTimer);
        state.typingTimer = setTimeout(renderTyping, 5000);
    }

    function _clearTypingFor(messages) {
        // an arriving message from a typer means they sent it — clear now
        var changed = false;
        (messages || []).forEach(function (m) {
            if (m && m.username && state.typing[m.username]) {
                delete state.typing[m.username];
                changed = true;
            }
        });
        if (changed) renderTyping();
    }

    // Every surface reduced from the protocol bus, painted together.
    function renderBusUI() {
        renderJukebox();
        renderPinbar();
        renderPoll();
        // topic lives in the head sub-line — but search mode freezes the
        // head (its input would be clobbered mid-typing by a socket event)
        if (!state.searchMode) renderHead();
    }

    // ── pinned messages (pin.add / pin.del on the bus) ──────────────────
    function renderPinbar() {
        // A POPOVER, not a standing bar (Boulder): pins are look-up-occasionally,
        // so they cost zero message height until the head 📌 button opens them.
        var host = q('[data-chat-pinbar]');
        if (!host) return;
        var show = state.pinsOpen && state.view === 'room';
        host.hidden = !show;
        if (!show) { host.innerHTML = ''; return; }
        var CP = window.ChatProtocol;
        var pins = CP ? CP.reducePins(_roomEvents()) : [];
        host.innerHTML = '<div class="chat-pins-title">📌 Pinned messages</div>' +
            (pins.length
                ? pins.slice().reverse().map(function (pin) {
                    return '<div class="chat-pin-row">' +
                        '<span class="chat-pin-text"><b>' + esc(pin.u) + '</b> ' + esc(pin.x) + '</span>' +
                        '<span class="chat-pin-by">pinned by ' + esc(pin.by) + '</span>' +
                        (state.canSend
                            ? '<button class="chat-pin-del" type="button" title="Unpin" ' +
                              'data-chat-pin-del-u="' + attr(pin.u) + '" data-chat-pin-del-ts="' + attr(pin.ts) + '">×</button>'
                            : '') +
                    '</div>';
                }).join('')
                : '<div class="chat-side-none">Nothing pinned yet — hover a message and hit 📌</div>');
    }

    // ── the room poll (poll.start / poll.vote / poll.end on the bus) ────
    function renderPoll() {
        var host = q('[data-chat-poll]');
        if (!host) return;
        var CP = window.ChatProtocol;
        var poll = (CP && state.view === 'room') ? CP.reducePoll(_roomEvents()) : null;
        if (!poll || (poll.closed && state.pollDismissedAt === poll.at)) {
            host.hidden = true; host.innerHTML = ''; return;
        }
        host.hidden = false;
        var total = poll.tally.total;
        var rows = poll.options.map(function (opt, i) {
            var idx = String(i + 1);
            var n = poll.tally.counts[idx] || 0;
            var pct = total ? Math.round(n * 100 / total) : 0;
            var winner = poll.closed && poll.tally.winner === idx;
            return '<div class="chat-poll-opt' + (winner ? ' chat-poll-opt--win' : '') + '">' +
                (poll.closed || !state.canSend
                    ? '<span class="chat-poll-label">' + esc(opt) + '</span>'
                    : '<button class="chat-poll-vote" type="button" data-chat-poll-vote="' + idx + '">' +
                          esc(opt) + '</button>') +
                '<span class="chat-poll-n">' + n + (total ? ' · ' + pct + '%' : '') + '</span>' +
                '<span class="chat-poll-bar" style="width:' + pct + '%"></span>' +
            '</div>';
        }).join('');
        host.innerHTML =
            '<div class="chat-poll-head">📊 <b>' + esc(poll.q) + '</b>' +
                '<span class="chat-jbx-meta">' + (poll.closed ? 'final — ' : '') +
                    total + ' vote' + (total === 1 ? '' : 's') + ' · by ' + esc(poll.by) + '</span>' +
                (!poll.closed && state.selfName && poll.by === state.selfName
                    ? '<button class="chat-fmt-btn" type="button" data-chat-poll-end>End poll</button>' : '') +
                (poll.closed
                    ? '<button class="chat-pin-del" type="button" title="Dismiss" data-chat-poll-dismiss>×</button>' : '') +
            '</div>' + rows;
    }

    function _pollStart() {
        var qEl = q('[data-chat-poll-q]');
        if (!qEl) return;
        var fields = { q: String(qEl.value || '').trim() };
        var opts = 0;
        for (var i = 1; i <= 4; i++) {
            var o = q('[data-chat-poll-o' + i + ']');
            var v = o ? String(o.value || '').trim() : '';
            if (v) { opts += 1; fields['o' + opts] = v; }   // compact gaps
        }
        if (!fields.q || opts < 2) {
            if (typeof showToast === 'function') showToast('A poll needs a question and at least 2 options', 'error');
            return;
        }
        sendProtocol('poll.start', fields);
        [qEl].concat([1, 2, 3, 4].map(function (i2) { return q('[data-chat-poll-o' + i2 + ']'); }))
            .forEach(function (el) { if (el) el.value = ''; });
        togglePollPop(true);
        state.pollDismissedAt = null;
    }

    function togglePollPop(forceClose) {
        var pop = q('[data-chat-poll-pop]');
        if (!pop) return;
        pop.hidden = forceClose === true ? true : !pop.hidden;
        if (!pop.hidden) {
            toggleEmojiPicker(true); toggleGifPicker(true); toggleAttachPanel(true);
            var qEl = q('[data-chat-poll-q]');
            if (qEl) qEl.focus();
        }
    }

    // ── jukebox (shared room listening — a pure fold over the bus) ──────
    // State lives in the protocol stream (jbx.sub / jbx.vote / jbx.now);
    // every client reduces the same events to the same queue + now-playing.
    // Playback is an OPT-IN YouTube embed ("Tune in" = the user gesture
    // browsers require for audible autoplay); joiners seek to the live
    // position from now.at. The DJ (deterministic election, no chatter) is
    // the one client that emits jbx.now when a track ends or the queue waits.
    function _roomEvents() {
        var room = state.room || '';
        return (state.protocolLog || []).filter(function (e) { return e.room === room; });
    }

    function _jbxState() {
        var CP = window.ChatProtocol;
        if (!CP) return { queue: [], now: null, tally: { counts: {}, winner: null, total: 0 } };
        return CP.reduceJukebox(_roomEvents());
    }

    function _jbxIsDj() {
        // DJ candidates are PROTOCOL-CAPABLE clients only: users who have
        // emitted protocol events (hello beacon, jukebox chatter) in this
        // room. Envelope messages are NOT enough — every pre-jukebox
        // SoulSync version speaks envelopes, and electing one of those gets
        // a DJ that can never press play. We're always in our own pool.
        var CP = window.ChatProtocol;
        if (!CP || !state.canSend || !state.selfName) return false;
        var emitters = {};
        _roomEvents().forEach(function (e) { emitters[e.username] = 1; });
        var pool = (state.users || []).filter(function (n) { return emitters[n]; });
        if (pool.indexOf(state.selfName) === -1) pool.push(state.selfName);
        return CP.electCoordinator(pool) === state.selfName;
    }

    // Track WHEN WE saw a now-track start, so elapsed is measured on our own
    // clock instead of the DJ's. `at` is the publisher's wall clock, and a
    // client whose clock runs minutes fast used to read every track as long
    // overdue — if that client was the DJ it advanced immediately and raced
    // through the whole queue. `at` is now only consulted when we JOIN
    // mid-track (the one case where we genuinely need someone else's offset).
    function _jbxNoteNow(now) {
        if (!now) { state.jukebox.nowSeen = null; return; }
        var s = state.jukebox.nowSeen;
        if (s && s.id === now.id) return;                  // already timing it
        var base = 0;
        if (!s && typeof now.at === 'number') {
            // Cold open: we joined with something already playing — trust `at`
            // for the starting offset (clamped; a wild clock reads as 0).
            var d = Math.floor(Date.now() / 1000 - now.at);
            if (d > 0 && d < 86400) base = d;
        }
        // A handoff we watched happen started NOW, by our clock. No skew.
        state.jukebox.nowSeen = { id: now.id, localStart: Date.now(), base: base };
    }

    function _jbxElapsed(now) {
        if (!now) return null;
        var s = state.jukebox.nowSeen;
        if (s && s.id === now.id) {
            return s.base + Math.floor((Date.now() - s.localStart) / 1000);
        }
        if (typeof now.at !== 'number') return null;
        var d = Math.floor(Date.now() / 1000 - now.at);
        return (d >= 0 && d < 86400) ? d : null;
    }

    function _fmtSecs(s) {
        if (s === null || isNaN(s)) return '';
        var m = Math.floor(s / 60), r = s % 60;
        return m + ':' + (r < 10 ? '0' : '') + r;
    }

    function _jbxThumb(id) {
        return 'https://i.ytimg.com/vi/' + id + '/mqdefault.jpg';
    }

    function _jbxEffDuration(now) {
        // the protocol event's duration when it has one, else a live player's
        // truth (pasted links resolve via oEmbed, which has no duration)
        if (!now) return null;
        if (now.d) return now.d;
        if (state.jukebox.tunedIn && state.jukebox.playerAlive &&
                state.jukebox.player && state.jukebox.playingId === now.id) {
            try {
                var pd = state.jukebox.player.getDuration();
                if (pd > 0) return Math.floor(pd);
            } catch (e) { /* mid-teardown */ }
        }
        return null;
    }

    function _jbxSkipNeeded() {
        // majority of tuned-in listeners (deterministic — derived from the
        // same event stream everywhere); floor of 1 when nobody's tuned
        var CP = window.ChatProtocol;
        var n = CP ? Object.keys(CP.reduceTuned(_roomEvents())).length : 0;
        return Math.max(1, Math.ceil(n / 2));
    }

    function _jbxHasListeners() {
        // Is anyone actually listening? Counts ourself the instant we tune in
        // (before our own jbx.tune echoes back). Gates auto-DJ so an unwatched
        // room never generates an endless stream of tracks nobody hears.
        if (state.jukebox.tunedIn) return true;
        var CP = window.ChatProtocol;
        return !!(CP && Object.keys(CP.reduceTuned(_roomEvents())).length);
    }

    function renderJukebox() {
        var panel = q('[data-chat-jukebox]');
        if (!panel) return;
        var st = _jbxState();
        var now = st.now;
        _jbxNoteNow(now);             // same clock base the watchdog uses
        var elapsed = _jbxElapsed(now);
        var effD = _jbxEffDuration(now);
        var ended = !!(now && effD && elapsed !== null && elapsed > effD + 5);
        // Display honesty (Boulder): the shared now-event can scroll out of the
        // bounded protocol log (busy room / long track), or a wrong duration
        // can flag 'ended' while the audio is genuinely still playing — either
        // way the panel would flip to 'Nothing playing' over a live track.
        // If our own player is actively playing, keep showing that track.
        if ((!now || ended) && state.jukebox.tunedIn && state.jukebox.playerAlive &&
                state.jukebox.player && state.jukebox.playingNow) {
            try {
                var _ps = state.jukebox.player.getPlayerState();
                if (_ps === 1 || _ps === 2 || _ps === 3) {   // playing / paused / buffering
                    now = state.jukebox.playingNow;
                    elapsed = _jbxElapsed(now);
                    effD = _jbxEffDuration(now);
                    ended = false;
                }
            } catch (e) { /* player mid-teardown */ }
        }
        // the player follows the ROOM, not the panel — a tuned-in listener
        // reading PMs must still hear the DJ's advances (panel merely hides)
        _jbxSyncPlayer(now && !ended ? now : null);
        // the header bar (brand + listeners + add-a-song) lives in the page
        // header and shows whenever a room is on screen — panel open or not
        var inRoom = state.view === 'room';
        var headbar = q('[data-chat-jbx-headbar]');
        if (headbar) {
            headbar.hidden = !inRoom;
            var form = q('[data-chat-jbx-form]');
            if (form) form.hidden = !state.canSend;
            var lc = q('[data-chat-jbx-listeners]');
            if (lc && window.ChatProtocol) {
                var nTuned = Object.keys(window.ChatProtocol.reduceTuned(_roomEvents())).length;
                lc.textContent = nTuned ? '♪ ' + nTuned + ' listening' : '';
            }
            var rb = q('[data-chat-jbx-radio]');
            if (rb) {
                rb.hidden = !state.canSend;
                rb.classList.toggle('chat-filter-btn--on', !!st.radio);
                rb.classList.toggle('chat-jbx-radiobtn--on', !!st.radio);
            }
        }
        var show = state.jukebox.open && inRoom;
        panel.hidden = !show;
        if (!show) return;
        var needed = _jbxSkipNeeded();
        var nextId = (st.queue.length > 1 && window.ChatProtocol)
            ? (window.ChatProtocol.nextTrack(st) || {}).id : null;
        // fingerprint: skip DOM writes when nothing visible changed (the
        // clock + progress bar tick via cheap updates below)
        var fp = JSON.stringify([now && now.id, ended, st.queue, state.jukebox.tunedIn,
                                 state.canSend, st.skips, needed, nextId,
                                 state.jukebox.histOpen, st.history.length,
                                 st.history[0] && st.history[0].id,   // cap rotation changes content, not length
                                 state.jukebox.videoHidden, st.radio]);
        if (fp !== state.jukebox.lastRendered) {
            state.jukebox.lastRendered = fp;
            var nowHost = q('[data-chat-jbx-now]');
            if (nowHost) {
                if (now && !ended) {
                    var pct = (effD && elapsed !== null)
                        ? Math.min(100, 100 * elapsed / effD) : 0;
                    nowHost.innerHTML =
                        '<div class="chat-jbx-nowcard">' +
                            '<img class="chat-jbx-art" src="' + attr(_jbxThumb(now.id)) + '" alt="">' +
                            '<div class="chat-jbx-nowmain">' +
                                '<div class="chat-jbx-titlerow">' +
                                    '<span class="chat-jbx-eq"><i></i><i></i><i></i></span>' +
                                    '<a class="chat-jbx-title" href="https://youtu.be/' + attr(now.id) +
                                        '" target="_blank" rel="noopener" title="' + attr(now.ti || now.id) + '">' +
                                        esc(now.ti || now.id) + '</a>' +
                                '</div>' +
                                '<div class="chat-jbx-meta">added by ' + esc(now.by || '?') +
                                    (elapsed !== null ? ' · <span data-chat-jbx-clock>' + _fmtSecs(elapsed) + '</span>' +
                                        (effD ? ' / ' + _fmtSecs(effD) : '') : '') + '</div>' +
                                '<div class="chat-jbx-progress"><span class="chat-jbx-progbar" ' +
                                    'data-chat-jbx-bar style="width:' + pct.toFixed(1) + '%"></span></div>' +
                            '</div>' +
                        '</div>' +
                        '<div class="chat-jbx-controls">' +
                            (state.jukebox.tunedIn
                                ? '<button class="chat-fmt-btn chat-jbx-tune" type="button" data-chat-jbx-tuneout>Tune out</button>'
                                : '<button class="chat-send-btn chat-jbx-tune" type="button" data-chat-jbx-tunein>▶ Tune in</button>') +
                            '<button class="chat-fmt-btn" type="button" data-chat-jbx-skip="' + attr(now.id) + '"' +
                                (state.canSend ? '' : ' disabled') +
                                ' title="Vote to skip this track (majority of listeners)">⏭ Skip' +
                                (st.skips ? ' ' + st.skips + '/' + needed : '') + '</button>' +
                            (state.jukebox.tunedIn
                                ? '<button class="chat-fmt-btn" type="button" data-chat-jbx-video title="' +
                                      (state.jukebox.videoHidden ? 'Show the video' : 'Audio only — hide the video') + '">' +
                                      (state.jukebox.videoHidden ? '🎬 Video' : '🎧 Audio only') + '</button>' +
                                  '<input class="chat-jbx-vol" data-chat-jbx-vol type="range" min="0" max="100" ' +
                                      'value="' + state.jukebox.vol + '" title="Volume (just yours)">'
                                : '') +
                        '</div>';
                } else {
                    // tuned-in users keep their exit even between tracks.
                    // Auto-DJ needs something to sound like: with no now-playing
                    // and nothing in the room's history it has no seed to search
                    // from, so say that instead of looking silently broken.
                    var idleMsg;
                    if (st.queue.length) {
                        idleMsg = 'Waiting for the next track…';
                    } else if (st.radio && !(st.history && st.history.length)) {
                        idleMsg = 'Auto-DJ is on, but it needs a starting point — ' +
                                  'add one song and it takes over from there.';
                    } else {
                        idleMsg = 'Nothing playing — add a song above and get the room voting.';
                    }
                    nowHost.innerHTML = '<div class="chat-jbx-meta chat-jbx-idle">' + idleMsg + '</div>' +
                        (state.jukebox.tunedIn
                            ? '<button class="chat-fmt-btn chat-jbx-tune" type="button" data-chat-jbx-tuneout>Tune out</button>' : '');
                }
            }
            var qHost = q('[data-chat-jbx-queue]');
            if (qHost) {
                var rows = st.queue.map(function (e) {
                    var mine = state.selfName && e.by === state.selfName;
                    return '<div class="chat-jbx-row' + (e.id === nextId ? ' chat-jbx-row--next' : '') + '">' +
                        '<img class="chat-jbx-qthumb" src="' + attr(_jbxThumb(e.id)) + '" alt="" loading="lazy">' +
                        '<div class="chat-jbx-qmain">' +
                            '<span class="chat-jbx-title" title="' + attr(e.ti || e.id) + '">' + esc(e.ti || e.id) + '</span>' +
                            '<span class="chat-jbx-meta">' +
                                (e.id === nextId ? '<b class="chat-jbx-next">up next</b> · ' : '') +
                                (e.auto ? '📻 ' + (e.why ? esc(e.why) : 'auto') + ' · ' : '') +
                                (e.d ? _fmtSecs(e.d) + ' · ' : '') + esc(e.by) + '</span>' +
                        '</div>' +
                        '<button class="chat-jbx-vote" type="button" data-chat-jbx-vote="' + attr(e.id) + '"' +
                            (state.canSend ? '' : ' disabled') + ' title="Vote to play this next">▲ ' +
                            (e.votes || 0) + '</button>' +
                        (mine && state.canSend
                            ? '<button class="chat-jbx-unsub" type="button" data-chat-jbx-unsub="' + attr(e.id) +
                              '" title="Remove your submission">×</button>' : '') +
                    '</div>';
                }).join('');
                if (st.history.length) {
                    rows += '<button class="chat-jbx-histbtn" type="button" data-chat-jbx-hist>' +
                        (state.jukebox.histOpen ? '▾' : '▸') + ' Recently played (' + st.history.length + ')</button>';
                    if (state.jukebox.histOpen) {
                        rows += st.history.map(function (h) {
                            return '<div class="chat-jbx-row chat-jbx-row--hist">' +
                                '<img class="chat-jbx-qthumb" src="' + attr(_jbxThumb(h.id)) + '" alt="" loading="lazy">' +
                                '<div class="chat-jbx-qmain">' +
                                    '<span class="chat-jbx-title" title="' + attr(h.ti || h.id) + '">' + esc(h.ti || h.id) + '</span>' +
                                    '<span class="chat-jbx-meta">' + esc(h.by || '') + '</span>' +
                                '</div>' +
                                (state.canSend
                                    ? '<button class="chat-jbx-vote" type="button" data-chat-jbx-resub="' + attr(h.id) +
                                      '" data-chat-jbx-resub-ti="' + attr(h.ti || '') + '"' +
                                      (h.d ? ' data-chat-jbx-resub-d="' + attr(String(h.d)) + '"' : '') +
                                      ' title="Queue it again">↻</button>' : '') +
                            '</div>';
                        }).join('');
                    }
                }
                qHost.innerHTML = rows;
            }
        } else if (elapsed !== null) {
            var clock = q('[data-chat-jbx-clock]');
            if (clock) clock.textContent = _fmtSecs(elapsed);
            var bar = q('[data-chat-jbx-bar]');
            if (bar && effD) bar.style.width = Math.min(100, 100 * elapsed / effD).toFixed(1) + '%';
        }
    }

    function toggleJukebox() {
        state.jukebox.open = !state.jukebox.open;
        if (!state.jukebox.open) _jbxTuneOut();
        state.jukebox.lastRendered = '';
        renderHead();
        renderJukebox();
        if (state.jukebox.open && !state.jukebox.timer) {
            state.jukebox.timer = setInterval(function () {
                renderJukebox();
                _jbxWatchdog();
            }, 5000);
        } else if (!state.jukebox.open && state.jukebox.timer) {
            clearInterval(state.jukebox.timer);
            state.jukebox.timer = null;
        }
    }

    // DJ duties: kick the queue when nothing is playing, or when the current
    // track has provably run out (duration known) and nobody advanced it.
    // Starvation fallback: if the elected DJ went away mid-session (closed
    // tab, network), ANY capable client kicks the queue after 45s — a rare
    // double-start converges (latest now wins, same track either way).
    function _jbxWatchdog() {
        // Drives the shared queue whenever we're viewing a room — NOT gated on
        // the jukebox panel being open, so the elected DJ advances the room
        // even with the panel closed (else the queue stalled until a 45s
        // starvation fallback, or froze if the DJ never opened the panel).
        // Called from the 5s panel timer AND the 4s room refresh; both are
        // already behind pageVisible, so a backgrounded tab never DJs.
        if (state.view !== 'room') return;
        var st = _jbxState();
        _jbxNoteNow(st.now);          // stamp handoffs on OUR clock (skew guard)
        var elapsed = _jbxElapsed(st.now);
        // A tuned-in client asks the PLAYER for the truth — pasted links have
        // no duration (oEmbed doesn't give one), and the iframe's ENDED event
        // is best-effort, so poll instead of trusting either.
        var effD = _jbxEffDuration(st.now);
        var playerEnded = false;
        if (state.jukebox.tunedIn && state.jukebox.playerAlive && state.jukebox.player) {
            try {
                playerEnded = state.jukebox.player.getPlayerState() === 0;   // YT ENDED
            } catch (e) { /* player mid-teardown */ }
        }
        var skipped = !!(st.now && st.skips >= _jbxSkipNeeded());
        var stale = !st.now || playerEnded || skipped ||
            (effD && elapsed !== null && elapsed > effD + 8) ||
            (!effD && elapsed !== null && elapsed > 900);   // untuned + unknown length: 15-min cap
        // Radio only refills for an audience — an empty, unwatched room must
        // not spin up an endless YouTube stream nobody hears. (Advancing an
        // EXISTING queue below is unconditional: a finite queue just drains.)
        if (!st.queue.length && st.radio && _jbxIsDj() && _jbxHasListeners()) _jbxAutoQueue(st);
        if (!st.queue.length || !stale) { state.jukebox.starvedAt = 0; return; }
        if (_jbxIsDj()) { _jbxAdvance(st); return; }
        if (!state.jukebox.starvedAt) {
            state.jukebox.starvedAt = Date.now();
        } else if (Date.now() - state.jukebox.starvedAt > 45000) {
            state.jukebox.starvedAt = 0;
            _jbxAdvance(st);
        }
    }

    function _jbxAutoQueue(st) {
        // Radio mode: the queue ran dry — find something related to what the
        // room just heard and queue it (marked auto, still vote/skippable).
        if (Date.now() - (state.jukebox.lastAutoAt || 0) < 25000) return;
        var seed = st.now || (st.history && st.history[0]);
        if (!seed || !seed.ti) return;
        state.jukebox.lastAutoAt = Date.now();
        // strip (Official Video)-style noise so the search finds neighbors
        // Video ids we must not repeat, plus the artist/title STRINGS the room
        // just heard — the server uses those to steer away from what's been on.
        var avoid = {};
        var avoidText = [];
        if (st.now) { avoid[st.now.id] = 1; avoidText.push(st.now.ti || ''); }
        (st.history || []).forEach(function (h) {
            avoid[h.id] = 1;
            if (h.ti) avoidText.push(h.ti);
        });
        // Send the raw titles too — the server splits "Artist - Track" itself.
        avoidText = avoidText.concat(avoidText.map(function (t) {
            var i = String(t).indexOf(' - ');
            return i > 0 ? String(t).slice(0, i) : '';
        })).filter(Boolean).slice(0, 80);

        var fallbackQ = seed.ti.replace(/[\(\[][^)\]]*[\)\]]/g, ' ')
            .replace(/\s+/g, ' ').trim().slice(0, 150);

        function _queueFrom(qtext, why) {
            if (!qtext) return;
            postJSON('/api/chat/jukebox/resolve', { q: qtext }).then(function (res) {
                if (!res.ok) return;               // paste-only servers: radio idles
                var pick = (res.body.results || []).filter(function (r) {
                    return r && r.id && !avoid[r.id];
                })[0];
                if (!pick) return;
                var p = { id: pick.id, ti: pick.title, a: 1 };
                if (pick.duration) p.d = pick.duration;
                if (why) p.w = String(why).slice(0, 60);   // "similar to X" credit
                sendProtocol('jbx.sub', p);
            });
        }

        // Ask the radio brain for a genuinely DIFFERENT next track (Last.fm
        // similar-tracks → similar-artists → the local graph). Only if it has
        // nothing do we fall back to the old behaviour of re-searching this
        // track's title, which tends to surface the same song again.
        postJSON('/api/chat/jukebox/radio', { title: seed.ti, avoid: avoidText })
            .then(function (res) {
                var q = res.ok && res.body && res.body.query;
                if (q) _queueFrom(q, res.body.why);
                else _queueFrom(fallbackQ, '');
            })
            .catch(function () { _queueFrom(fallbackQ, ''); });
    }

    function _jbxAdvance(st) {
        var CP = window.ChatProtocol;
        if (!CP || !state.canSend) return;
        if (Date.now() - state.jukebox.lastAdvanceAt < 15000) return;  // outlast a slow slskd roundtrip
        var next = CP.nextTrack(st || _jbxState());
        if (!next) return;
        state.jukebox.lastAdvanceAt = Date.now();
        var p = { id: next.id, ti: next.ti, at: Math.floor(Date.now() / 1000) };
        if (next.d) p.d = next.d;
        sendProtocol('jbx.now', p);
    }

    // ── jukebox playback (YouTube iframe API, loaded on first tune-in) ──
    function _jbxLoadYT(cb) {
        if (window.YT && window.YT.Player) { cb(); return; }
        state.jukebox.ytCbs.push(cb);
        if (state.jukebox.ytLoading) return;
        state.jukebox.ytLoading = true;
        var prev = window.onYouTubeIframeAPIReady;
        window.onYouTubeIframeAPIReady = function () {
            if (typeof prev === 'function') { try { prev(); } catch (e) { /* theirs */ } }
            var cbs = state.jukebox.ytCbs;
            state.jukebox.ytCbs = [];
            cbs.forEach(function (f) { try { f(); } catch (e) { /* one bad cb */ } });
        };
        var s = document.createElement('script');
        s.src = 'https://www.youtube.com/iframe_api';
        document.head.appendChild(s);
    }

    function _jbxTuneIn() {
        var st = _jbxState();
        if (!st.now) return;
        state.jukebox.tunedIn = true;
        sendProtocol('jbx.tune', { on: 1 });
        state.jukebox.lastRendered = '';
        renderJukebox();
        _jbxLoadYT(function () {
            if (!state.jukebox.tunedIn) return;        // tuned out while loading
            _jbxSyncPlayer(_jbxState().now);
        });
    }

    function _jbxTuneOut() {
        if (state.jukebox.tunedIn) sendProtocol('jbx.tune', { on: 0 });
        state.jukebox.tunedIn = false;
        if (state.jukebox.player) {
            try { state.jukebox.player.destroy(); } catch (e) { /* already gone */ }
        }
        state.jukebox.player = null;
        state.jukebox.playingId = null;
        state.jukebox.playingNow = null;
        state.jukebox.playerAlive = false;
        var host = q('[data-chat-jbx-player]');
        if (host) { host.innerHTML = ''; host.hidden = true; }
        state.jukebox.lastRendered = '';
    }

    function _jbxSyncPlayer(now) {
        // Point the player at `now`, seeking to the live position. Never
        // touches the DOM outside [data-chat-jbx-player] — renderJukebox
        // depends on that so the iframe survives queue re-renders.
        if (!state.jukebox.tunedIn) return;
        if (!now) return;   // between tracks: keep the player, never kick the listener
        if (!(window.YT && window.YT.Player)) return;  // tune-in cb will land here again
        var host = q('[data-chat-jbx-player]');
        if (!host) return;
        var offset = Math.max(0, _jbxElapsed(now) || 0);
        host.classList.toggle('chat-jbx-player--audio', state.jukebox.videoHidden);
        if (!state.jukebox.player) {
            host.hidden = false;
            host.innerHTML = '<div data-chat-jbx-yt></div>';
            state.jukebox.playingId = now.id;
            state.jukebox.playingNow = now;
            state.jukebox.player = new window.YT.Player(host.firstChild, {
                width: '100%', height: '158', videoId: now.id,
                playerVars: { autoplay: 1, start: offset, rel: 0, playsinline: 1 },
                events: {
                    onReady: function () {
                        state.jukebox.playerAlive = true;
                        try { state.jukebox.player.setVolume(state.jukebox.vol); } catch (e) { /* gone */ }
                        _jbxSyncPlayer(_jbxState().now);   // catch a now-change during boot
                    },
                    onStateChange: _jbxOnPlayerState,
                },
            });
        } else if (state.jukebox.playingId !== now.id && state.jukebox.playerAlive) {
            state.jukebox.playingId = now.id;
            state.jukebox.playingNow = now;
            try {
                state.jukebox.player.loadVideoById({ videoId: now.id, startSeconds: offset });
            } catch (e) { _jbxTuneOut(); }
        }
    }

    function _jbxOnPlayerState(e) {
        // ENDED (0): drop the display fallback so the panel can honestly show
        // 'waiting for the next track', and (if we're the DJ) advance the room.
        if (e && e.data === 0) {
            state.jukebox.playingNow = null;
            if (_jbxIsDj()) _jbxAdvance(null);
        }
    }

    function _jbxSubmit() {
        var input = q('[data-chat-jbx-input]');
        var resHost = q('[data-chat-jbx-results]');
        if (!input || state.jukebox.resolving) return;
        var qtext = String(input.value || '').trim();
        if (!qtext) return;
        state.jukebox.resolving = true;
        if (resHost) { resHost.hidden = false; resHost.innerHTML = '<div class="chat-jbx-meta">Looking that up…</div>'; }
        postJSON('/api/chat/jukebox/resolve', { q: qtext }).then(function (res) {
            state.jukebox.resolving = false;
            if (!res.ok || !(res.body.results || []).length) {
                if (resHost) resHost.innerHTML = '<div class="chat-jbx-meta">' +
                    esc((res.body && res.body.error) || 'Nothing found — try a link or a different search.') + '</div>';
                return;
            }
            var results = res.body.results;
            if (resHost) { resHost.hidden = true; resHost.innerHTML = ''; }
            if (results.length === 1) {                 // pasted link → straight in
                _jbxPick(results[0]);
                return;
            }
            _openJbxSearchModal(results, qtext);         // a search → the rich picker
        }).catch(function () {
            state.jukebox.resolving = false;
            if (resHost) resHost.innerHTML = '<div class="chat-jbx-meta">Lookup failed — try again.</div>';
        });
    }

    // ── jukebox YouTube search modal (search-page video-card look) ──────
    function _fmtViews(n) {
        n = Number(n) || 0;
        if (n >= 1e9) return (n / 1e9).toFixed(1) + 'B';
        if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
        if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
        return n ? String(n) : '';
    }

    function _jbxSearchCards(results) {
        return (results || []).map(function (r, i) {
            var views = _fmtViews(r.views);
            return '<button class="chat-jbx-vcard" type="button" data-chat-jbx-vpick="' + i + '">' +
                '<div class="chat-jbx-vthumb">' +
                    '<img src="' + attr(_jbxThumb(r.id)) + '" alt="" loading="lazy">' +
                    '<span class="chat-jbx-vplay">▶</span>' +
                    (r.duration ? '<span class="chat-jbx-vdur">' + _fmtSecs(r.duration) + '</span>' : '') +
                '</div>' +
                '<div class="chat-jbx-vinfo">' +
                    '<div class="chat-jbx-vtitle" title="' + attr(r.title || r.id) + '">' + esc(r.title || r.id) + '</div>' +
                    '<div class="chat-jbx-vchannel">' + esc(r.channel || '') +
                        (views ? ' · ' + views + ' views' : '') + '</div>' +
                '</div>' +
            '</button>';
        }).join('');
    }

    function _openJbxSearchModal(results, query) {
        var ov = q('[data-chat-jbx-search-modal]');
        if (!ov) { _jbxPick(results[0]); return; }   // no modal in DOM → graceful fallback
        state.jukebox.searchResults = results;
        var grid = q('[data-chat-jbx-searchgrid]');
        if (grid) grid.innerHTML = _jbxSearchCards(results) ||
            '<div class="chat-jbx-meta">Nothing found — try different words or paste a link.</div>';
        var inp = q('[data-chat-jbx-searchinput]');
        if (inp) inp.value = query || '';
        ov.hidden = false;
    }

    function _closeJbxSearchModal() {
        var ov = q('[data-chat-jbx-search-modal]');
        if (ov) ov.hidden = true;
        state.jukebox.searchResults = [];
    }

    function _jbxSearchModalSubmit() {
        var inp = q('[data-chat-jbx-searchinput]');
        var grid = q('[data-chat-jbx-searchgrid]');
        if (!inp || state.jukebox.resolving) return;
        var qtext = String(inp.value || '').trim();
        if (!qtext) return;
        state.jukebox.resolving = true;
        if (grid) grid.innerHTML = '<div class="chat-jbx-meta">Searching…</div>';
        postJSON('/api/chat/jukebox/resolve', { q: qtext }).then(function (res) {
            state.jukebox.resolving = false;
            var results = (res.ok && res.body.results) || [];
            state.jukebox.searchResults = results;
            if (grid) grid.innerHTML = results.length ? _jbxSearchCards(results) :
                '<div class="chat-jbx-meta">' +
                esc((res.body && res.body.error) || 'Nothing found — try different words or paste a link.') + '</div>';
        }).catch(function () {
            state.jukebox.resolving = false;
            if (grid) grid.innerHTML = '<div class="chat-jbx-meta">Search failed — try again.</div>';
        });
    }

    function _jbxPick(r) {
        if (!r || !r.id) return;
        var p = { id: r.id, ti: String(r.title || '').slice(0, 120) };
        if (r.duration) p.d = r.duration;
        sendProtocol('jbx.sub', p);
        if (!state.jukebox.open && state.view === 'room') toggleJukebox();
        var input = q('[data-chat-jbx-input]');
        if (input) input.value = '';
        var resHost = q('[data-chat-jbx-results]');
        if (resHost) { resHost.hidden = true; resHost.innerHTML = ''; }
        state.jukebox.results = [];
        if (typeof showToast === 'function') showToast('♫ Added to the room queue', 'success');
    }

    function onRoomMessages(d) {
        _ensureSelf();
        // a mention pings you wherever you are in the app (Discord behavior)
        var mentioned = (d && d.messages || []).filter(function (m) {
            return mentionsMe(m.message);
        });
        if (mentioned.length && !(pageVisible() && state.view === 'room') &&
                typeof showToast === 'function') {
            showToast('💬 ' + (mentioned[0].username || 'someone') +
                ' mentioned you in # ' + (state.room || 'chat'), 'info');
        }
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
                        onRoomProtocol: onRoomProtocol, sendProtocol: sendProtocol,
                        // exported for the node render harness (XSS contract tests)
                        renderRich: renderRich, renderPlain: renderPlain,
                        renderGroups: renderGroups,
                        _testSetSelf: function (n) { state.selfName = n; },
                        _testSetState: function (patch) {
                            Object.keys(patch || {}).forEach(function (k) { state[k] = patch[k]; });
                        } };
})();
