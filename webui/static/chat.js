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
        connectionError: null,
        configured: null,        // null = unknown yet
        timer: null,
        lastStamp: null,         // newest message timestamp we've rendered
        stickBottom: true,       // autoscroll unless the user scrolled up
        failStreak: 0,           // consecutive failed polls (see pollProblem)
        renderedOk: false,       // this VIEW has painted real content since
                                 // it was opened — the hiccup gate. never
                                 // state.msgs: that is the ROOM store and a
                                 // DM would read the room's leftovers.
        started: false,
        ssOnly: false,           // room filter AND send format: see + speak SoulSync
        protocolLog: [],         // recent machine-coordination events (bounded)
        beaconed: {},            // rooms we've announced ourselves in this session
        isAdmin: false,          // shows the settings cog (from /status)
        newMarker: null,         // frozen last-seen ts for the NEW divider (per room open)
        renderedCount: 0,        // for the new-messages pill delta
        msgs: [],                // room message store: archive pages + live tail (merged)
        pmMsgs: [],              // active PM conversation store (messages + pending outgoing)
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
        editing: null,           // {key} while editing one of your own messages
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
        trivDismissedAt: null,   // locally-dismissed closed trivia (its ask ts)
        arcade: null,            // {game, sel, promo, flip} when the Arcade view is open
        watch: {                 // movie night (reduced from watch.* on the bus)
            searchResults: [],   //   picker modal TMDB results
            searching: false,    //   picker fetch in flight
            pickShow: -1,        //   result index awaiting a season/episode pick
            owned: {},           //   nomination key → true/false (MY library's answer)
            ownedFetching: '',   //   in-flight probe signature (dedupe)
            ownedRetryAt: 0,     //   backoff after a failed/denied probe (ms)
            ownedDenied: false,  //   403 = music-only profile: hide ownership UI
            grabbed: {},         //   keys we already sent to the grab pipeline
            // ── P2: the room itself ──────────────────────────────────────
            joined: '',          //   showing key we're actually PLAYING ('' = not in)
            play: null,          //   {verdict, reasons} from /watch/playable
            playFetching: '',    //   in-flight playability probe signature
            drift: null,         //   interval id: re-anchors the element to the fold
            err: '',             //   the browser refused the file (element error)
            autoJoin: '',        //   showing key to join the moment it goes live
            autoStart: '',       //   nomination to start as soon as the fold sees it
            playAhead: {},       //   key → verdict, probed BEFORE joining
            art: {},             //   key → OUR poster-proxy path (never off the bus)
            vol: 100,            //   local playback volume (persisted, like the jukebox's)
        },
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
        var _wv = parseInt(localStorage.getItem('chat_watch_vol') || '100', 10);
        if (_wv >= 0 && _wv <= 100) state.watch.vol = _wv;
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

    function _classifyUrl(u) {
        var raw = (u || '').replace(/&amp;/g, '&').toLowerCase();
        if (raw.indexOf('youtube.com') > -1 || raw.indexOf('youtu.be') > -1) {
            return { cls: 'chat-link--yt', brand: 'YouTube' };
        }
        if (raw.indexOf('spotify.com') > -1) {
            return { cls: 'chat-link--spotify', brand: 'Spotify' };
        }
        if (raw.indexOf('soundcloud.com') > -1) {
            return { cls: 'chat-link--sc', brand: 'SoundCloud' };
        }
        if (raw.indexOf('bandcamp.com') > -1) {
            return { cls: 'chat-link--bc', brand: 'Bandcamp' };
        }
        if (raw.indexOf('deezer.com') > -1) {
            return { cls: 'chat-link--dz', brand: 'Deezer' };
        }
        if (raw.indexOf('music.apple.com') > -1) {
            return { cls: 'chat-link--apple', brand: 'Apple Music' };
        }
        if (raw.indexOf('github.com') > -1) {
            return { cls: 'chat-link--github', brand: 'GitHub' };
        }
        if (raw.indexOf('wikipedia.org') > -1) {
            return { cls: 'chat-link--wiki', brand: 'Wikipedia' };
        }
        if (raw.indexOf('reddit.com') > -1) {
            return { cls: 'chat-link--reddit', brand: 'Reddit' };
        }
        return { cls: 'chat-link--web', brand: '' };
    }

    function _linkHtml(u) {
        // u is already-escaped text (esc ran first) — safe in attr + label.
        var info = _classifyUrl(u);
        return '<a class="chat-link ' + info.cls + '" href="' + u + '" target="_blank" rel="noopener noreferrer">' + u + '</a>';
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

    function _spotifyEmbedInfo(u) {
        var raw = u.replace(/&amp;/g, '&');
        var m = raw.match(/open\.spotify\.com\/(track|album|playlist|episode)\/([A-Za-z0-9]{15,30})/i);
        return m ? { type: m[1].toLowerCase(), id: m[2] } : null;
    }

    function _deezerEmbedInfo(u) {
        var raw = u.replace(/&amp;/g, '&');
        var m = raw.match(/deezer\.com\/(?:[a-z]{2}\/)?(track|album|playlist)\/([0-9]{3,15})/i);
        return m ? { type: m[1].toLowerCase(), id: m[2] } : null;
    }

    function _soundcloudEmbedInfo(u) {
        var raw = u.replace(/&amp;/g, '&');
        var m = raw.match(/^https?:\/\/(?:www\.)?soundcloud\.com\/([A-Za-z0-9_-]+)\/([A-Za-z0-9_-]+)(?:[?#]|$)/i);
        if (m && m[1] !== 'you' && m[1] !== 'discover' && m[1] !== 'stream' && m[1] !== 'search' && m[1] !== 'upload') {
            return { user: m[1], track: m[2] };
        }
        return null;
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
            return html + ' <button type="button" class="chat-embed-chip chat-embed-chip--yt" data-chat-embed-yt="' +
                yt + '" title="Play YouTube video inline in chat">▶ play</button>' +
                ' <button type="button" class="chat-embed-action chat-embed-action--jbx" data-chat-jbx-add="' +
                yt + '" title="Queue to Room Jukebox">🎵 jukebox</button>';
        }
        var sp = _spotifyEmbedInfo(u);
        if (sp) {
            return html + ' <button type="button" class="chat-embed-chip chat-embed-chip--sp" data-chat-embed-spotify="' +
                sp.type + '/' + sp.id + '" title="Open Spotify player inline">🟢 spotify</button>';
        }
        var dz = _deezerEmbedInfo(u);
        if (dz) {
            return html + ' <button type="button" class="chat-embed-chip chat-embed-chip--dz" data-chat-embed-deezer="' +
                dz.type + '/' + dz.id + '" title="Open Deezer player inline">💜 deezer</button>';
        }
        var sc = _soundcloudEmbedInfo(u);
        if (sc) {
            return html + ' <button type="button" class="chat-embed-chip chat-embed-chip--sc" data-chat-embed-sc="' +
                encodeURIComponent(u) + '" title="Open SoundCloud player inline">☁ soundcloud</button>';
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
            var info = _classifyUrl(url);
            return '<a class="chat-link ' + info.cls + '" href="' + url + '" target="_blank" rel="noopener noreferrer">' +
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
            }).catch(function () {
                return { ok: false, status: 0, body: { error: 'Could not reach SoulSync. Your message was not confirmed; check the conversation before trying again.' } };
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

    // Moderator gate — one list, owned by chat-protocol.js (the reducers
    // enforce it on EVERY client; this only decides which buttons WE see).
    function _selfIsMod() {
        var CP = window.ChatProtocol;
        return !!(CP && CP.isModerator && CP.isModerator(state.selfName));
    }

    function _hiddenSet() {
        var CP = window.ChatProtocol;
        if (!CP || !CP.reduceHidden || state.view !== 'room') return {};
        return CP.reduceHidden(_roomEvents());
    }

    function _lineHtml(m) {
        var self = m.self === true || m.direction === 'Out';
        // Edits: the DISPLAYED text is the latest applied edit; m.message
        // stays the original everywhere a stable identity matters (the react
        // key hashes it, the message key embeds it).
        var versions = m._editOrphan ? null : _editsFor(m);
        var showText = versions ? versions[versions.length - 1] : String(m.message || '');
        // Moderator-hidden messages collapse to a stub on EVERY SoulSync
        // client (the reducer only folds mod.hide from moderators, so the
        // envelope can't be forged). Click reveals locally; a moderator
        // also gets the unhide that lifts it for the whole room.
        var hideKey = String(m.username || '') + '|' + String(m.timestamp || '');
        if (state.view === 'room' && _hiddenSet()[hideKey]) {
            if (!state.revealedHidden || !state.revealedHidden[hideKey]) {
                return '<div class="chat-line chat-line--hidden" data-chat-hidden-reveal="' +
                    attr(hideKey) + '" title="Click to view anyway">' +
                    '🚫 <span>Message from <b>' + esc(m.username || '') +
                    '</b> hidden by a moderator</span>' +
                    (_selfIsMod()
                        ? ' <button type="button" class="chat-line-reply" ' +
                          'data-chat-unhide-user="' + attr(m.username || '') + '" ' +
                          'data-chat-unhide-ts="' + attr(String(m.timestamp || '')) +
                          '">unhide</button>'
                        : '') +
                    '</div>';
            }
        }
        var me = !self && state.view === 'room' && mentionsMe(showText);
        var replyRef = (m.reply && m.reply.u)
            ? '<div class="chat-reply-ref">↩ <b>' + esc(m.reply.u) + '</b> ' +
              '<span>' + esc(m.reply.x || '') + '</span></div>'
            : '';
        var acts = '<button type="button" class="chat-line-reply" title="Copy text" ' +
            'data-chat-copy="' + attr(showText) + '">⧉</button>';
        if (state.view === 'room' && state.canSend && !self) {
            acts = '<button type="button" class="chat-line-reply" title="Browse ' + attr(m.username || '') + '’s files" ' +
                'data-chat-browse-user="' + attr(m.username || '') + '">📁</button>' +
                '<button type="button" class="chat-line-reply" title="React" ' +
                'data-chat-react-user="' + attr(m.username || '') + '" ' +
                'data-chat-react-text="' + attr(String(m.message || '')) + '">🙂+</button>' +   // FULL text — the react key is a hash of it
                '<button type="button" class="chat-line-reply" title="Reply" ' +
                'data-chat-reply-user="' + attr(m.username || '') + '" ' +
                'data-chat-reply-x="' + attr(showText.slice(0, 100)) + '">↩</button>' + acts;
        }
        // Your own SoulSync message, still under the edit cap → offer ✏.
        // File cards are excluded (their text is the link the card dresses),
        // and so are edit carriers themselves.
        if (state.view === 'room' && state.canSend && state.selfName &&
                m.username === state.selfName && m.rich && !m.ed && !m._editOrphan &&
                !(m.file && m.file.n) &&
                (!versions || versions.length < EDIT_MAX)) {
            acts = '<button type="button" class="chat-line-reply" title="Edit' +
                (versions ? ' (1 edit left)' : '') + '" ' +
                'data-chat-edit-key="' + attr(_msgKey(m).slice(0, 160)) + '" ' +
                'data-chat-edit-text="' + attr(showText) + '">✏</button>' + acts;
        }
        if (state.view === 'room' && state.canSend && !state.thread && _chanRoom()) {
            acts += '<button type="button" class="chat-line-reply" title="Start a thread on this message" ' +
                'data-chat-thread-start="' + attr(_msgKey(m)) + '" ' +
                'data-chat-thread-title="' + attr(String(m.message || '').slice(0, 60)) + '">🧵</button>';
        }
        // Pin + hide are MODERATOR tools now (Boulder): the reducers refuse
        // anyone else's events anyway — hiding the buttons just keeps honest
        // UIs honest, exactly like the reserved avatar's picker.
        if (state.view === 'room' && state.canSend && _selfIsMod()) {
            acts += '<button type="button" class="chat-line-reply" title="Pin to the room board" ' +
                'data-chat-pin-user="' + attr(m.username || '') + '" ' +
                'data-chat-pin-ts="' + attr(String(m.timestamp || '')) + '" ' +
                'data-chat-pin-x="' + attr(showText.slice(0, 140)) + '">📌</button>' +
                '<button type="button" class="chat-line-reply" title="Hide for everyone (moderator)" ' +
                'data-chat-hide-user="' + attr(m.username || '') + '" ' +
                'data-chat-hide-ts="' + attr(String(m.timestamp || '')) + '">🚫</button>';
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
        var npData = m.np || _extractNpFromText(m.message);
        var wantData = m.want || _extractWantFromText(m.message);
        var bodyHtml = (npData && npData.t)
            ? _nowPlayingCardHtml(m, npData)
            : (wantData && wantData.t)
                ? _wantedCardHtml(m, wantData)
                : (m.overlay && m.overlay.n)
                    ? _overlayCardHtml(m)
                    : (m.file && m.file.n)
                        ? _fileCardHtml(m)
                        : (m.rich ? renderRich(showText) : renderPlain(showText));
        // An edited message wears the marker; hovering it shows every prior
        // version, oldest first (the history is retained, not replaced).
        if (versions) {
            var history = [String(m.message || '')].concat(versions.slice(0, -1));
            bodyHtml += '<span class="chat-line-edited" title="' +
                attr(history.map(function (v, i) {
                    return (i === 0 ? 'original: ' : 'edit ' + i + ': ') + v;
                }).join('\n')) + '">(edited)</span>';
        }
        // A carrier whose original isn't in the loaded window (or that fell
        // past the edit cap) renders as itself, annotated.
        if (m._editOrphan) {
            bodyHtml = '<span class="chat-line-editnote" title="This is an edit of an ' +
                'earlier message that isn’t loaded here">✏</span> ' + bodyHtml;
        }
        return '<div class="chat-line' + (me ? ' chat-line--me' : '') + '" title="' +
            attr(_fullTs(m.timestamp)) + '">' + replyRef +
            bodyHtml + actions + chips + '</div>';
    }

    // chat.js guards every showToast call (it can load without downloads.js,
    // which defines it) — 45 places do this. One helper rather than a 46th
    // hand-rolled guard.
    function _ovToast(msg, kind) {
        if (typeof showToast === 'function') showToast(msg, kind);
    }

    // Pick one of YOUR templates and send it.
    //
    // A grid of RENDERED previews, not a list of names. Each card is the
    // template composited onto a neutral poster by the same endpoint the
    // Overlay Studio gallery uses — a template is a visual thing, and choosing
    // one by name is choosing blind. (The first version of this was a
    // window.prompt asking for a number, which is exactly as bad as it sounds.)
    function _pickOverlayToShare() {
        toggleAttachPanel(true);
        var ov = q('[data-chat-ovl-modal]');
        var grid = q('[data-chat-ovl-grid]');
        if (!ov || !grid) { _ovToast('The overlay picker is unavailable', 'error'); return; }
        grid.innerHTML = '<div class="chat-ovl-empty">Loading your templates\u2026</div>';
        ov.hidden = false;
        _bindOverlayPickerEsc();
        fetch('/api/video/overlays/templates', { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                var list = (d && d.templates) || [];
                if (!list.length) {
                    grid.innerHTML = '<div class="chat-ovl-empty">You have no overlay templates yet. ' +
                        'Design one in the Overlay Studio and it will show up here.</div>';
                    return;
                }
                grid.innerHTML = list.map(_overlayPickCard).join('');
            })
            .catch(function () {
                grid.innerHTML = '<div class="chat-ovl-empty">Could not load your templates.</div>';
            });
    }

    function _overlayPickCard(t) {
        var layers = Number(t.layer_count || 0);
        return '<button type="button" class="chat-ovl-card" data-chat-ovl-pick="' + attr(t.id) + '" ' +
            'data-chat-ovl-name="' + attr(t.name || 'Overlay template') + '" ' +
            'title="Share ' + attr(t.name || 'this template') + '">' +
            // same preview the studio gallery shows, so what you pick is what
            // they get
            '<span class="chat-ovl-shot">' +
                // thumb 404s if pillow can't render. a broken image icon here
                // looks like the template is broken, so fall back instead
                '<img src="/api/video/overlays/templates/' + attr(t.id) + '/thumb" alt="" loading="lazy" ' +
                    'onerror="this.parentNode.classList.add(\'is-noshot\');this.remove();">' +
            '</span>' +
            '<span class="chat-ovl-meta">' +
                '<span class="chat-ovl-cardname">' + esc(t.name || 'Untitled') + '</span>' +
                '<span class="chat-ovl-cardsub">' + layers +
                    (layers === 1 ? ' layer' : ' layers') +
                    (t.kind && t.kind !== 'poster' ? ' \u00b7 ' + esc(t.kind) : '') +
                '</span>' +
            '</span>' +
        '</button>';
    }

    function _closeOverlayPicker() {
        var ov = q('[data-chat-ovl-modal]');
        if (ov) ov.hidden = true;
        if (_ovlEsc) { document.removeEventListener('keydown', _ovlEsc); _ovlEsc = null; }
    }

    // escape closes it. the other chat modals don't bother but they should.
    // unbind on close, otherwise you stack a listener per open.
    var _ovlEsc = null;
    function _bindOverlayPickerEsc() {
        if (_ovlEsc) return;
        _ovlEsc = function (ev) { if (ev.key === 'Escape') _closeOverlayPicker(); };
        document.addEventListener('keydown', _ovlEsc);
    }

    // The gallery row is a summary — the DEFINITION has to be fetched before it
    // can be sent.
    function _shareOverlayById(id, name) {
        _closeOverlayPicker();
        fetch('/api/video/overlays/templates/' + encodeURIComponent(id),
              { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (full) {
                var defn = full && full.definition;
                if (!defn || !defn.layers || !defn.layers.length) {
                    _ovToast('That template has no layers to share', 'error');
                    return;
                }
                _sendOverlayShare(name || full.name || 'Overlay template', defn);
            })
            .catch(function () { _ovToast('Could not read that template', 'error'); });
    }

    function _sendOverlayShare(name, definition) {
        // Rooms only. A PM is sent as plaintext by design, so an envelope-only
        // share would arrive as nothing at all rather than as a card.
        if (state.view !== 'room') {
            _ovToast('Overlay templates can only be shared in a room', 'info');
            return;
        }
        postJSON('/api/chat/room/message', _tagRoomPayload({
            message: '',
            overlay: { n: name, d: definition },
        })).then(function (res) {
            if (res && res.ok) {
                _ovToast('Shared "' + name + '"', 'success');
                // The composer's own trick: clearing lastStamp forces the next
                // poll to render authoritatively instead of diffing. There is
                // no loadRoom() to call — only loadRooms(), which reloads the
                // room LIST and would not bring the message back any sooner.
                state.lastStamp = null;
                state.stickBottom = true;
            } else {
                // The server names the half that did not fit - with a template
                // attached, "message too long" would send someone to shorten a
                // sentence that was already empty.
                _ovToast((res && res.body && res.body.error) ||
                          'Could not share that template', 'error');
            }
        });
    }

    // Definitions of the templates currently on screen, so the Add button can
    // carry a short key instead of a few KB of JSON in an attribute.
    var _overlayShares = { n: 0, map: {}, order: [] };
    var OVERLAY_SHARE_KEEP = 40;

    function _rememberOverlayShare(share) {
        var key = 'ov' + (_overlayShares.n++);
        _overlayShares.map[key] = share;
        _overlayShares.order.push(key);
        // Bounded on purpose. Each definition is a few KB and a busy room would
        // otherwise hold every template ever scrolled past for the life of the
        // tab. Forty covers everything on screen and then some.
        while (_overlayShares.order.length > OVERLAY_SHARE_KEEP) {
            delete _overlayShares.map[_overlayShares.order.shift()];
        }
        return key;
    }

    // Adopt a template someone shared. The definition is already on the
    // message (it rode the envelope), so this is one POST and no fetching.
    function _adoptSharedOverlay(btn) {
        var o = _overlayShares.map[btn.getAttribute('data-chat-overlay-add')];
        if (!o || !o.d) { _ovToast('That template is no longer on screen', 'error'); return; }
        btn.disabled = true;
        var was = btn.textContent;
        btn.textContent = 'Adding\u2026';
        fetch('/api/video/overlays/templates/from-share', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
            body: JSON.stringify({ name: o.n, definition: o.d })
        })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (!d || !d.ok) {
                    btn.disabled = false; btn.textContent = was;
                    _ovToast((d && d.error) || 'Could not add that template', 'error');
                    return;
                }
                btn.textContent = '\u2713 added';
                var miss = (d.missing_assets || []).length;
                // The missing half is said AFTER the success, not instead of
                // it: the template really was added, it just has holes the
                // sender has to send you separately.
                _ovToast(miss
                    ? 'Added "' + d.name + '" \u2014 ' + miss +
                      (miss === 1 ? ' image is' : ' images are') +
                      ' missing, ask the sender for them'
                    : 'Added "' + d.name + '" to your overlays',
                    miss ? 'info' : 'success');
            })
            .catch(function () {
                btn.disabled = false; btn.textContent = was;
                _ovToast('Could not add that template', 'error');
            });
    }

    // ── shared overlay template (envelope 'o') ──────────────────────────
    //
    // A design, not a link: the whole template rides the message, so the card
    // can say what it IS before anyone commits to adopting it — how many layers,
    // and crucially how many images it depends on that this install does not
    // have. Those refs are content-addressed (asset://<sha1>), so an image you
    // already uploaded resolves for free and anything else is named exactly.
    function _overlayCardHtml(m) {
        var o = m.overlay || {};
        var layers = Number(o.layers || 0);
        var assets = (o.assets || []).length;
        // The definition is far too large for a data- attribute, so it goes in
        // a render-scoped registry and only the KEY rides the button. The file
        // card can put its whole payload (a url) on the element; a template
        // cannot, and inventing a lookup that does not exist is how the last
        // card like this ended up doing nothing when clicked.
        var key = _rememberOverlayShare({ n: o.n, d: o.d });
        return '<div class="chat-overlay-card">' +
            '<span class="chat-overlay-icon">\u25F0</span>' +
            '<span class="chat-overlay-meta">' +
                '<b class="chat-overlay-name">' + esc(o.n || 'Overlay template') + '</b>' +
                '<span class="chat-overlay-sub">Overlay template \u00b7 ' +
                    layers + (layers === 1 ? ' layer' : ' layers') + '</span>' +
                // Said up front, not after the click: adopting a template whose
                // art you lack leaves layers that paint nothing, and finding
                // that out afterwards feels like a broken import.
                (assets ? '<span class="chat-overlay-warn">Needs ' + assets +
                          (assets === 1 ? ' image' : ' images') +
                          ' you may not have</span>' : '') +
            '</span>' +
            '<button type="button" class="chat-embed-chip chat-overlay-add" ' +
                'data-chat-overlay-add="' + key + '">\u2795 add to my overlays</button>' +
        '</div>';
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

    // ── Now Playing & Wanted / ISO Cards ────────────────────────────────────
    function _extractNpFromText(text) {
        if (!text || typeof text !== 'string') return null;
        var m = text.match(/🎵\s*Now Playing:\s*([^\-]+?)\s*-\s*([^(]+?)(?:\s*\(([^)]+)\))?$/i);
        if (!m) return null;
        return { a: m[1].trim(), t: m[2].trim(), al: (m[3] || '').trim(), src: 'shared' };
    }

    function _extractWantFromText(text) {
        if (!text || typeof text !== 'string') return null;
        var m = text.match(/🔍\s*In Search Of:\s*(?:\[(ALBUM|TRACK|EP|SINGLE)\]\s*)?([^\-]+?)\s*-\s*([^(]+?)(?:\s*\(([^)]+)\))?$/i);
        if (!m) return null;
        var ty = (m[1] || 'album').toLowerCase();
        return { a: m[2].trim(), t: m[3].trim(), y: (m[4] || '').trim(), ty: ty, src: 'shared' };
    }

    var _npArtCache = {};
    var _npArtPending = {};

    function _scheduleNpArtProbe(title, artist, isAlbum) {
        if (!title && !artist) return;
        var key = (artist || '').toLowerCase().trim() + '|||' + (title || '').toLowerCase().trim();
        if (key in _npArtCache || _npArtPending[key]) return;
        _npArtPending[key] = true;

        var qStr = (artist && artist !== 'Unknown Artist' ? artist + ' ' : '') + (title && title !== 'Unknown Track' ? title : '');
        qStr = qStr.trim();
        if (!qStr) {
            delete _npArtPending[key];
            return;
        }

        fetch('/api/enhanced-search', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query: qStr })
        })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
            delete _npArtPending[key];
            if (!data) return;
            var trs = data.tracks || data.spotify_tracks || [];
            var als = data.albums || data.spotify_albums || [];
            var candidates = isAlbum ? als.concat(trs) : trs.concat(als);
            var hit = candidates.find(function (c) {
                return c && (c.image_url || (c.images && c.images[0] && (typeof c.images[0] === 'string' ? c.images[0] : c.images[0].url)));
            }) || candidates[0];

            if (hit) {
                var imgUrl = hit.image_url || (hit.images && hit.images[0] && (typeof hit.images[0] === 'string' ? hit.images[0] : hit.images[0].url)) || '';
                if (imgUrl) {
                    _npArtCache[key] = imgUrl;
                    _applyNpArtToDom(key, imgUrl, hit);
                }
            }
        })
        .catch(function () {
            delete _npArtPending[key];
        });
    }

    function _applyNpArtToDom(key, imgUrl, hit) {
        document.querySelectorAll('.chat-np-card').forEach(function (card) {
            var cTitle = (card.getAttribute('data-np-title') || '').toLowerCase().trim();
            var cArtist = (card.getAttribute('data-np-artist') || '').toLowerCase().trim();
            var cKey = cArtist + '|||' + cTitle;
            if (cKey === key) {
                card.setAttribute('data-np-img', imgUrl);
                var ph = card.querySelector('.chat-np-thumb--ph');
                if (ph) {
                    var img = document.createElement('img');
                    img.className = 'chat-np-thumb';
                    img.src = imgUrl;
                    img.alt = '';
                    img.loading = 'lazy';
                    ph.replaceWith(img);
                }
            }
        });
        document.querySelectorAll('.chat-wanted-card').forEach(function (card) {
            var cTitle = (card.getAttribute('data-wanted-title') || '').toLowerCase().trim();
            var cArtist = (card.getAttribute('data-wanted-artist') || '').toLowerCase().trim();
            var cKey = cArtist + '|||' + cTitle;
            if (cKey === key) {
                card.setAttribute('data-wanted-img', imgUrl);
                if (hit) {
                    if (hit.id && !card.getAttribute('data-wanted-id')) card.setAttribute('data-wanted-id', hit.id);
                    if (hit.source && (!card.getAttribute('data-wanted-src') || card.getAttribute('data-wanted-src') === 'shared')) {
                        card.setAttribute('data-wanted-src', hit.source);
                    }
                    if (hit.album && !card.getAttribute('data-wanted-album')) card.setAttribute('data-wanted-album', hit.album);
                }
                var ph = card.querySelector('.chat-wanted-thumb--ph');
                if (ph) {
                    var img = document.createElement('img');
                    img.className = 'chat-wanted-thumb';
                    img.src = imgUrl;
                    img.alt = '';
                    img.loading = 'lazy';
                    ph.replaceWith(img);
                }
            }
        });
    }

    function _nowPlayingCardHtml(m, npOverride) {
        var np = npOverride || m.np || _extractNpFromText(m.message);
        if (!np) return m.rich ? renderRich(m.message) : renderPlain(m.message);
        var t = esc(np.t || 'Unknown Track');
        var a = esc(np.a || 'Unknown Artist');
        var al = esc(np.al || '');
        var src = esc((np.src || 'MUSIC').toUpperCase());
        var br = np.br ? '<span class="chat-np-badge chat-np-badge--br">' + esc(np.br) + 'k</span>' : '';
        var durBadge = '';
        if (np.dur && Number(np.dur) > 0) {
            var rawD = Number(np.dur);
            var secTotal = Math.round(rawD > 1000 ? rawD / 1000 : rawD);
            var dMin = Math.floor(secTotal / 60);
            var dSec = Math.floor(secTotal % 60);
            durBadge = '<span class="chat-np-badge chat-np-badge--dur">' + dMin + ':' + (dSec < 10 ? '0' : '') + dSec + '</span>';
        }

        var probeKey = (np.a || '').toLowerCase().trim() + '|||' + (np.t || '').toLowerCase().trim();
        var resolvedImg = np.img || _npArtCache[probeKey] || '';
        var img = (resolvedImg && (/^https?:\/\//.test(resolvedImg) || /^\/api\//.test(resolvedImg)))
            ? '<img class="chat-np-thumb" src="' + attr(resolvedImg) + '" alt="" loading="lazy">'
            : '<div class="chat-np-thumb chat-np-thumb--ph">🎵</div>';
        if (!resolvedImg && (np.t || np.a)) {
            _scheduleNpArtProbe(np.t, np.a);
        }

        return '<div class="chat-np-card" data-np-title="' + attr(np.t) + '" data-np-artist="' + attr(np.a) + '" data-np-album="' + attr(np.al || '') + '" data-np-id="' + attr(np.id || '') + '" data-np-src="' + attr(np.src || '') + '" data-np-dur="' + attr(np.dur || 0) + '" data-np-img="' + attr(resolvedImg || np.img || '') + '" data-np-type="track">' +
            '<div class="chat-np-header">' +
                '<span class="chat-np-tag"><span class="chat-np-eq"><i></i><i></i><i></i></span> NOW PLAYING</span>' +
                '<span class="chat-np-src-pill">' + src + '</span>' +
                br +
                durBadge +
            '</div>' +
            '<div class="chat-np-body">' +
                img +
                '<div class="chat-np-meta">' +
                    '<div class="chat-np-title" title="' + attr(np.t) + '">' + t + '</div>' +
                    '<div class="chat-np-artist" title="' + attr(np.a) + '">' + a + '</div>' +
                    (al ? '<div class="chat-np-album" title="' + attr(np.al) + '">' + al + '</div>' : '') +
                '</div>' +
            '</div>' +
            '<div class="chat-np-actions">' +
                '<button type="button" class="chat-card-btn chat-card-btn--primary" data-chat-card-stream title="Preview audio stream">▶ Preview</button>' +
                '<button type="button" class="chat-card-btn chat-card-btn--accent" data-chat-card-dl title="Open Download Tracks modal">📥 Download</button>' +
                '<button type="button" class="chat-card-btn chat-card-btn--wishlist" data-chat-card-wishlist title="Add to your Wishlist">➕ Wishlist</button>' +
                '<button type="button" class="chat-card-btn" data-chat-card-artist title="Go to ' + attr(np.a) + ' artist page">👤 Artist</button>' +
                '<button type="button" class="chat-card-btn" data-chat-card-search title="Search SoulSync for this release">🔍 Search</button>' +
            '</div>' +
        '</div>';
    }

    var _wantedPresenceCache = {};
    var _wantedPresencePending = {};

    function _scheduleWantedPresenceProbe(w) {
        var key = (w.a || '').toLowerCase().trim() + '|||' + (w.t || '').toLowerCase().trim();
        if (key in _wantedPresenceCache || _wantedPresencePending[key]) return;
        _wantedPresencePending[key] = true;

        var reqData = {
            albums: (w.ty === 'album' || w.ty === 'ep') ? [{ title: w.t, artist: w.a }] : [],
            tracks: (w.ty === 'track' || w.ty === 'single') ? [{ name: w.t, artist: w.a, album: w.al || '' }] : []
        };
        if (!reqData.albums.length && !reqData.tracks.length) {
            reqData.albums = [{ title: w.t, artist: w.a }];
        }

        fetch('/api/enhanced-search/library-check', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(reqData)
        })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            delete _wantedPresencePending[key];
            var owned = false;
            if (data && data.albums && data.albums[0] === true) owned = true;
            if (data && data.tracks && data.tracks[0] && data.tracks[0].in_library === true) owned = true;
            _wantedPresenceCache[key] = owned;
            document.querySelectorAll('[data-chat-wanted-probe="' + attr(key) + '"]').forEach(function (el) {
                el.className = 'chat-wanted-badge ' + (owned ? 'chat-wanted-badge--owned' : 'chat-wanted-badge--miss');
                el.textContent = owned ? '✓ In Your Library' : 'Not in library';
                if (owned) {
                    var card = el.closest('.chat-wanted-card');
                    if (card) {
                        var btn = card.querySelector('[data-chat-wanted-have]');
                        if (btn) btn.classList.add('chat-card-btn--glow');
                    }
                }
            });
        })
        .catch(function () { delete _wantedPresencePending[key]; });
    }

    function _wantedCardHtml(m, wantOverride) {
        var w = wantOverride || m.want || _extractWantFromText(m.message);
        if (!w) return m.rich ? renderRich(m.message) : renderPlain(m.message);
        var t = esc(w.t || 'Unknown Title');
        var a = esc(w.a || 'Unknown Artist');
        var ty = esc((w.ty || 'album').toUpperCase());
        var y = w.y ? esc(w.y) : '';
        var src = esc(w.src || '');
        var requester = m.username || 'user';
        var isMe = (m.username === state.selfName || m.self === true || requester === 'you');

        var cacheKey = (w.a || '').toLowerCase().trim() + '|||' + (w.t || '').toLowerCase().trim();
        var inLib = _wantedPresenceCache[cacheKey];

        var probeKey = (w.a || '').toLowerCase().trim() + '|||' + (w.t || '').toLowerCase().trim();
        var resolvedImg = w.img || _npArtCache[probeKey] || '';
        var img = (resolvedImg && (/^https?:\/\//.test(resolvedImg) || /^\/api\//.test(resolvedImg)))
            ? '<img class="chat-wanted-thumb" src="' + attr(resolvedImg) + '" alt="" loading="lazy">'
            : '<div class="chat-wanted-thumb chat-wanted-thumb--ph">💿</div>';
        if (!resolvedImg && (w.t || w.a)) {
            _scheduleNpArtProbe(w.t, w.a, (w.ty === 'album' || w.ty === 'ep'));
        }

        var statusChip = '';
        if (inLib === true) {
            statusChip = '<span class="chat-wanted-badge chat-wanted-badge--owned">✓ In Your Library</span>';
        } else if (inLib === false) {
            statusChip = '<span class="chat-wanted-badge chat-wanted-badge--miss">Not in library</span>';
        } else {
            statusChip = '<span class="chat-wanted-badge chat-wanted-badge--checking" data-chat-wanted-probe="' + attr(cacheKey) + '">Checking library…</span>';
            _scheduleWantedPresenceProbe(w);
        }

        var haveBtn = '';
        if (!isMe) {
            haveBtn = '<button type="button" class="chat-card-btn chat-card-btn--success' + (inLib === true ? ' chat-card-btn--glow' : '') + '" ' +
                'data-chat-wanted-have data-wanted-user="' + attr(requester) + '" data-wanted-title="' + attr(w.t) + '" data-wanted-artist="' + attr(w.a) + '" ' +
                'title="Send @' + attr(requester) + ' a direct message saying you have this!">' +
                '💬 I Have This! (Send PM)</button>';
        }

        return '<div class="chat-wanted-card" ' +
            'data-wanted-title="' + attr(w.t) + '" ' +
            'data-wanted-artist="' + attr(w.a) + '" ' +
            'data-wanted-album="' + attr(w.al || '') + '" ' +
            'data-wanted-type="' + attr(w.ty || 'album') + '" ' +
            'data-wanted-id="' + attr(w.id || '') + '" ' +
            'data-wanted-src="' + attr(w.src || '') + '" ' +
            'data-wanted-img="' + attr(resolvedImg || w.img || '') + '" ' +
            'data-wanted-year="' + attr(w.y || '') + '" ' +
            'data-wanted-dur="' + attr(w.dur || 0) + '" ' +
            'data-wanted-key="' + attr(cacheKey) + '">' +
            '<div class="chat-wanted-header">' +
                '<span class="chat-wanted-tag">🔍 IN SEARCH OF</span>' +
                '<span class="chat-wanted-type-pill">' + ty + '</span>' +
                (src ? '<span class="chat-wanted-src-pill">' + src.toUpperCase() + '</span>' : '') +
                statusChip +
            '</div>' +
            '<div class="chat-wanted-body">' +
                img +
                '<div class="chat-wanted-meta">' +
                    '<div class="chat-wanted-title" title="' + attr(w.t) + '">' + t + '</div>' +
                    '<div class="chat-wanted-artist" title="' + attr(w.a) + '">' + a + (y ? ' <span class="chat-wanted-year">(' + y + ')</span>' : '') + '</div>' +
                    '<div class="chat-wanted-req">Requested by <b>@' + esc(requester) + '</b></div>' +
                '</div>' +
            '</div>' +
            '<div class="chat-wanted-actions">' +
                haveBtn +
                '<button type="button" class="chat-card-btn chat-card-btn--accent" data-chat-card-dl title="Open Download Tracks modal">📥 Download</button>' +
                '<button type="button" class="chat-card-btn chat-card-btn--wishlist" data-chat-wanted-wishlist title="Add to your Wishlist">➕ Wishlist</button>' +
                '<button type="button" class="chat-card-btn" data-chat-card-artist title="Go to ' + attr(w.a) + ' artist page">👤 Artist</button>' +
                '<button type="button" class="chat-card-btn" data-chat-card-search title="Search SoulSync for this release">🔍 Search</button>' +
            '</div>' +
        '</div>';
    }

    async function shareNowPlaying() {
        if (state.view !== 'room' || _plainOn() || !state.canSend) {
            if (typeof showToast === 'function') {
                showToast('🎵 Now Playing cards can only be shared in SoulSync rooms.', 'warning');
            }
            return;
        }
        var cur = window.__ssCurrentTrack || (typeof window.getCurrentTrack === 'function' ? window.getCurrentTrack() : null) || state.localNowPlaying;
        if (!cur || (!cur.title && !cur.name)) {
            if (typeof showToast === 'function') {
                showToast('🎵 Nothing is currently playing in SoulSync. Start a track first!', 'info');
            }
            return;
        }
        var t = String(cur.title || cur.name || 'Unknown Track').replace(/^[a-z0-9_-]+\|\|/i, '');
        var a = String(cur.artist || 'Unknown Artist').replace(/^[a-z0-9_-]+\|\|/i, '');
        var al = String(cur.album || '').replace(/^[a-z0-9_-]+\|\|/i, '');
        var src = cur.source || (cur.is_library ? 'library' : 'stream');
        var id = String(cur.id || '');
        var img = String(cur.image_url || cur.album_cover_url || cur.thumb_url || '');
        if (!img) {
            var artEl = document.getElementById('sidebar-album-art') || document.getElementById('np-album-art');
            if (artEl && artEl.src && !artEl.src.endsWith('/trans2.png') && !artEl.src.endsWith('/default-artwork.png')) {
                img = artEl.src;
            }
        }
        if (img && !/^https?:\/\//.test(img) && !/^\/api\//.test(img)) img = '';

        var dur = 0;
        if (cur.duration_ms && Number(cur.duration_ms) > 0) {
            dur = Math.round(Number(cur.duration_ms));
        } else if (cur.duration && Number(cur.duration) > 0) {
            var rawD = Number(cur.duration);
            dur = rawD > 1000 ? Math.round(rawD) : Math.round(rawD * 1000);
        }
        if (!dur) {
            var apEl = document.getElementById('audio-player');
            if (apEl && isFinite(apEl.duration) && apEl.duration > 0) {
                dur = Math.round(apEl.duration * 1000);
            }
        }
        var br = cur.bitrate || 0;

        var probeKey = (a || '').toLowerCase().trim() + '|||' + (t || '').toLowerCase().trim();
        if (!img && _npArtCache[probeKey]) {
            img = _npArtCache[probeKey];
        }

        if (!img && (t || a)) {
            try {
                var searchQ = (a && a !== 'Unknown Artist' ? a + ' ' : '') + (t && t !== 'Unknown Track' ? t : '');
                if (searchQ.trim()) {
                    var searchRes = await fetch('/api/enhanced-search', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ query: searchQ.trim() })
                    }).then(function (r) { return r.ok ? r.json() : null; });
                    if (searchRes) {
                        var candTracks = searchRes.tracks || searchRes.spotify_tracks || [];
                        var candAlbums = searchRes.albums || searchRes.spotify_albums || [];
                        var best = candTracks[0] || candAlbums[0];
                        if (best) {
                            if (best.image_url) {
                                img = best.image_url;
                                _npArtCache[probeKey] = img;
                            }
                            if (!dur && best.duration_ms) dur = Math.round(best.duration_ms);
                            if (!al && best.album) al = best.album;
                            if (cur && typeof cur === 'object') {
                                if (img && !cur.image_url) cur.image_url = img;
                                if (dur && !cur.duration_ms) cur.duration_ms = dur;
                                if (al && !cur.album) cur.album = al;
                            }
                        }
                    }
                }
            } catch (err) {
                console.warn('[Chat] Failed to probe art before sharing now playing:', err);
            }
        }

        var fallbackText = '🎵 Now Playing: ' + a + ' - ' + t + (al ? ' (' + al + ')' : '');
        var payload = {
            message: fallbackText,
            np: { t: t, a: a, al: al, src: src, id: id, img: img, dur: dur, br: br }
        };
        _tagRoomPayload(payload);
        postJSON('/api/chat/room/message', payload).then(function (res) {
            if (res.ok) {
                refresh();
                if (typeof showToast === 'function') showToast('🎵 Shared Now Playing to chat!', 'success');
            } else if (typeof showToast === 'function') {
                showToast(res.body && res.body.error || 'Failed to share Now Playing', 'error');
            }
        });
    }

    var _wantSearchTimer = null;
    var _wantActiveSource = 'auto';

    function openWantedModal(query) {
        if (state.view !== 'room' || _plainOn() || !state.canSend) {
            if (typeof showToast === 'function') {
                showToast('🔍 Wanted cards can only be posted in SoulSync rooms.', 'warning');
            }
            return;
        }
        var modal = q('[data-chat-want-modal]');
        var backdrop = q('[data-chat-want-backdrop]');
        if (!modal) return;
        modal.hidden = false;
        if (backdrop) backdrop.hidden = false;
        requestAnimationFrame(function () {
            modal.classList.add('visible');
            if (backdrop) backdrop.classList.add('visible');
        });
        var inp = q('[data-chat-want-input]');
        if (inp) {
            if (query) inp.value = query;
            setTimeout(function () { inp.focus(); }, 60);
            if (query) searchWanted();
        }
    }

    function closeWantedModal() {
        var modal = q('[data-chat-want-modal]');
        var backdrop = q('[data-chat-want-backdrop]');
        if (modal) {
            modal.classList.remove('visible');
            setTimeout(function () { modal.hidden = true; }, 320);
        }
        if (backdrop) {
            backdrop.classList.remove('visible');
            setTimeout(function () { backdrop.hidden = true; }, 320);
        }
    }

    function searchWanted() {
        var inp = q('[data-chat-want-input]');
        var resultsEl = q('[data-chat-want-results]');
        var statusEl = q('[data-chat-want-status]');
        var clearBtn = q('[data-chat-want-clear]');
        if (!inp || !resultsEl) return;

        var query = (inp.value || '').trim();
        if (clearBtn) clearBtn.hidden = !query;

        if (query.length < 2) {
            resultsEl.innerHTML = '';
            if (statusEl) {
                statusEl.hidden = false;
                statusEl.textContent = 'Type 2+ letters to search across metadata providers';
            }
            return;
        }

        if (statusEl) {
            statusEl.hidden = false;
            statusEl.textContent = 'Searching metadata providers…';
        }

        var fetchFn = (typeof enhancedSearchFetch === 'function')
            ? enhancedSearchFetch
            : function (q, opt) {
                var b = { query: q };
                if (opt && opt.source && opt.source !== 'auto') b.source = opt.source;
                return fetch('/api/enhanced-search', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(b)
                }).then(function (r) { return r.json(); });
            };

        fetchFn(query, { source: _wantActiveSource })
            .then(function (data) {
                if (!data) {
                    if (statusEl) statusEl.textContent = 'No results returned';
                    return;
                }
                var items = [];
                var primarySrc = data.metadata_source || data.primary_source || 'spotify';
                var albums = data.albums || data.spotify_albums || data.results || [];
                albums.slice(0, 15).forEach(function (al) {
                    var albumImg = al.image_url ||
                        (al.images && al.images[0] && (typeof al.images[0] === 'string' ? al.images[0] : al.images[0].url)) ||
                        al.cover_xl || al.cover_big || al.cover_medium || '';
                    items.push({
                        name: al.name || al.title || '',
                        artist: al.artist || (al.artists && al.artists[0] && al.artists[0].name) || '',
                        type: al.album_type || 'album',
                        year: al.release_date ? al.release_date.slice(0, 4) : (al.year || ''),
                        source: al.source || primarySrc,
                        id: String(al.id || ''),
                        image_url: albumImg
                    });
                });
                var tracks = data.tracks || data.spotify_tracks || [];
                tracks.slice(0, 15).forEach(function (tr) {
                    var trImg = tr.image_url ||
                        (tr.images && tr.images[0] && (typeof tr.images[0] === 'string' ? tr.images[0] : tr.images[0].url)) ||
                        (tr.album && tr.album.images && tr.album.images[0] && (typeof tr.album.images[0] === 'string' ? tr.album.images[0] : tr.album.images[0].url)) ||
                        '';
                    items.push({
                        name: tr.name || tr.title || '',
                        artist: tr.artist || (tr.artists && tr.artists[0] && tr.artists[0].name) || '',
                        album: (typeof tr.album === 'string' ? tr.album : (tr.album && tr.album.name)) || '',
                        type: 'track',
                        year: tr.release_date ? tr.release_date.slice(0, 4) : '',
                        source: tr.source || primarySrc,
                        id: String(tr.id || ''),
                        image_url: trImg,
                        duration_ms: tr.duration_ms || 0
                    });
                });

                if (!items.length) {
                    if (statusEl) statusEl.textContent = 'No releases found for "' + query + '"';
                    resultsEl.innerHTML = '';
                    return;
                }

                if (statusEl) statusEl.hidden = true;
                resultsEl.innerHTML = items.map(function (item, idx) {
                    var thumb = item.image_url
                        ? '<img class="chat-want-res-img" src="' + attr(item.image_url) + '" alt="" loading="lazy">'
                        : '<div class="chat-want-res-img chat-want-res-img--ph">' + (item.type === 'track' ? '🎵' : '💿') + '</div>';
                    return '<div class="chat-want-res-row">' +
                        thumb +
                        '<div class="chat-want-res-meta">' +
                            '<div class="chat-want-res-name">' + esc(item.name) + '</div>' +
                            '<div class="chat-want-res-sub">' + esc(item.artist) + (item.year ? ' · ' + esc(item.year) : '') + '</div>' +
                            '<div class="chat-want-res-badges">' +
                                '<span class="chat-want-res-pill">' + esc(item.type.toUpperCase()) + '</span>' +
                                '<span class="chat-want-res-pill chat-want-res-pill--src">' + esc(item.source.toUpperCase()) + '</span>' +
                            '</div>' +
                        '</div>' +
                        '<button type="button" class="chat-card-btn chat-card-btn--primary" data-chat-want-post-idx="' + idx + '">⭐ Post to Chat</button>' +
                    '</div>';
                }).join('');

                resultsEl._items = items;
            })
            .catch(function (err) {
                if (statusEl) statusEl.textContent = 'Search failed: ' + (err.message || err);
            });
    }

    function postWantedCard(item) {
        if (!item || !state.canSend || state.view !== 'room' || _plainOn()) return;
        var t = item.name || item.title || 'Unknown Title';
        var a = item.artist || (item.artists && item.artists[0] && item.artists[0].name) || 'Unknown Artist';
        var ty = item.type || (item.album_type ? 'album' : 'track');
        var al = item.album || '';
        var src = item.source || 'spotify';
        var id = String(item.id || '');
        var img = item.image_url || '';
        if (img && !/^https?:\/\//.test(img) && !/^\/api\//.test(img)) img = '';
        var y = String(item.year || item.release_date || '').slice(0, 4);
        var dur = item.duration_ms || 0;

        // Cache art immediately so local rendering never flickers
        var key = a.toLowerCase().trim() + '|||' + t.toLowerCase().trim();
        if (img) _npArtCache[key] = img;

        var fallbackText = '🔍 In Search Of: [' + ty.toUpperCase() + '] ' + a + ' - ' + t + (y ? ' (' + y + ')' : '');
        var wantObj = { t: t, a: a, ty: ty, al: al, src: src, id: id, img: img, y: y };
        if (dur) wantObj.dur = dur;

        var payload = {
            message: fallbackText,
            want: wantObj
        };
        _tagRoomPayload(payload);
        postJSON('/api/chat/room/message', payload).then(function (res) {
            closeWantedModal();
            if (res.ok) {
                refresh();
                if (typeof showToast === 'function') showToast('⭐ Posted Wanted card to chat!', 'success');
            } else if (typeof showToast === 'function') {
                showToast(res.body && res.body.error || 'Failed to post card', 'error');
            }
        });
    }

    async function _openDownloadForCard(artist, title, album, id, src, durationMs, imageUrl, cardType) {
        if (typeof window.openDownloadMissingModalForArtistAlbum !== 'function') {
            if (typeof showToast === 'function') showToast('Download missing modal unavailable', 'error');
            return;
        }

        var albumName = album || title;
        var isAlbum = (cardType === 'album' || cardType === 'ep');
        durationMs = Number(durationMs) || 0;
        if (durationMs > 0 && durationMs < 1000) {
            durationMs = Math.round(durationMs * 1000);
        }

        // 1. Check active audio player if duration is missing
        if (!durationMs) {
            var cur = (typeof window.getCurrentTrack === 'function' && window.getCurrentTrack())
                || window.__ssCurrentTrack
                || (typeof currentTrack !== 'undefined' ? currentTrack : null);
            if (cur && (cur.title === title || cur.name === title || !title)) {
                if (cur.duration_ms) durationMs = Math.round(Number(cur.duration_ms));
                else if (cur.duration) {
                    var rd = Number(cur.duration);
                    durationMs = rd > 1000 ? Math.round(rd) : Math.round(rd * 1000);
                }
                if (!imageUrl) imageUrl = cur.image_url || cur.album_cover_url || cur.thumb_url || '';
            }
            if (!durationMs) {
                var ap = document.getElementById('audio-player');
                if (ap && isFinite(ap.duration) && ap.duration > 0) {
                    durationMs = Math.round(ap.duration * 1000);
                }
            }
        }

        // 2. If duration, image or id is still missing, query enhanced search
        if ((!durationMs || !imageUrl || !id) && (artist || title)) {
            try {
                var searchQ = (artist ? artist + ' ' : '') + (title || '');
                var searchRes = await fetch('/api/enhanced-search', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ query: searchQ.trim() })
                });
                if (searchRes.ok) {
                    var sData = await searchRes.json();
                    var tracksList = sData.spotify_tracks || sData.tracks || [];
                    var albumsList = sData.spotify_albums || sData.albums || [];
                    if (isAlbum && albumsList.length > 0) {
                        var matchAl = albumsList.find(function (al) {
                            return al.name && title && al.name.toLowerCase() === title.toLowerCase();
                        }) || albumsList[0];
                        if (matchAl) {
                            if (!imageUrl && matchAl.image_url) imageUrl = matchAl.image_url;
                            if ((!albumName || albumName === title) && matchAl.name) albumName = matchAl.name;
                            if (!id && matchAl.id) id = String(matchAl.id);
                            if (!src && matchAl.source) src = matchAl.source;
                        }
                    } else if (tracksList.length > 0) {
                        var match = tracksList.find(function (tr) {
                            return tr.name && title && tr.name.toLowerCase() === title.toLowerCase();
                        }) || tracksList[0];
                        if (match) {
                            if (!durationMs && match.duration_ms) durationMs = match.duration_ms;
                            if (!imageUrl && match.image_url) imageUrl = match.image_url;
                            if ((!albumName || albumName === title) && match.album) albumName = match.album;
                            if (!id && match.id) id = String(match.id);
                            if (!src && match.source) src = match.source;
                        }
                    }
                }
            } catch (err) {
                console.debug('Enhanced search lookup for download modal failed:', err);
            }
        }

        var virtualId = (isAlbum ? 'album_' : 'np_') + Date.now() + '_' + Math.random().toString(36).slice(2, 7);
        var imagesArray = imageUrl ? [{ url: imageUrl }] : [];

        var spotifyTracks = [{
            id: id || virtualId,
            name: title,
            artist: artist,
            artists: [{ name: artist, id: null }],
            album: {
                name: albumName,
                id: id || null,
                album_type: isAlbum ? (cardType || 'album') : 'single',
                images: imagesArray,
                total_tracks: 1
            },
            source: src || 'spotify',
            duration_ms: durationMs || 0,
            image_url: imageUrl || null,
            total_tracks: 1
        }];

        var albumObj = {
            id: id || null,
            name: albumName,
            album_type: isAlbum ? (cardType || 'album') : 'single',
            images: imagesArray,
            artists: [{ name: artist }]
        };

        var artistObj = {
            id: null,
            name: artist,
            source: src || 'spotify'
        };

        // If it's an album and we have an ID and source, fetch the full album tracks
        if (isAlbum && id && src && src !== 'shared') {
            try {
                var albRes = await fetch('/api/spotify/album/' + encodeURIComponent(id) + '?source=' + encodeURIComponent(src) + '&name=' + encodeURIComponent(albumName) + '&artist=' + encodeURIComponent(artist));
                if (albRes.ok) {
                    var albData = await albRes.json();
                    if (albData && albData.tracks && albData.tracks.length > 0) {
                        spotifyTracks = albData.tracks.map(function (tr, idx) {
                            return {
                                id: tr.id || (virtualId + '_' + idx),
                                name: tr.name || tr.title || ('Track ' + (idx + 1)),
                                artist: artist,
                                artists: tr.artists || [{ name: artist, id: null }],
                                album: albumObj,
                                track_number: tr.track_number || (idx + 1),
                                disc_number: tr.disc_number || 1,
                                duration_ms: tr.duration_ms || 0,
                                source: src,
                                image_url: imageUrl || null
                            };
                        });
                        albumObj.total_tracks = spotifyTracks.length;
                        if (albData.images && albData.images.length) {
                            albumObj.images = albData.images;
                        }
                    }
                }
            } catch (err) {
                console.debug('[Chat] Album tracks fetch for download failed:', err);
            }
        }

        window.openDownloadMissingModalForArtistAlbum(
            virtualId,
            '[' + artist + '] ' + albumName,
            spotifyTracks,
            albumObj,
            artistObj,
            false
        );
    }

    function _openArtistPage(artist, artist_id, src) {
        if (artist_id && typeof navigateToArtistDetail === 'function') {
            navigateToArtistDetail(artist_id, artist, src);
            return;
        }
        if (typeof _navigateToArtistFromWishlist === 'function') {
            _navigateToArtistFromWishlist(artist);
            return;
        }
        _searchFromCard(artist, '');
    }

    function _searchFromCard(artist, title) {
        var qstr = ((artist ? artist + ' ' : '') + (title || '')).trim();
        if (typeof navigateToPage === 'function') {
            navigateToPage('search');
            setTimeout(function () {
                // 'search-input' is a class, not an id; the react search bar is the only input
                var inp = document.getElementById('enhanced-search-input');
                if (inp) {
                    inp.value = qstr;
                    inp.dispatchEvent(new Event('input', { bubbles: true }));
                    inp.focus();
                }
            }, 300);
        }
    }

    function _streamFromCard(title, artist, album) {
        if (typeof showToast === 'function') showToast('🔍 Finding preview for "' + title + '"…', 'info');
        fetch('/api/enhanced-search/stream-track', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                track_name: title,
                artist_name: artist,
                album_name: album || ''
            })
        })
        .then(function (r) { return r.json(); })
        .then(function (res) {
            if (res && res.success && res.track) {
                if (typeof startStream === 'function') {
                    startStream(res.track);
                } else if (typeof setTrackInfo === 'function') {
                    setTrackInfo(res.track);
                }
                if (typeof showToast === 'function') showToast('▶ Playing preview for ' + title, 'success');
            } else {
                if (typeof showToast === 'function') showToast((res && res.error) || 'Could not find a stream preview for this track', 'warning');
            }
        })
        .catch(function () {
            if (typeof showToast === 'function') showToast('Failed to start stream preview', 'error');
        });
    }

    function _onHaveWanted(username, title, artist) {
        if (!username || username === 'you' || username === state.selfName) {
            if (typeof showToast === 'function') showToast('This is your own wanted card!', 'info');
            return;
        }
        openPm(username);
        var input = q('[data-chat-input]');
        if (input) {
            input.value = 'Hey @' + username + '! I saw your request for "' + title + '" by ' + artist + ' — I have this in my library! Feel free to browse my shares or let me know if you want me to send it over.';
            input.focus();
            input.dispatchEvent(new Event('input', { bubbles: true }));
        }
        if (typeof showToast === 'function') {
            showToast('Switched to PM with @' + username + ' — message ready to send!', 'success');
        }
    }

    async function _addWantedToWishlist(w) {
        if (!w || (!w.t && !w.a)) return;
        var title = w.t || 'Unknown Title';
        var artist = w.a || 'Unknown Artist';
        var albumName = w.al || w.t || title;
        var imgUrl = w.img || '';
        var src = w.src || 'spotify';
        if (src === 'shared') src = 'spotify';
        var id = w.id || '';
        var ty = (w.ty || 'album').toLowerCase();
        var isAlbum = (ty === 'album' || ty === 'ep');

        if (typeof showToast === 'function') {
            showToast('Loading ' + (isAlbum ? 'album details' : 'track') + ' for wishlist…', 'info');
        }

        var fullAlbumData = null;
        var tracks = [];

        // 1. If it's an album and we have an ID and source, fetch the full album tracks directly
        if (isAlbum && id && src) {
            try {
                var albRes = await fetch('/api/spotify/album/' + encodeURIComponent(id) + '?source=' + encodeURIComponent(src) + '&name=' + encodeURIComponent(albumName) + '&artist=' + encodeURIComponent(artist));
                if (albRes.ok) {
                    fullAlbumData = await albRes.json();
                }
            } catch (e) {
                console.debug('[Chat] Direct album fetch failed:', e);
            }
        }

        // 2. If we don't have full album data, or if id was missing, query enhanced-search
        if ((isAlbum && (!fullAlbumData || !fullAlbumData.tracks || !fullAlbumData.tracks.length)) || !id || !imgUrl) {
            try {
                var searchQ = (artist !== 'Unknown Artist' ? artist + ' ' : '') + title;
                var sr = await fetch('/api/enhanced-search', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ query: searchQ.trim() })
                }).then(function (r) { return r.ok ? r.json() : null; });
                if (sr) {
                    var als = sr.albums || sr.spotify_albums || [];
                    var trs = sr.tracks || sr.spotify_tracks || [];
                    if (isAlbum && als.length > 0) {
                        var matchAl = als.find(function (a) {
                            return a.name && title && a.name.toLowerCase() === title.toLowerCase();
                        }) || als[0];
                        if (matchAl) {
                            if (!id) id = String(matchAl.id || '');
                            if (!imgUrl && matchAl.image_url) imgUrl = matchAl.image_url;
                            if (matchAl.source) src = matchAl.source;
                            if (matchAl.name) albumName = matchAl.name;
                            // Fetch full tracks using the resolved ID and source
                            if (id) {
                                var albRes2 = await fetch('/api/spotify/album/' + encodeURIComponent(id) + '?source=' + encodeURIComponent(src) + '&name=' + encodeURIComponent(albumName) + '&artist=' + encodeURIComponent(artist));
                                if (albRes2.ok) {
                                    fullAlbumData = await albRes2.json();
                                }
                            }
                        }
                    } else if (!isAlbum && trs.length > 0) {
                        var matchTr = trs.find(function (t) {
                            return t.name && title && t.name.toLowerCase() === title.toLowerCase();
                        }) || trs[0];
                        if (matchTr) {
                            if (!id) id = String(matchTr.id || '');
                            if (!imgUrl && matchTr.image_url) imgUrl = matchTr.image_url;
                            if (matchTr.album) albumName = matchTr.album;
                            if (matchTr.source) src = matchTr.source;
                            if (matchTr.duration_ms) w.dur = matchTr.duration_ms;
                        }
                    }
                }
            } catch (e) {
                console.debug('[Chat] Enhanced-search fallback for wishlist failed:', e);
            }
        }

        // 3. Build albumObj and tracks array with full context
        var albumObj = {
            id: id || null,
            name: (fullAlbumData && fullAlbumData.name) || albumName,
            album_type: ty,
            image_url: (fullAlbumData && fullAlbumData.images && fullAlbumData.images[0] && fullAlbumData.images[0].url) || imgUrl,
            images: (fullAlbumData && fullAlbumData.images && fullAlbumData.images.length)
                ? fullAlbumData.images
                : (imgUrl ? [{ url: imgUrl }] : []),
            artists: (fullAlbumData && fullAlbumData.artists && fullAlbumData.artists.length)
                ? fullAlbumData.artists
                : [{ name: artist }],
            release_date: (fullAlbumData && fullAlbumData.release_date) || w.y || '',
            total_tracks: (fullAlbumData && fullAlbumData.total_tracks) || 0,
            source: src
        };

        var artistObj = {
            id: (fullAlbumData && fullAlbumData.artists && fullAlbumData.artists[0] && fullAlbumData.artists[0].id) || null,
            name: artist,
            image_url: '',
            source: src
        };

        if (fullAlbumData && fullAlbumData.tracks && fullAlbumData.tracks.length > 0) {
            tracks = fullAlbumData.tracks.map(function (tr, idx) {
                return {
                    id: tr.id || ('wanted_' + id + '_' + idx),
                    name: tr.name || tr.title || ('Track ' + (idx + 1)),
                    artist: artist,
                    artists: (tr.artists && tr.artists.length) ? tr.artists : [{ name: artist }],
                    album: albumObj,
                    track_number: tr.track_number || (idx + 1),
                    disc_number: tr.disc_number || 1,
                    duration_ms: tr.duration_ms || 0,
                    image_url: albumObj.image_url,
                    source: src
                };
            });
            albumObj.total_tracks = tracks.length;
        } else {
            tracks = [{
                id: id || ('wanted_' + Date.now()),
                name: title,
                artist: artist,
                artists: [{ name: artist }],
                album: albumObj,
                track_number: 1,
                total_tracks: 1,
                duration_ms: Number(w.dur) || 0,
                image_url: imgUrl,
                source: src
            }];
        }

        // Use openAddToWishlistModal if available (the proper standard)
        if (typeof window.openAddToWishlistModal === 'function') {
            try {
                window.openAddToWishlistModal(albumObj, artistObj, tracks, ty, null);
                return;
            } catch (err) {
                console.error('[Chat] openAddToWishlistModal failed:', err);
                if (typeof showToast === 'function') showToast('Could not open wishlist modal', 'error');
            }
            return;
        }

        // Fallback: direct API call
        try {
            var trackObj = tracks[0] || {
                id: id || 'wanted_' + Date.now(),
                name: title,
                artist: artist,
                artists: [{ name: artist }],
                duration_ms: 0,
                source: src
            };
            var res = await fetch('/api/add-album-to-wishlist', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    track: trackObj,
                    artist: { id: null, name: artist, source: src },
                    album: { id: id || null, name: albumName, album_type: ty, images: imgUrl ? [{ url: imgUrl }] : [] },
                    source_type: ty,
                    source_context: { album_name: albumName, artist_name: artist, album_type: ty }
                })
            });
            var result = await res.json();
            if (result.success) {
                if (typeof showToast === 'function') showToast('✅ Added "' + title + '" to Wishlist!', 'success');
            } else {
                if (typeof showToast === 'function') showToast(result.error || 'Failed to add to wishlist', 'error');
            }
        } catch (err) {
            console.error('[Chat] Failed to add to wishlist:', err);
            if (typeof showToast === 'function') showToast('Failed to add to wishlist', 'error');
        }
    }

    function _bindChatDragAndDrop() {
        var zone = q('.chat-main') || q('[data-chat-messages]');
        var overlay = q('[data-chat-drop-overlay]');
        if (!zone || !overlay) return;

        var dragCounter = 0;
        zone.addEventListener('dragenter', function (e) {
            e.preventDefault();
            dragCounter++;
            overlay.hidden = false;
        });
        zone.addEventListener('dragover', function (e) {
            e.preventDefault();
            if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
            overlay.hidden = false;
        });
        zone.addEventListener('dragleave', function (e) {
            e.preventDefault();
            dragCounter--;
            if (dragCounter <= 0) {
                dragCounter = 0;
                overlay.hidden = true;
            }
        });
        zone.addEventListener('drop', function (e) {
            e.preventDefault();
            dragCounter = 0;
            overlay.hidden = true;
            if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) {
                var file = e.dataTransfer.files[0];
                attachUploadFile(file);
            }
        });
    }

    // ── developer identification (SoulSync creator & lead dev) ─────────────
    function isDev(name) {
        if (!name) return false;
        return String(name).toLowerCase() === 'boulderbadgedad';
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
                (isDev(user) ? '<span class="chat-dev-badge" title="SoulSync Creator & Lead Developer"><span class="chat-dev-badge-check">✔</span> DEV</span>' : '') +
                (!self && isFriend(user) ? '<span class="chat-friend-badge" title="Friend">⭐ Friend</span>' : '') +
                (ext ? '<span class="chat-peer-badge chat-ext-tag" title="Sent from another Soulseek client — not SoulSync">via Soulseek</span>' : '<span class="chat-peer-badge chat-peer-badge--soulsync">SoulSync</span>') +
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
        // The Arcade takes over the message column, so it hangs off the same
        // entry point every caller already uses rather than needing each of
        // them to know about it.
        if (_arcOn()) { renderArcade(); return; }
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
        // Fold message edits BEFORE any view filtering, so an edit applies to
        // its target no matter which channel/thread either is shown in.
        var editFold = _applyEdits(msgs);
        _editsByKey = editFold.edits;
        var shown = editFold.list, hidden = 0, muted = 0;
        // Suppress messages from locally blocked users (both in rooms and PMs)
        var blk = blockedSet();
        if (blk.length) {
            shown = shown.filter(function (m) {
                var u = String(m.username || m.user || '');
                if (isBlocked(u) && !(m.self === true || m.direction === 'Out')) {
                    muted++;
                    return false;
                }
                return true;
            });
        }
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
        _unfurlPendingLinks(host);
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

    // ── per-channel mute (local, per browser — like the user mute) ──────────
    // A muted channel stays fully readable and keeps its place in the list;
    // it just goes quiet: no unread badge, dimmed row, 🔕. Mentions still
    // ping (someone saying your name cuts through, Discord-style). Nothing
    // rides the wire — muting is this browser's preference, nobody else's.
    function mutedChans() {
        try { return JSON.parse(localStorage.getItem('chat_chan_muted') || '[]'); }
        catch (e) { return []; }
    }
    function isChanMuted(slug) { return mutedChans().indexOf(slug) > -1; }
    function toggleChanMuted(slug) {
        if (!slug) return;
        var list = mutedChans();
        var i = list.indexOf(slug);
        if (i > -1) list.splice(i, 1); else list.push(slug);
        try { localStorage.setItem('chat_chan_muted', JSON.stringify(list)); } catch (e) { /* ignore */ }
        renderChannels();
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

    // ── social badges updater (sidebar buttons, drawer tabs, peer bookmarks button) ──
    function _updateSocialBadges() {
        var frCount = friendsSet().length;
        var blCount = blockedSet().length;
        var bmCount = peerBookmarksSet().length;

        // Sidebar navigation badges
        var sideFr = q('[data-chat-side-count-friends]');
        if (sideFr) {
            sideFr.textContent = frCount;
            sideFr.hidden = (frCount === 0);
        }
        var sideBl = q('[data-chat-side-count-blocked]');
        if (sideBl) {
            sideBl.textContent = blCount;
            sideBl.hidden = (blCount === 0);
        }
        var sideBm = q('[data-chat-side-count-bookmarks]');
        if (sideBm) {
            sideBm.textContent = bmCount;
            sideBm.hidden = (bmCount === 0);
        }

        // Drawer tab pills
        var dFr = q('[data-chat-count-friends]');
        if (dFr) dFr.textContent = frCount;
        var dBl = q('[data-chat-count-blocked]');
        if (dBl) dBl.textContent = blCount;
        var dBm = q('[data-chat-count-bookmarks]');
        if (dBm) dBm.textContent = bmCount;

        // Peer explorer drawer bookmarks button
        var peerBmBtn = q('[data-chat-browse-saved-peers]');
        if (peerBmBtn) {
            peerBmBtn.textContent = '📚 Bookmarks (' + bmCount + ')';
        }
    }

    // the bookmark handlers still call this by its old name; the count now
    // lives with the rest of the social badges, so it's the same refresh
    function _updateSavedPeersBtn() { _updateSocialBadges(); }

    // ── friend list (local only — special shiny star & badge, no requesting) ──
    function friendsSet() {
        try { return JSON.parse(localStorage.getItem('chat_friends') || '[]'); }
        catch (e) { return []; }
    }
    function isFriend(name) {
        if (!name) return false;
        return friendsSet().some(function (f) { return String(f).toLowerCase() === String(name).toLowerCase(); });
    }
    function toggleFriend(name) {
        if (!name) return false;
        var list = friendsSet();
        var idx = -1;
        for (var i = 0; i < list.length; i++) {
            if (String(list[i]).toLowerCase() === String(name).toLowerCase()) { idx = i; break; }
        }
        var isNow = false;
        if (idx > -1) {
            list.splice(idx, 1);
        } else {
            list.push(name);
            isNow = true;
        }
        try { localStorage.setItem('chat_friends', JSON.stringify(list)); } catch (e) { /* ignore */ }
        state.lastStamp = null;
        renderMessages(state.msgs);
        renderUsersList();
        _updateSocialBadges();
        return isNow;
    }

    // ── block list (local only — completely suppresses chat messages by user) ──
    function blockedSet() {
        try { return JSON.parse(localStorage.getItem('chat_blocked') || '[]'); }
        catch (e) { return []; }
    }
    function isBlocked(name) {
        if (!name) return false;
        return blockedSet().some(function (b) { return String(b).toLowerCase() === String(name).toLowerCase(); });
    }
    function toggleBlocked(name) {
        if (!name) return false;
        var list = blockedSet();
        var idx = -1;
        for (var i = 0; i < list.length; i++) {
            if (String(list[i]).toLowerCase() === String(name).toLowerCase()) { idx = i; break; }
        }
        var isNowBlocked = false;
        if (idx > -1) {
            list.splice(idx, 1);
        } else {
            list.push(name);
            isNowBlocked = true;
        }
        try { localStorage.setItem('chat_blocked', JSON.stringify(list)); } catch (e) { /* ignore */ }
        state.lastStamp = null;
        renderMessages(state.msgs);
        renderUsersList();
        _updateSocialBadges();
        return isNowBlocked;
    }

    // ── peer bookmarks (local only — saved peers to browse or bookmark) ──
    function peerBookmarksSet() {
        try { return JSON.parse(localStorage.getItem('chat_peer_bookmarks') || '[]'); }
        catch (e) { return []; }
    }
    function isPeerBookmarked(name) {
        if (!name) return false;
        return peerBookmarksSet().some(function (p) { return String(p).toLowerCase() === String(name).toLowerCase(); });
    }
    function togglePeerBookmark(name) {
        if (!name) return false;
        var list = peerBookmarksSet();
        var idx = -1;
        for (var i = 0; i < list.length; i++) {
            if (String(list[i]).toLowerCase() === String(name).toLowerCase()) { idx = i; break; }
        }
        var isNowBookmarked = false;
        if (idx > -1) {
            list.splice(idx, 1);
        } else {
            list.push(name);
            isNowBookmarked = true;
        }
        try { localStorage.setItem('chat_peer_bookmarks', JSON.stringify(list)); } catch (e) { /* ignore */ }
        _updateSocialBadges();
        return isNowBookmarked;
    }

    // ── direct message closed / hidden list (dismissed so they don't show in list) ──
    function hiddenDmsSet() {
        try { return JSON.parse(localStorage.getItem('chat_hidden_dms') || '[]'); }
        catch (e) { return []; }
    }
    function isDmHidden(name) {
        if (!name) return false;
        return hiddenDmsSet().some(function (h) { return String(h).toLowerCase() === String(name).toLowerCase(); });
    }
    function hideDm(name) {
        if (!name) return;
        var list = hiddenDmsSet();
        var exists = list.some(function (h) { return String(h).toLowerCase() === String(name).toLowerCase(); });
        if (!exists) {
            list.push(name);
            try { localStorage.setItem('chat_hidden_dms', JSON.stringify(list)); } catch (e) {}
        }
    }
    function unhideDm(name) {
        if (!name) return;
        var list = hiddenDmsSet();
        var filtered = list.filter(function (h) { return String(h).toLowerCase() !== String(name).toLowerCase(); });
        if (filtered.length !== list.length) {
            try { localStorage.setItem('chat_hidden_dms', JSON.stringify(filtered)); } catch (e) {}
        }
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
        var blk = isBlocked(n);
        var fr = isFriend(n);
        var tuned = tunedMap && tunedMap[n];
        var np = npMap && npMap[n];
        return '<button class="chat-user' + (extraClass || '') + (ign ? ' chat-user--ignored' : '') +
            (blk ? ' chat-user--blocked' : '') + (fr ? ' chat-user--friend' : '') +
            '" type="button" data-chat-user="' + attr(n) + '" title="' + attr(n) +
            (fr ? ' (Friend)' : '') + (blk ? ' (Blocked)' : '') +
            (tuned ? ' — listening to the room jukebox' : '') + '">' +
            '<span class="chat-user-av">' +
                _avatarHtml(n, avMap && avMap[n], 'chat-av--fill') +
                '<span class="chat-user-dot' + (tuned ? ' chat-user-dot--tuned' : '') + '"></span>' +
            '</span>' +
            '<span class="chat-user-main">' +
                '<span class="chat-user-name">' + esc(n) +
                    (isDev(n) ? '<span class="chat-dev-badge chat-dev-badge--inline" title="SoulSync Creator & Lead Developer"><span class="chat-dev-badge-check">✔</span> DEV</span>' : '') +
                    (fr ? '<span class="chat-user-friend-star" title="Friend">⭐</span>' : '') +
                '</span>' +
                // the shared jukebox wins the line — it's what the room is doing
                // together; a personal now-playing shows otherwise
                (tuned
                    ? '<span class="chat-user-act chat-user-tuned">♫ Listening to the jukebox</span>'
                    : (np && np.t
                        ? '<span class="chat-user-act" title="' + attr(np.t + (np.a ? ' — ' + np.a : '')) +
                            '">♪ ' + esc(np.t) + (np.a ? ' · ' + esc(np.a) : '') + '</span>'
                        : '')) +
            '</span>' +
            (blk ? '<span class="chat-user-mute">blocked</span>' : (ign ? '<span class="chat-user-mute">muted</span>' : '')) + '</button>';
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
        var devs = [], self = [], friends = [], apps = [], rest = [];
        names.forEach(function (n) {
            if (isDev(n)) devs.push(n);
            else if (state.selfName && n === state.selfName) self.push(n);
            else if (isFriend(n)) friends.push(n);
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
        if (devs.length) {
            html += '<div class="chat-users-label chat-users-label--sub" style="color:#a5b4fc;">🛠️ Developer &mdash; ' + devs.length + '</div>' +
                devs.map(function (n) { return _userBtn(n, ' chat-user--dev', tunedMap, npMap, avMap); }).join('');
        }
        if (self.length) {
            html += '<div class="chat-users-label chat-users-label--sub">You</div>' +
                self.map(function (n) { return _userBtn(n, ' chat-user--self', tunedMap, npMap, avMap); }).join('');
        }
        if (friends.length) {
            html += '<div class="chat-users-label chat-users-label--sub" style="color:#fbbf24;">⭐ Friends &mdash; ' + friends.length + '</div>' +
                friends.map(function (n) { return _userBtn(n, '', tunedMap, npMap, avMap); }).join('');
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
        if (!devs.length && !self.length && !friends.length && !apps.length && !rest.length) {
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
        var list = (convos || []).filter(function (c) {
            var name = c.username || c.name || '';
            if (!name) return false;
            var unread = c.hasUnAcknowledgedMessages || c.unAcknowledgedMessageCount > 0;
            if (unread) {
                unhideDm(name);
                return true;
            }
            if (state.view === 'pm' && state.pmUser === name) {
                return true;
            }
            return !isDmHidden(name);
        }).map(function (c) {
            var name = c.username || c.name || '';
            var unread = c.hasUnAcknowledgedMessages || c.unAcknowledgedMessageCount > 0;
            var on = state.view === 'pm' && state.pmUser === name;
            return '<div class="chat-side-convo' + (on ? ' chat-side-item--on' : '') + '">' +
                '<button class="chat-side-item' + (on ? ' chat-side-item--on' : '') +
                '" type="button" data-chat-open-pm="' + attr(name) + '">' + esc(name) +
                (unread ? '<span class="chat-side-dot"></span>' : '') + '</button>' +
                '<button class="chat-side-convo-close" type="button" data-chat-close-pm="' + attr(name) + '" title="Close conversation">×</button>' +
            '</div>';
        }).join('');
        host.innerHTML = list || '<div class="chat-side-none">No conversations</div>';
        renderGuilds();
        renderChannels();
        renderUserPanel();
        _arcBindDrag();       // idempotent; the page element outlives every render
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
            // The community room is the app's own room, so it gets the app's
            // mark. Every other room is somebody else's and keeps initials.
            var face = r.home
                ? '<img class="chat-guild-logo" src="/static/trans2.png" alt="" ' +
                  'aria-hidden="true">'
                : esc(initials);
            // The rail is the ONLY room switcher now (the sidebar lists channels,
            // not rooms), so leaving has to live here too — × on hover, home room
            // excluded, same rule the old sidebar list used.
            return '<span class="chat-guild-wrap">' +
                '<button class="chat-guild' + (on ? ' chat-guild--on' : '') + '" type="button" ' +
                    'data-chat-open-room="' + attr(r.name) + '" title="' + attr(r.name) + '">' +
                    face + '</button>' +
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
                var muted = isChanMuted(ch.slug);
                // Muted = quiet: the unread badge is suppressed, not the
                // channel. Mentions still ping regardless.
                var n = muted ? 0 : (unread[ch.slug] || 0);
                var row = '<button class="chat-chan' + (on ? ' chat-chan--on' : '') +
                    (n ? ' chat-chan--unread' : '') +
                    (muted ? ' chat-chan--muted' : '') + '" type="button" ' +
                    'data-chat-chan="' + attr(ch.slug) + '">' +
                    '<span class="chat-chan-hash">#</span>' +
                    '<span class="chat-chan-name">' + esc(ch.name) + '</span>' +
                    (n ? '<span class="chat-chan-unread">' + (n > 99 ? '99+' : n) + '</span>' : '') +
                    '<span class="chat-chan-mute" data-chat-chan-mute="' + attr(ch.slug) + '" ' +
                        'title="' + (muted ? 'Unmute #' + attr(ch.slug) : 'Mute #' + attr(ch.slug) +
                        ' — no unread badge, mentions still ping') + '">' +
                        (muted ? '🔕' : '🔔') + '</span>' +
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
        }).join('') + _arcSidebarHtml();
    }

    // The Arcade's sidebar block. It sits below the channels and looks like
    // one, but it is a view rather than a tag — see the Arcade section.
    // Active games hang beneath it the way threads hang beneath a channel.
    function _arcSidebarHtml() {
        if (!_arcReady()) return '';
        var closed = (state.chanCatClosed || {}).Games;
        var on = !!state.arcade;
        if (closed) {
            return '<button class="chat-cat chat-cat--closed" type="button" data-chat-cat="Games">' +
                '<span class="chat-cat-caret">⌄</span>Games</button>';
        }
        var turns = _arcMyTurnCount();
        var row = '<button class="chat-chan' + (on && !state.arcade.game ? ' chat-chan--on' : '') +
            (turns ? ' chat-chan--unread' : '') + '" type="button" data-chat-arc-home>' +
            '<span class="chat-chan-hash">🎲</span>' +
            '<span class="chat-chan-name">arcade</span>' +
            (turns ? '<span class="chat-chan-unread">' + turns + '</span>' : '') +
        '</button>';
        if (on) {
            row += _arcSidebarGames().map(function (g) {
                var gOn = state.arcade.game === g.id;
                var label = (g.white || '?') + ' vs ' + (g.black || 'open');
                return '<button class="chat-thread' + (gOn ? ' chat-thread--on' : '') +
                    (_arcMyMove(g) ? ' chat-thread--turn' : '') +
                    '" type="button" data-chat-arc-open="' + attr(g.id) + '" ' +
                    'title="' + attr(label) + '">' +
                    '<span class="chat-thread-branch"></span>' +
                    '<span class="chat-thread-name">' + esc(label) + '</span>' +
                    (_arcMyMove(g) ? '<span class="chat-chan-unread">!</span>' : '') +
                '</button>';
            }).join('');
        }
        return '<button class="chat-cat" type="button" data-chat-cat="Games">' +
            '<span class="chat-cat-caret">⌄</span>Games</button>' + row;
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
        state.arcade = null;      // leaving for a channel leaves the arcade
        renderMessages(state.msgs);
        renderHead();
        renderComposer();
        renderChannels();
    }

    // ── The Arcade ──────────────────────────────────────────────────────────
    //
    // Games are a VIEW, deliberately not a channel. A channel is a tag carried
    // on messages, and every tag must resolve to somewhere a message is
    // readable — that is the "nothing is ever invisible" invariant. A channel
    // whose column renders a chessboard instead of messages would break it, so
    // the arcade sits beside the channel list with its own state flag and the
    // message-channel logic is left completely alone.
    //
    // Everything on screen is a pure fold of the room's protocol carriers
    // (see chat-games.js). There is no server: the board you are looking at
    // was computed locally from chat messages, and so was your opponent's.
    var ARC_GLYPH = {
        K: '♔', Q: '♕', R: '♖', B: '♗', N: '♘', P: '♙',
        k: '♚', q: '♛', r: '♜', b: '♝', n: '♞', p: '♟',
    };
    var ARC_PROMO = [['q', '♕'], ['r', '♖'], ['b', '♗'], ['n', '♘']];

    function _arcReady() { return !!(window.ChessEngine && window.ChatGames); }
    function _arcOn() { return !!state.arcade && _chanRoom() && _arcReady(); }

    // The fold is cheap (~3ms for a 400-move game) but it runs on every bus
    // repaint, so it is cached. The key is the log ARRAY ITSELF plus its
    // length, not the length alone: ingestion appends (length moves), and
    // anything that replaces the log hands us a different array. Keying on
    // length alone would serve a stale fold whenever a different log happened
    // to be the same size — which is exactly what the render harness caught.
    // `stale` depends on the wall clock, but only at 24h granularity, so a
    // cached value is never meaningfully wrong.
    var _arcCache = { ref: null, n: -1, room: null, out: null };

    function _gamesState() {
        if (!_arcReady()) return { games: {}, order: [] };
        var log = state.protocolLog || [];
        if (_arcCache.out && _arcCache.ref === log && _arcCache.n === log.length &&
            _arcCache.room === state.room) {
            return _arcCache.out;
        }
        var out = window.ChatGames.reduceGames(_roomEvents(), Date.now());
        _settleWagers(out);
        // Moderator game-kill: a killed id is erased from the fold — board,
        // cards, lifecycle, everywhere — on every client that folds the room.
        var CPk = window.ChatProtocol;
        var kills = (CPk && CPk.reduceGameKills) ? CPk.reduceGameKills(_roomEvents()) : {};
        if (out && out.order && out.order.some(function (id) { return kills[id]; })) {
            var games = {};
            var order = out.order.filter(function (id) { return !kills[id]; });
            order.forEach(function (id) { games[id] = out.games[id]; });
            out = { games: games, order: order };
        }
        _arcCache = { ref: log, n: log.length, room: state.room, out: out };
        return out;
    }

    function _arcGame(id) {
        return _gamesState().games[id] || null;
    }

    // Every carrier in this room that belongs to a game — what the reveal
    // shows. Cheap: the protocol log is already in memory.
    function _arcCarriers(gid) {
        return _roomEvents().filter(function (e) {
            return e && e.p && typeof e.p.k === 'string' &&
                   e.p.k.slice(0, 3) === 'gm.' && e.p.g === gid;
        });
    }

    // A .pgn the room can load into Lichess. A game played over Soulseek
    // being a normal chess file is the entire joke, so this is a real export
    // and not a text dump: toPGN writes the seven-tag roster, and for a game
    // adopted mid-stream it emits SetUp/FEN so the partial move list still
    // describes a valid game rather than a wrong one.
    function _arcPgn(game) {
        var E = window.ChessEngine;
        return E.toPGN(game.moves, {
            White: game.white || '?', Black: game.black || '?',
            Result: game.result || '*',
            Site: 'Soulseek/' + (state.room || 'SoulSync'),
        }, game.startFen);
    }

    function arcDownloadPgn(gid) {
        var game = _arcGame(gid);
        if (!game) return;
        try {
            var blob = new Blob([_arcPgn(game)], { type: 'application/x-chess-pgn' });
            var url = URL.createObjectURL(blob);
            var a = document.createElement('a');
            a.href = url;
            a.download = 'soulsync-' + gid + '.pgn';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
        } catch (e) {
            if (typeof showToast === 'function') showToast('Could not build the PGN', 'error');
        }
    }

    function arcCopyPgn(gid) {
        var game = _arcGame(gid);
        if (!game) return;
        var text = _arcPgn(game);
        var done = function () {
            if (typeof showToast === 'function') showToast('PGN copied', 'success');
        };
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).then(done, function () {
                if (typeof showToast === 'function') showToast('Could not copy', 'error');
            });
        } else if (typeof showToast === 'function') {
            showToast('Clipboard unavailable in this browser', 'error');
        }
    }

    // 8 chars of [a-z0-9]. A collision only means the second gm.new is
    // ignored, so this needs to be unlikely, not guaranteed.
    function _newGid() {
        var abc = 'abcdefghijklmnopqrstuvwxyz0123456789', s = '';
        for (var i = 0; i < 8; i++) s += abc[Math.floor(Math.random() * abc.length)];
        return s;
    }

    function _arcSeat(game) {
        if (!game || !state.selfName) return '';
        if (game.white === state.selfName) return 'w';
        if (game.black === state.selfName) return 'b';
        return '';
    }

    function _arcMyMove(game) {
        return !!(game && window.ChatGames.toMove(game) &&
                  window.ChatGames.toMove(game) === state.selfName);
    }

    // The room's move is everyone's to influence except the opponent's — they
    // are playing against the room, so they do not get a ballot in it.
    function _arcCanVote(game) {
        return !!(game && state.canSend && window.ChatGames.isRoomTurn(game) &&
                  !_arcSeat(game));
    }

    // Can this client put a piece on this board right now, either as a move
    // or as a vote? Everything interactive keys off this.
    function _arcActive(game) {
        return _arcMyMove(game) || _arcCanVote(game);
    }

    // Games worth showing in the sidebar: mine first, then anything live or
    // waiting for an opponent. Finished games stay in the lobby only.
    function _arcSidebarGames() {
        var st = _gamesState();
        return st.order.map(function (id) { return st.games[id]; }).filter(function (g) {
            return g.status !== 'over';
        }).slice(0, 12);
    }

    // Withdrawing is for a game nothing has happened in. An open table
    // qualifies, and so does a room game nobody has voted in yet — those are
    // 'live' from creation, so without this they could only be escaped by
    // resigning to an opponent who does not exist.
    function _arcCanWithdraw(game) {
        if (!game || !state.canSend || game.createdBy !== state.selfName) return false;
        if (game.status === 'open') return true;
        return !!(game.roomSeat && game.status === 'live' && game.ply === 0);
    }

    function _arcMyTurnCount() {
        var st = _gamesState();
        var n = 0;
        st.order.forEach(function (id) { if (_arcMyMove(st.games[id])) n++; });
        return n;
    }

    function openArcade(gid) {
        if (!_arcReady()) return;
        state.arcade = { game: gid || null, sel: -1, promo: null, flip: false, slots: false };
        var g = gid ? _arcGame(gid) : null;
        // Black sees the board from black's side, like every chess site.
        if (g && _arcSeat(g) === 'b') state.arcade.flip = true;
        state.thread = null;
        state.lastStamp = null;
        renderMessages(state.msgs);
        renderHead();
        renderComposer();
        renderChannels();
    }

    function closeArcade() {
        if (!state.arcade) return;
        state.arcade = null;
        state.lastStamp = null;
        renderMessages(state.msgs);
        renderHead();
        renderComposer();
        renderChannels();
    }

    // ── Arcade actions (each is one carrier on the bus) ──────────────────
    // Nothing is applied optimistically. Our own carrier comes back through
    // the room like everyone else's and the fold picks it up, so what we see
    // is always what the room saw — an optimistic board could disagree with
    // the fold and there would be no way to tell which was right.

    function _arcAfterSend(gid) {
        return function (r) {
            if (!r || !r.ok) {
                if (typeof showToast === 'function') showToast('Could not reach the room', 'error');
                return;
            }
            if (gid) state.arcade = Object.assign(state.arcade || {}, { game: gid, sel: -1 });
            refresh();
        };
    }

    function arcNewGame(color, opponent, variant, vsRoom) {
        var gid = _newGid();
        var known = { connect4: 1, battleship: 1 };
        var f = { g: gid, v: known[variant] ? variant : 'chess',
                  c: color === 'b' ? 'b' : 'w' };
        if (vsRoom) f.r = 1;
        if (opponent) f.o = String(opponent).slice(0, 64);
        // The lobby's stake selector rides along (two-human games only).
        var stake = (state.arcade && state.arcade.stake) || 0;
        if (stake > 0 && !vsRoom) f.w = stake;
        sendProtocol('gm.new', f).then(_arcAfterSend(gid));
    }

    // ── Wager settlement ─────────────────────────────────────────────────
    // Each client books ONLY ITS OWN result against ITS OWN bank — the
    // fold is the referee every client shares, so both sides compute the
    // same winner; your client applying your loss is the bank's whole
    // philosophy (play money, nobody to defraud but yourself). Idempotent
    // per game id across repaints AND reloads via localStorage; marked
    // settled BEFORE the POST so a slow request can't double-book.
    var _wagerSettled = null;
    function _wagerSettledMap() {
        if (_wagerSettled) return _wagerSettled;
        try { _wagerSettled = JSON.parse(localStorage.getItem('chatWagerSettled') || '{}'); }
        catch (e) { _wagerSettled = {}; }
        return _wagerSettled;
    }
    function _wagerMarkSettled(gid) {
        var map = _wagerSettledMap();
        map[gid] = 1;
        var keys = Object.keys(map);
        if (keys.length > 300) keys.slice(0, keys.length - 300).forEach(function (k) { delete map[k]; });
        try { localStorage.setItem('chatWagerSettled', JSON.stringify(map)); } catch (e) { /* private mode */ }
    }
    function _settleWagers(st) {
        if (!state.selfName) return;
        var map = _wagerSettledMap();
        (st.order || []).forEach(function (gid) {
            var g = st.games[gid];
            if (!g || !g.wager || g.status !== 'over' || !g.result || map[gid]) return;
            if (g.reason === 'expired' || g.reason === 'cancelled') return;
            var seat = _arcSeat(g);
            if (!seat) return;                            // spectators hold no stake
            _wagerMarkSettled(gid);
            var delta = 0;
            if (g.result === '1/2-1/2') delta = 0;
            else if (g.winner === state.selfName) delta = g.wager;
            else delta = -g.wager;
            if (!delta) return;
            postJSON('/api/chat/arcade/bank', { delta: delta }).then(function (r) {
                if (r && r.ok && r.body && typeof r.body.balance === 'number') {
                    _slotState().bank = r.body;
                }
                if (typeof showToast === 'function') {
                    showToast(delta > 0
                        ? '🪙 +' + delta + ' — you won the stake'
                        : '🪙 ' + delta + ' — stake paid out', delta > 0 ? 'success' : 'info');
                }
                renderArcade();
            }).catch(function () { /* bank floors at zero server-side; refusal = uncollectable, fine */ });
        });
    }

    function arcJoin(gid) { sendProtocol('gm.join', { g: gid }).then(_arcAfterSend(gid)); }

    // ── keeping in step ─────────────────────────────────────────────────
    //
    // A forward move already pulls a stale client up to date, but it needs a
    // move to arrive. The case that fixes nothing is mutual silence: if you
    // missed my last move, you are waiting on me and I am waiting on you, so
    // neither of us ever sends anything. gm.sync is how we get out — ask the
    // room where the game is instead of waiting for a message that is never
    // coming.
    //
    // Every carrier is visible noise to vanilla Soulseek clients, so this is
    // deliberately stingy: only while the chat page is open, only for a live
    // game we are seated in, only after a long quiet spell, and at most once
    // per game per cooldown.
    var ARC_SYNC_QUIET = 5 * 60 * 1000;    // nothing heard for this long
    var ARC_SYNC_EVERY = 10 * 60 * 1000;   // and ask no more often than this
    var ARC_ANSWER_SPREAD = 6000;          // answers stagger across this window
    var _arcAsked = {};                    // gid -> when we last asked (ms)
    var _arcAnswered = {};                 // gid|user|n -> already answered

    function arcSync(gid, accept) {
        var g = _arcGame(gid);
        if (!g) return;
        _arcAsked[gid] = Date.now();
        var f = { g: gid, n: g.ply };
        if (accept) f.r = 1;
        sendProtocol('gm.sync', f).then(_arcAfterSend(gid));
    }

    // A stable per-client delay in [0, ARC_ANSWER_SPREAD). Derived from the
    // username so it does not change between renders, and so two clients
    // reliably answer at different moments rather than colliding.
    function _arcAnswerDelay() {
        var h = 0, n = String(state.selfName || '');
        for (var i = 0; i < n.length; i++) h = ((h << 5) - h + n.charCodeAt(i)) | 0;
        return Math.abs(h) % ARC_ANSWER_SPREAD;
    }

    // Answer somebody else's request. EVERY SoulSync client in the room folds
    // every game, so the pool of clients that can help is the whole room, not
    // the two players — which is what makes a quorum reachable at all.
    //
    // The flip side is that a naive implementation has sixteen people shouting
    // the same position at once. So each client waits its own stable moment and
    // then shuts up if the room has already produced enough agreement: the cost
    // settles at roughly quorum-plus-a-few messages instead of one per client.
    function _arcAnswerSyncs(fresh) {
        if (!_arcReady() || !state.canSend || !state.selfName) return;
        (fresh || []).forEach(function (e) {
            if (!e || !e.p || e.p.k !== 'gm.sync') return;
            if (e.username === state.selfName) return;          // not our own
            if (typeof e.p.n !== 'number') return;
            var g0 = _gamesState().games[e.p.g];
            if (!g0 || g0.status !== 'live') return;
            if (!(g0.ply > e.p.n) && !e.p.r) return;            // they are not behind
            var key = g0.id + '|' + e.username + '|' + e.p.n;
            if (_arcAnswered[key]) return;                      // asked and answered
            _arcAnswered[key] = 1;
            setTimeout(function () {
                // Re-read: the answer may have arrived from others while we
                // waited, and the game may have moved on entirely.
                var g = _gamesState().games[e.p.g];
                if (!g || g.status !== 'live') return;
                if (!(g.ply > e.p.n) && !e.p.r) return;
                var slot = g.answers && g.answers[g.fen];
                var backers = slot ? Object.keys(slot.by).length : 0;
                if (backers >= window.ChatGames.STATE_QUORUM) return;   // settled without us
                sendProtocol('gm.state', { g: g.id, n: g.ply, f: g.fen });
            }, _arcAnswerDelay());
        });
    }

    // Ask, when a wait has gone on long enough to be suspicious.
    function _arcMaybeSync() {
        if (!_arcReady() || !state.canSend || !state.selfName || !_chanRoom()) return;
        var st = _gamesState();
        var now = Date.now();
        st.order.forEach(function (id) {
            var g = st.games[id];
            if (!g || g.status !== 'live' || g.desync) return;
            if (!_arcSeat(g)) return;                           // only our own games
            if (_arcMyMove(g)) return;                          // the ball is with us
            if (now - g.lastAt < ARC_SYNC_QUIET) return;
            if (now - (_arcAsked[id] || 0) < ARC_SYNC_EVERY) return;
            arcSync(id, false);
        });
    }

    // Vote for the room's move. Nobody owns that seat, so this is not a move
    // — the fold commits it once enough distinct people have picked the same
    // one, which every client works out from the same stream.
    function arcVote(gid, uci) {
        var g = _arcGame(gid);
        // A ballot is cast for the ROOM's seat — that is the actor a variant
        // judges the move by (chess and Connect 4 ignore it).
        if (!g || !window.ChatGames.previewMove(g, uci, g.roomSeat)) return;
        state.arcade.sel = -1;
        state.arcade.promo = null;
        sendProtocol('gm.vote', { g: gid, n: g.ply, m: uci }).then(_arcAfterSend(gid));
        renderArcade();
    }

    function arcResign(gid) {
        showConfirmDialog({
            title: 'Resign this game?',
            message: 'Your opponent takes the win. This cannot be undone.',
            confirmText: 'Resign',
            destructive: true,
        }).then(function (ok) {
            if (ok) sendProtocol('gm.res', { g: gid }).then(_arcAfterSend(gid));
        });
    }

    function arcDraw(gid) { sendProtocol('gm.draw', { g: gid }).then(_arcAfterSend(gid)); }

    // Withdraw a game nobody joined. No confirm: nothing is lost, and making
    // someone confirm away a table they set up and got bored of is friction
    // for its own sake. Resigning a LIVE game still confirms.
    function arcCancel(gid) {
        sendProtocol('gm.cancel', { g: gid }).then(_arcAfterSend(gid));
    }
    function arcClaim(gid) { sendProtocol('gm.claim', { g: gid }).then(_arcAfterSend(gid)); }

    // Send a move with the ply it occupies and the position it produces.
    // Both are what let a client that missed the opening still follow along,
    // and what lets a client that has the history catch a disagreement.
    var _arcLastMoveAt = 0;
    var _arcLastMoveKey = '';

    function arcMove(gid, uci) {
        var g = _arcGame(gid);
        if (!g) return false;
        // Every move is a real message in the room, and vanilla Soulseek
        // clients see each one as a line of noise. The floor keys on the
        // EXACT action (game + move), not on time alone: it exists to eat a
        // double-click, and a flat global floor also ate legitimate back-to-
        // back actions — battleship answers a shot automatically and the
        // answerer fires next, so their real shot landed inside the window
        // and vanished with no feedback.
        var nowMs = Date.now();
        var moveKey = gid + '|' + uci;
        if (moveKey === _arcLastMoveKey && nowMs - _arcLastMoveAt < 600) return false;
        _arcLastMoveKey = moveKey;
        _arcLastMoveAt = nowMs;
        // The fold owns "what does this move produce" so this works for any
        // variant, and so an illegal move is caught here rather than being
        // sent and silently dropped by every client that receives it. The
        // actor seat rides along: battleship's apply() refuses to judge a
        // move without knowing who made it (only a board's owner may answer
        // a shot at it), so previewing without it rejected EVERY battleship
        // action — commit, fire, answer and reveal all died right here.
        var next = window.ChatGames.previewMove(g, uci, _arcSeat(g));
        if (!next) return false;                 // never put an illegal move on the bus
        state.arcade.sel = -1;
        state.arcade.promo = null;
        sendProtocol('gm.move', {
            g: gid, v: g.variant, n: g.ply, m: uci, f: next.fen,
        }).then(_arcAfterSend(gid));
        renderArcade();                          // clear the selection immediately
        return true;
    }

    // ── Arcade rendering ────────────────────────────────────────────────

    function renderArcade() {
        if (!_arcOn()) return;
        var host = q('[data-chat-messages]');
        if (!host) return;
        // The slot machine is solo: it has no carriers, no opponent and no
        // entry in the fold, so it is a destination rather than a game id.
        if (state.arcade.slots) { host.innerHTML = _arcSlotHtml(); return; }
        var g = state.arcade.game ? _arcGame(state.arcade.game) : null;
        host.innerHTML = g ? _arcBoardHtml(g) : _arcLobbyHtml();
    }

    function _arcWho(name, colorGlyph, isTurn, roomSeat) {
        return '<span class="chat-arc-who' + (isTurn ? ' chat-arc-who--turn' : '') + '">' +
            '<span class="chat-arc-who-dot">' + colorGlyph + '</span>' +
            (roomSeat ? '🗳 the room' : esc(name || 'open seat')) + '</span>';
    }

    function _arcLobbyHtml() {
        var st = _gamesState();
        var CG = window.ChatGames;
        var mine = [], open = [], live = [], done = [], cold = [];
        st.order.forEach(function (id) {
            var g = st.games[id];
            // A withdrawn table never had an opponent and never had a result,
            // so it does not belong in "Finished" beside real games — nothing
            // finished. The carriers stay in the room (they cannot be unsent),
            // but there is nothing here worth showing anyone.
            if (g.reason === 'cancelled' || g.reason === 'expired') return;
            if (g.status === 'over') { done.push(g); return; }
            if (_arcSeat(g)) { mine.push(g); return; }
            if (g.status === 'open') {
                // A table nobody joined inside the expiry window stops
                // squatting in the lobby: carriers can't be unsent, and only
                // the creator can withdraw — so everyone else's lobby demotes
                // cold tables to a collapsed count instead of forever-cards.
                // (The creator's own table sits in `mine`, Withdraw and all.)
                (g.expired ? cold : open).push(g);
                return;
            }
            live.push(g);
        });

        function card(g) {
            var toMove = CG.toMove(g);
            var mySeat = _arcSeat(g);
            var can = state.canSend;
            var badge = '';
            if (g.status === 'over') {
                badge = '<span class="chat-arc-badge chat-arc-badge--done">' +
                    (g.result ? esc(g.result) + ' · ' : '') +
                    esc(g.reason || 'finished') + '</span>';
            } else if (g.status === 'open') {
                badge = g.expired
                    ? '<span class="chat-arc-badge chat-arc-badge--stale">nobody joined — ' +
                      'this table has gone cold</span>'
                    : '<span class="chat-arc-badge chat-arc-badge--open">waiting for an opponent</span>';
            } else if (_arcMyMove(g)) {
                badge = '<span class="chat-arc-badge chat-arc-badge--you">your move</span>';
            } else if (g.stale) {
                badge = '<span class="chat-arc-badge chat-arc-badge--stale">idle 24h · seat claimable</span>';
            } else if (g.desync) {
                badge = '<span class="chat-arc-badge chat-arc-badge--bad">positions disagreed</span>';
            }
            var actions = '';
            if (g.status === 'open' && !mySeat && can &&
                (!g.isPrivate || g.invited === state.selfName)) {
                actions += '<button class="chat-arc-btn chat-arc-btn--go" type="button" ' +
                    'data-chat-arc-join="' + attr(g.id) + '">Join</button>';
            }
            if (g.status === 'live' && !mySeat && g.stale && can) {
                actions += '<button class="chat-arc-btn" type="button" ' +
                    'data-chat-arc-claim="' + attr(g.id) + '" ' +
                    'title="This seat has been idle for 24 hours — take it over">Take the seat</button>';
            }
            if (_arcCanWithdraw(g)) {
                actions += '<button class="chat-arc-btn" type="button" ' +
                    'data-chat-arc-cancel="' + attr(g.id) + '" ' +
                    'title="Take the table away — nobody joined, so nobody wins">' +
                    'Withdraw</button>';
            }
            return '<div class="chat-arc-card' + (mySeat ? ' chat-arc-card--mine' : '') +
                '" role="button" tabindex="0" data-chat-arc-open="' + attr(g.id) + '">' +
                '<span class="chat-arc-card-top">' +
                    _arcWho(g.white, '♔', toMove && toMove === g.white) +
                    '<span class="chat-arc-vs">vs</span>' +
                    _arcWho(g.black, '♚', toMove && toMove === g.black) +
                '</span>' +
                '<span class="chat-arc-card-sub">' +
                    esc(g.variant) + ' · move ' + (Math.floor(g.ply / 2) + 1) +
                    (g.wager ? ' · 🪙' + g.wager + ' stake' : '') +
                    (g.isPrivate ? ' · private' : '') +
                    (g.partial ? ' · joined mid-game' : '') +
                '</span>' +
                badge +
                ((_selfIsMod()
                    ? '<span class="chat-arc-card-actions"><button type="button" class="chat-line-reply" ' +
                      'title="End this game for everyone (moderator)" ' +
                      'data-chat-arc-kill="' + attr(g.id) + '">🛑</button>' + (actions || '') + '</span>'
                    : (actions ? '<span class="chat-arc-card-actions">' + actions + '</span>' : ''))) +
            '</div>';
        }

        function section(title, list) {
            if (!list.length) return '';
            return '<div class="chat-arc-sectitle">' + esc(title) +
                '<span class="chat-arc-count">' + list.length + '</span></div>' +
                list.map(card).join('');
        }

        var empty = (!mine.length && !open.length && !live.length && !done.length);
        var yours = _arcMyTurnCount();

        // The bank was only ever drawn inside the slot machine, so the balance
        // was invisible everywhere else. It belongs on the front page.
        var sl = _slotState();
        if (!sl.bank) _slotLoadBank();
        var bank = sl.bank;

        function _stakeRowHtml() {
        var cur = (state.arcade && state.arcade.stake) || 0;
        return '<div class="chat-arc-stakes">' +
            '<span class="chat-arc-stakes-lab">table stake</span>' +
            [0, 5, 25, 100, 500].map(function (s) {
                return '<button type="button" class="chat-arc-btn' +
                    (s === cur ? ' chat-arc-btn--go' : '') + '" ' +
                    'data-chat-arc-stake="' + s + '">' +
                    (s ? '🪙' + s : 'none') + '</button>';
            }).join('') +
            '<span class="chat-arc-stakes-note">winner takes it from the loser\'s bank</span>' +
        '</div>';
    }

    function tile(attrs, icon, name, blurb) {
            return '<button class="chat-arc-tile" type="button" ' + attrs + '>' +
                '<span class="chat-arc-tile-icon">' + icon + '</span>' +
                '<span class="chat-arc-tile-name">' + esc(name) + '</span>' +
                '<span class="chat-arc-tile-blurb">' + esc(blurb) + '</span>' +
            '</button>';
        }

        return '<div class="chat-arc-lobby">' +
            '<div class="chat-arc-hero">' +
                '<div class="chat-arc-hero-top">' +
                    '<div>' +
                        '<div class="chat-arc-hero-title">The Arcade</div>' +
                        '<div class="chat-arc-hero-sub">No server anywhere. Every board ' +
                            'here is folded out of chat messages in this Soulseek room — ' +
                            'your client and your opponent\'s each work it out ' +
                            'independently.</div>' +
                    '</div>' +
                    '<div class="chat-arc-purse" title="Play money, kept on this ' +
                        'machine. Topped back up to the daily allowance at midnight if ' +
                        'you are below it — anything you win above it, you keep.">' +
                        '<span class="chat-arc-purse-coin">🪙</span>' +
                        '<span class="chat-arc-purse-amt">' +
                            (bank ? bank.balance.toLocaleString() : '·····') + '</span>' +
                        '<span class="chat-arc-purse-sub">play money</span>' +
                    '</div>' +
                '</div>' +
                (yours
                    ? '<div class="chat-arc-hero-turn">' + yours + ' game' +
                      (yours === 1 ? '' : 's') + ' waiting on you</div>'
                    : '') +
            '</div>' +
            (state.canSend
                ? _stakeRowHtml() +
                  '<div class="chat-arc-tiles">' +
                    tile('data-chat-arc-new="w"', '♟', 'Chess',
                         'turn by turn, no clock') +
                    tile('data-chat-arc-new="w" data-chat-arc-variant="connect4"', '🔴',
                         'Connect 4', 'four in a row, quick') +
                    tile('data-chat-arc-new="w" data-chat-arc-variant="battleship"', '🚢',
                         'Battleship', 'hidden fleets, checked at the end') +
                    tile('data-chat-arc-new="w" data-chat-arc-variant="othello"', '⚫',
                         'Othello', 'flank and flip — corners are king') +
                    tile('data-chat-arc-new="w" data-chat-arc-variant="gomoku"', '⚪',
                         'Gomoku', 'five stones in a row wins') +
                    tile('data-chat-arc-new="w" data-chat-arc-room="1"', '🗳',
                         'You vs the room', 'everyone else votes their move') +
                    tile('data-chat-triv-open', '🎓', 'Trivia',
                         'first right answer takes the pot') +
                    tile('data-chat-slot-open', '🎰', 'Slots',
                         'solo, against your own luck') +
                  '</div>' +
                  '<div class="chat-arc-tilefoot">Chess starts you as white — ' +
                      '<button class="chat-arc-inline" type="button" ' +
                      'data-chat-arc-new="b">open one as black</button> instead.</div>'
                : '<div class="chat-arc-note">Sending is admin-only on this server, ' +
                  'so you can watch every game here but not play.</div>') +
            (empty
                ? '<div class="chat-arc-blank">Nothing on the tables yet. Start ' +
                  'something and it shows up for everyone in the room.</div>'
                : section('Your games', mine) + section('Looking for an opponent', open) +
                  section('In progress', live) + section('Finished', done.slice(0, 10)) +
                  // Cold tables: one muted line, expandable — not forever-cards.
                  (cold.length
                      ? (state.arcade && state.arcade.showCold
                          ? section('Gone cold — nobody joined', cold)
                          : '<button class="chat-arc-cold-line" type="button" data-chat-arc-cold>' +
                            '🧊 ' + cold.length + ' cold table' + (cold.length === 1 ? '' : 's') +
                            ' hidden — nobody joined · show</button>')
                      : '')) +
            _arcLadderHtml() +
        '</div>';
    }

    // The room ladder. Persistent ratings with no server and no database of
    // record — a second fold over the results the first fold produced.
    function _arcLadderHtml() {
        var table = window.ChatGames.ratings(_gamesState());
        if (!table.length) return '';
        return '<div class="chat-arc-sectitle">Room ladder' +
                '<span class="chat-arc-count">' + table.length + '</span></div>' +
            '<div class="chat-arc-ladder">' +
                table.slice(0, 15).map(function (r, i) {
                    return '<div class="chat-arc-ladrow' +
                        (r.name === state.selfName ? ' chat-arc-ladrow--me' : '') + '">' +
                        '<span class="chat-arc-ladno">' + (i + 1) + '</span>' +
                        '<span class="chat-arc-ladname">' + esc(r.name) + '</span>' +
                        '<span class="chat-arc-ladwl">' + r.wins + 'W ' + r.losses +
                            'L ' + r.draws + 'D</span>' +
                        '<span class="chat-arc-ladelo">' + r.rating + '</span>' +
                    '</div>';
                }).join('') +
                '<div class="chat-arc-ladnote">Elo from ' + ELO_NOTE + '</div>' +
            '</div>';
    }
    var ELO_NOTE = 'finished games in this room, folded the same way on every ' +
        'client — everyone starts at 1200. Games this client joined mid-way ' +
        'are not rated: their seats were deduced, not observed.';

    function _arcBoardHtml(game) {
        if (game.variant === 'connect4') return _arcC4BoardHtml(game);
        if (game.variant === 'battleship') return _arcBsBoardHtml(game);
        if (game.variant === 'othello') return _arcOthBoardHtml(game);
        if (game.variant === 'gomoku') return _arcGmkBoardHtml(game);
        var E = window.ChessEngine;
        var CG = window.ChatGames;
        // game.fen is our OWN fold's output, not wire data — it has already
        // been through fromWireFEN if it was ever adopted.
        var pos = E.fromFEN(game.fen);
        if (!pos) return '<div class="chat-empty">This board could not be read.</div>';

        var arc = state.arcade;
        var mySeat = _arcSeat(game);
        var voting = _arcCanVote(game);
        var myMove = _arcMyMove(game);
        var active = _arcActive(game);
        // When voting, the side you are picking for is the room's, not yours.
        var actSide = voting ? game.roomSeat : mySeat;
        var sel = arc.sel;

        // Legal destinations from the selected square, so the board can show
        // dots instead of making people guess.
        var dests = {};
        if (sel >= 0 && active) {
            E.legalMoves(pos).forEach(function (m) {
                if (m.from === sel) dests[m.to] = 1;
            });
        }
        var lastMove = null;
        if (game.moves.length) {
            var lu = game.moves[game.moves.length - 1];
            lastMove = { from: E.fromAlg(lu.slice(0, 2)), to: E.fromAlg(lu.slice(2, 4)) };
        }
        var checkSq = -1;
        if (E.inCheck(pos, pos.turn)) {
            for (var s = 0; s < 128; s++) {
                if (!E.onBoard(s)) { s += 7; continue; }
                if (pos.board[s] === (pos.turn === 'w' ? 'K' : 'k')) { checkSq = s; break; }
            }
        }

        var ranks = [7, 6, 5, 4, 3, 2, 1, 0];
        var files = [0, 1, 2, 3, 4, 5, 6, 7];
        if (arc.flip) { ranks = ranks.slice().reverse(); files = files.slice().reverse(); }

        var cells = [];
        ranks.forEach(function (r) {
            files.forEach(function (f) {
                var sq = E.sqOf(f, r);
                var piece = pos.board[sq];
                var cls = 'chat-arc-sq chat-arc-sq--' + ((f + r) % 2 === 1 ? 'light' : 'dark');
                if (sq === sel) cls += ' chat-arc-sq--sel';
                if (dests[sq]) cls += piece ? ' chat-arc-sq--take' : ' chat-arc-sq--dest';
                if (lastMove && (sq === lastMove.from || sq === lastMove.to)) cls += ' chat-arc-sq--last';
                if (sq === checkSq) cls += ' chat-arc-sq--check';
                var mineToDrag = piece && active &&
                    E.colorOf(piece) === actSide && game.status === 'live';
                cells.push('<div class="' + cls + '" data-chat-arc-sq="' + sq + '"' +
                    (mineToDrag ? ' draggable="true"' : '') +
                    ' title="' + attr(E.toAlg(sq)) + '">' +
                    (piece ? '<span class="chat-arc-pc chat-arc-pc--' +
                        (E.colorOf(piece) === 'w' ? 'w' : 'b') + '">' +
                        ARC_GLYPH[piece] + '</span>' : '') +
                '</div>');
            });
        });

        // Move list in real algebraic notation. It replays from the game's
        // OWN start position, not from the opening: a game adopted mid-stream
        // only collects the moves that arrived after this client picked it up,
        // so numbering those from move 1 would claim the game began with them.
        var walk = E.fromFEN(game.startFen || E.START_FEN) || E.newGame();
        var rows = [];
        var pending = null;
        game.moves.forEach(function (uci) {
            var mv = E.uciToMove(walk, uci);
            if (!mv) return;
            var san = E.toSAN(walk, mv);
            var no = walk.fullmove;
            if (walk.turn === 'w') {
                pending = { no: no, w: san, b: '' };
                rows.push(pending);
            } else if (pending && pending.no === no) {
                pending.b = san;
            } else {
                pending = { no: no, w: '…', b: san };   // resumed on black's move
                rows.push(pending);
            }
            walk = E.makeMove(walk, mv);
        });
        var sanRows = rows.map(function (r) {
            return '<div class="chat-arc-moverow"><span class="chat-arc-moveno">' +
                r.no + '.</span><span>' + esc(r.w) + '</span><span>' +
                esc(r.b) + '</span></div>';
        }).join('') || '<div class="chat-arc-moverow chat-arc-moverow--none">no moves yet</div>';

        var toMove = CG.toMove(game);
        var statusLine;
        if (game.status === 'over') {
            statusLine = game.winner
                ? esc(game.winner) + ' wins by ' + esc(game.reason)
                : 'Draw — ' + esc(game.reason);
        } else if (game.desync) {
            statusLine = 'Frozen: a move arrived with a position that disagreed with ' +
                'this one, and neither can be proven right.';
        } else if (game.status === 'open') {
            statusLine = 'Waiting for an opponent to join.';
        } else if (myMove) {
            statusLine = 'Your move.' + (E.inCheck(pos, pos.turn) ? ' You are in check.' : '');
        } else if (CG.isRoomTurn(game)) {
            statusLine = 'The room is choosing' +
                (voting ? ' — pick a move to vote for it.'
                        : '. You are playing against them, so no ballot for you.');
        } else {
            statusLine = 'Waiting on ' + esc(toMove) + '.' +
                (E.inCheck(pos, pos.turn) ? ' They are in check.' : '');
        }

        var actions = '';
        if (state.canSend && mySeat && game.status === 'live') {
            actions += '<button class="chat-arc-btn" type="button" data-chat-arc-draw="' +
                attr(game.id) + '">' +
                (game.drawOffer && game.drawOffer !== state.selfName
                    ? 'Accept draw' : 'Offer draw') + '</button>';
            actions += '<button class="chat-arc-btn chat-arc-btn--bad" type="button" ' +
                'data-chat-arc-resign="' + attr(game.id) + '">Resign</button>';
        }
        if (state.canSend && !mySeat && game.status === 'open' &&
            (!game.isPrivate || game.invited === state.selfName)) {
            actions += '<button class="chat-arc-btn chat-arc-btn--go" type="button" ' +
                'data-chat-arc-join="' + attr(game.id) + '">Join this game</button>';
        }
        if (state.canSend && !mySeat && game.status === 'live' && game.stale) {
            actions += '<button class="chat-arc-btn" type="button" data-chat-arc-claim="' +
                attr(game.id) + '">Take the idle seat</button>';
        }
        if (_arcCanWithdraw(game)) {
            actions += '<button class="chat-arc-btn" type="button" data-chat-arc-cancel="' +
                attr(game.id) + '">Withdraw</button>';
        }

        var offer = (game.drawOffer && game.status === 'live')
            ? '<div class="chat-arc-note">' + esc(game.drawOffer) + ' offered a draw.</div>' : '';
        var partial = game.partial
            ? '<div class="chat-arc-note">Picked up mid-game — the room archive had ' +
              'already rolled past the opening, so the move list starts where this ' +
              'client joined (numbered from the real move, not from 1).</div>' : '';

        var promo = arc.promo
            ? '<div class="chat-arc-promo"><span>Promote to</span>' +
                ARC_PROMO.map(function (p) {
                    return '<button class="chat-arc-promobtn" type="button" ' +
                        'data-chat-arc-promo="' + p[0] + '">' + p[1] + '</button>';
                }).join('') +
                '<button class="chat-arc-btn" type="button" data-chat-arc-promo="">Cancel</button>' +
              '</div>'
            : '';

        return '<div class="chat-arc-board-wrap">' +
            '<div class="chat-arc-players">' +
                _arcWho(arc.flip ? game.white : game.black, arc.flip ? '♔' : '♚',
                        toMove === (arc.flip ? game.white : game.black),
                        game.roomSeat === (arc.flip ? 'w' : 'b')) +
            '</div>' +
            '<div class="chat-arc-board' + (game.status === 'over' ? ' chat-arc-board--over' : '') +
                '" data-chat-arc-board="' + attr(game.id) + '">' + cells.join('') + '</div>' +
            '<div class="chat-arc-players">' +
                _arcWho(arc.flip ? game.black : game.white, arc.flip ? '♚' : '♔',
                        toMove === (arc.flip ? game.black : game.white),
                        game.roomSeat === (arc.flip ? 'b' : 'w')) +
                '<button class="chat-arc-btn chat-arc-btn--slim" type="button" ' +
                    'data-chat-arc-flip title="Flip the board">⇅</button>' +
            '</div>' +
            promo + offer + partial + _arcBallotHtml(game) +
            '<div class="chat-arc-status">' + statusLine + '</div>' +
            _arcAckHtml(game) +
            (function () {
                var extra = actions + _arcSyncActions(game);
                return extra ? '<div class="chat-arc-actions">' + extra + '</div>' : '';
            })() +
            '<div class="chat-arc-moves">' + sanRows + '</div>' +
            '<div class="chat-arc-exports">' +
                '<button class="chat-arc-btn chat-arc-btn--slim" type="button" ' +
                    'data-chat-arc-pgn="' + attr(game.id) + '" ' +
                    'title="Download a .pgn — it opens in Lichess like any other game">' +
                    '⤓ PGN</button>' +
                '<button class="chat-arc-btn chat-arc-btn--slim" type="button" ' +
                    'data-chat-arc-pgncopy="' + attr(game.id) + '">Copy PGN</button>' +
            '</div>' +
            _arcRevealHtml(game) +
        '</div>';
    }

    // The trick, made visible. People assume there is a server; showing the
    // actual chat messages the board was computed from is the whole point of
    // building it this way.
    function _arcRevealHtml(game) {
        var carriers = _arcCarriers(game.id);
        var open = !!(state.arcade && state.arcade.reveal);
        var head = '<button class="chat-arc-reveal-btn" type="button" data-chat-arc-reveal>' +
            '⚡ no server · this board folded from ' + carriers.length +
            ' room message' + (carriers.length === 1 ? '' : 's') +
            '<span class="chat-arc-reveal-caret">' + (open ? '⌃' : '⌄') + '</span></button>';
        if (!open) return '<div class="chat-arc-reveal">' + head + '</div>';
        var rows = carriers.slice(-40).map(function (e) {
            return '<div class="chat-arc-rawrow">' +
                '<span class="chat-arc-rawwho">' + esc(e.username || '?') + '</span>' +
                '<code>' + esc(JSON.stringify(e.p)) + '</code>' +
            '</div>';
        }).join('') || '<div class="chat-arc-rawrow">nothing in this client\'s log</div>';
        return '<div class="chat-arc-reveal chat-arc-reveal--open">' + head +
            '<div class="chat-arc-rawnote">These are real messages in the Soulseek ' +
                'room. Every SoulSync client folds them into the same position; a ' +
                'plain Soulseek client just sees them as text it ignores.</div>' +
            '<div class="chat-arc-raw">' + rows + '</div>' +
        '</div>';
    }

    // ── The slot machine ────────────────────────────────────────────────
    //
    // The one Arcade game with no opponent, which is exactly why it can use
    // the play-money bank: there is nobody to defraud but yourself. It is NOT
    // on the protocol bus — a solo pull is nobody else's business and would
    // only be noise in the room.
    //
    // The reel strip is weighted by repetition rather than by a probability
    // table: the symbol you want most simply appears least. Easier to reason
    // about, and the odds are visible in the payout list rather than buried.
    var SLOT_REEL = [
        '🍒', '🍒', '🍒', '🍒', '🍒', '🍒',
        '🍋', '🍋', '🍋', '🍋', '🍋',
        '🔔', '🔔', '🔔', '🔔',
        '💿', '💿', '💿',
        '🎧', '🎧',
        '💎',
    ];
    var SLOT_PAYS = {
        '💎': { three: 200, two: 12 },
        '🎧': { three: 60, two: 6 },
        '💿': { three: 25, two: 3 },
        '🔔': { three: 12, two: 2 },
        '🍋': { three: 6, two: 1 },
        '🍒': { three: 4, two: 1 },
    };
    var SLOT_STAKES = [5, 25, 100, 500];

    function _slotPayout(reels, stake) {
        var a = reels[0], b = reels[1], c = reels[2];
        if (a === b && b === c) return stake * (SLOT_PAYS[a].three || 0);
        // Two of a kind only pays on the first two reels — the usual rule, and
        // it keeps the maths obvious when you are staring at the result.
        if (a === b) return stake * (SLOT_PAYS[a].two || 0);
        return 0;
    }

    function _slotSpin() {
        var out = [];
        for (var i = 0; i < 3; i++) {
            out.push(SLOT_REEL[Math.floor(Math.random() * SLOT_REEL.length)]);
        }
        return out;
    }

    function _slotState() {
        if (!state.arcade.slot) {
            state.arcade.slot = { reels: ['🍒', '🍋', '🔔'], stake: 25, spinning: false,
                                  last: null, bank: null };
        }
        return state.arcade.slot;
    }

    var _bankPending = 0;      // in flight, or backing off after a failure

    // Rendering triggers this, so it MUST be idempotent and must not retry in
    // a loop: without the guard a failing endpoint meant one request per
    // render, which is the request flood all over again.
    function _slotLoadBank(then) {
        var now = Date.now();
        if (_bankPending && now - _bankPending < 15000) { if (then) then(); return; }
        _bankPending = now;
        getJSON('/api/chat/arcade/bank').then(function (r) {
            if (r && r.ok && r.body && typeof r.body.balance === 'number') {
                _slotState().bank = r.body;
                _bankPending = 0;                  // got it; free to refresh later
            }
            if (then) then();
            else renderArcade();
        }).catch(function () {
            if (then) then();                      // stays backed off for 15s
        });
    }

    function _slotPull() {
        var sl = _slotState();
        if (sl.spinning) return;
        if (!sl.bank || sl.bank.balance < sl.stake) {
            if (typeof showToast === 'function') showToast('Not enough in the bank', 'error');
            return;
        }
        sl.spinning = true;
        sl.last = null;
        renderArcade();

        // Debit first, then settle the win. Two calls rather than one net
        // adjustment so a spin that is interrupted halfway costs you the
        // stake rather than silently paying out.
        postJSON('/api/chat/arcade/bank', { delta: -sl.stake }).then(function (r) {
            if (!r.ok) {
                sl.spinning = false;
                if (typeof showToast === 'function') {
                    showToast((r.body && r.body.error) || 'The bank said no', 'error');
                }
                renderArcade();
                return;
            }
            sl.bank = r.body;
            var reels = _slotSpin();
            var win = _slotPayout(reels, sl.stake);
            // A beat of spinning so it reads as a pull rather than a number
            // changing. Purely cosmetic; the result is already decided.
            var ticks = 0;
            var timer = setInterval(function () {
                _slotState().reels = _slotSpin();
                renderArcade();
                if (++ticks < 8) return;
                clearInterval(timer);
                var s2 = _slotState();
                s2.reels = reels;
                s2.spinning = false;
                s2.last = { win: win, stake: sl.stake };
                if (win > 0) {
                    postJSON('/api/chat/arcade/bank', { delta: win }).then(function (r2) {
                        if (r2.ok) s2.bank = r2.body;
                        renderArcade();
                    });
                } else {
                    renderArcade();
                }
            }, 90);
        });
    }

    function _arcSlotHtml() {
        var sl = _slotState();
        if (!sl.bank) _slotLoadBank();
        var bal = sl.bank ? sl.bank.balance : null;
        var last = sl.last;
        return '<div class="chat-slot-wrap">' +
            '<div class="chat-slot-cab' + (sl.spinning ? ' chat-slot-cab--spin' : '') + '">' +
                '<div class="chat-slot-reels">' +
                    sl.reels.map(function (r) {
                        return '<div class="chat-slot-reel">' + r + '</div>';
                    }).join('') +
                '</div>' +
                '<div class="chat-slot-verdict' +
                    (last && last.win > 0 ? ' chat-slot-verdict--win' : '') + '">' +
                    (sl.spinning ? '…'
                        : last ? (last.win > 0 ? '+' + last.win.toLocaleString() : 'no luck')
                               : 'pull to play') +
                '</div>' +
            '</div>' +
            '<div class="chat-slot-bank">' +
                '<span class="chat-slot-balance">' +
                    (bal === null ? '…' : bal.toLocaleString()) + '</span>' +
                '<span class="chat-slot-banklabel">in the bank · back up to ' +
                    (sl.bank ? sl.bank.allowance.toLocaleString() : '10,000') +
                    ' at midnight if you drop below · winnings are yours</span>' +
            '</div>' +
            '<div class="chat-slot-stakes">' +
                SLOT_STAKES.map(function (v) {
                    return '<button class="chat-arc-btn' + (v === sl.stake ? ' chat-arc-btn--go' : '') +
                        '" type="button" data-chat-slot-stake="' + v + '">' + v + '</button>';
                }).join('') +
                '<button class="chat-arc-btn chat-arc-btn--go chat-slot-pull" type="button" ' +
                    'data-chat-slot-pull' + (sl.spinning ? ' disabled' : '') + '>Pull</button>' +
            '</div>' +
            '<div class="chat-slot-pays">' +
                Object.keys(SLOT_PAYS).map(function (sym) {
                    var p = SLOT_PAYS[sym];
                    return '<div class="chat-slot-payrow"><span>' + sym + sym + sym + '</span>' +
                        '<b>' + p.three + '×</b><span class="chat-slot-paytwo">' + sym + sym +
                        '</span><b>' + p.two + '×</b></div>';
                }).join('') +
            '</div>' +
            '<div class="chat-arc-note">Play money, kept on this machine only. It is ' +
                'not worth anything and cannot be staked against another player — ' +
                'nobody else can see it, so nobody else could trust it.</div>' +
        '</div>';
    }

    // ── Battleship ──────────────────────────────────────────────────────
    //
    // Your fleet never goes on the wire until the reveal, so it lives in
    // localStorage keyed by game id: it has to survive a reload, and it must
    // not be reconstructible by anyone else from the message stream.
    //
    // Answers are AUTOMATIC. Your client can see your own board, so it
    // replies truthfully to a shot without asking you anything — no "were you
    // hit?" prompt to sit unanswered for an hour, and no opportunity to lie by
    // accident. A determined cheater edits their client, which is the threat
    // model this game already accepts and catches at the reveal.
    var BS_W = 10, BS_H = 10;
    var BS_FLEET = [
        { id: '1', name: 'Carrier', len: 5 },
        { id: '2', name: 'Battleship', len: 4 },
        { id: '3', name: 'Cruiser', len: 3 },
        { id: '4', name: 'Submarine', len: 3 },
        { id: '5', name: 'Destroyer', len: 2 },
    ];
    var _bsAnswered = {};      // gid|shotCount -> already replied

    function _bsCellName(i) {
        return 'abcdefghij'[i % BS_W] + String(Math.floor(i / BS_W) + 1);
    }

    function _bsSecret(gid, save) {
        var key = 'chat_bs_' + gid;
        try {
            if (save === undefined) {
                var raw = localStorage.getItem(key);
                return raw ? JSON.parse(raw) : null;
            }
            localStorage.setItem(key, JSON.stringify(save));
            return save;
        } catch (e) { return null; }
    }

    // Can `len` cells starting at `idx` going `horiz` sit on this board?
    function _bsFits(cells, idx, horiz, len) {
        var col = idx % BS_W, row = Math.floor(idx / BS_W);
        if (horiz && col + len > BS_W) return false;
        if (!horiz && row + len > BS_H) return false;
        for (var i = 0; i < len; i++) {
            if (cells[idx + (horiz ? i : i * BS_W)] !== '.') return false;
        }
        return true;
    }

    function _bsRandomBoard() {
        var cells = new Array(BS_W * BS_H).fill('.');
        for (var f = 0; f < BS_FLEET.length; f++) {
            var ship = BS_FLEET[f];
            for (var tries = 0; tries < 500; tries++) {
                var horiz = Math.random() < 0.5;
                var idx = Math.floor(Math.random() * BS_W * BS_H);
                if (!_bsFits(cells, idx, horiz, ship.len)) continue;
                for (var i = 0; i < ship.len; i++) {
                    cells[idx + (horiz ? i : i * BS_W)] = ship.id;
                }
                break;
            }
        }
        return cells.join('');
    }

    // The placement in progress, before it is committed.
    function _bsDraft() {
        var arc = state.arcade;
        if (!arc.bs) {
            // Empty, not a random fleet. Starting full meant there was nothing
            // left to place: clicking open water did nothing, and rotate
            // looked broken because no placement ever happened. Random is one
            // button away for anyone who does not want to lay it out.
            arc.bs = { board: '.'.repeat(BS_W * BS_H), next: 0, horiz: true };
        }
        return arc.bs;
    }

    function _bsPlacedCount(board) {
        var n = 0;
        for (var f = 0; f < BS_FLEET.length; f++) {
            if (board.indexOf(BS_FLEET[f].id) >= 0) n++;
        }
        return n;
    }

    // Answer a shot at us, truthfully, from the board only we hold.
    function _bsAutoAnswer(game) {
        if (!game || game.variant !== 'battleship' || game.status !== 'live') return;
        var seat = _arcSeat(game);
        if (!seat || !state.canSend) return;
        var st;
        try { st = JSON.parse(game.fen); } catch (e) { return; }
        if (st.pending !== seat) return;
        var secret = _bsSecret(game.id);
        if (!secret || !secret.board) return;
        var foe = seat === 'w' ? 'b' : 'w';
        var shots = st.shots[foe] || [];
        var idx = shots[shots.length - 1];
        if (idx === undefined) return;
        var key = game.id + '|' + shots.length;
        if (_bsAnswered[key]) return;                 // one answer per shot

        var board = secret.board;
        var res = 'miss';
        if (board[idx] !== '.') {
            var ship = board[idx], hit = 0, len = 0;
            for (var i = 0; i < board.length; i++) {
                if (board[i] !== ship) continue;
                len++;
                if (shots.indexOf(i) >= 0) hit++;
            }
            res = hit >= len ? 'sunk' : 'hit';
        }
        // Marked answered only when the send actually went out — marking
        // first meant a send arcMove dropped (rate floor, dead game) was
        // swallowed forever and the game sat at "Answering…" until a reload.
        // The tick runs on every bus event, so a miss here simply retries.
        if (arcMove(game.id, 'r:' + res)) _bsAnswered[key] = 1;
    }

    // Once our fleet is down we owe a reveal; send it without ceremony.
    function _bsAutoReveal(game) {
        if (!game || game.variant !== 'battleship' || game.status !== 'live') return;
        var seat = _arcSeat(game);
        if (!seat || !state.canSend) return;
        var st;
        try { st = JSON.parse(game.fen); } catch (e) { return; }
        if (!st.sunkAll || st.reveal[seat]) return;
        var secret = _bsSecret(game.id);
        if (!secret || !secret.board || !secret.salt) return;
        arcMove(game.id, 'v:' + secret.salt + ':' + secret.board);
    }

    function _bsTick() {
        if (!_arcReady()) return;
        var st = _gamesState();
        st.order.forEach(function (id) {
            var g = st.games[id];
            if (!g || g.variant !== 'battleship') return;
            _bsAutoAnswer(g);
            _bsAutoReveal(g);
        });
    }

    function _arcBsBoardHtml(game) {
        var arc = state.arcade;
        var seat = _arcSeat(game);
        var st;
        try { st = JSON.parse(game.fen); } catch (e) {
            return '<div class="chat-empty">This board could not be read.</div>';
        }
        var foe = seat === 'w' ? 'b' : 'w';
        var secret = _bsSecret(game.id);
        var committed = seat ? !!st.commits[seat] : false;

        // ── setup ──
        if (seat && !committed) {
            var draft = _bsDraft();
            var placed = _bsPlacedCount(draft.board);
            var nextShip = BS_FLEET[draft.next];
            var cells = [];
            for (var i = 0; i < BS_W * BS_H; i++) {
                var v = draft.board[i];
                cells.push('<div class="chat-bs-cell' + (v !== '.' ? ' chat-bs-cell--ship' : '') +
                    '" data-chat-bs-place="' + i + '" title="' + attr(_bsCellName(i)) + '"></div>');
            }
            return '<div class="chat-arc-board-wrap">' +
                '<div class="chat-bs-title">Lay out your fleet</div>' +
                '<div class="chat-bs-grid chat-bs-grid--own">' + cells.join('') + '</div>' +
                '<div class="chat-bs-fleetbar">' +
                    BS_FLEET.map(function (sh, n) {
                        var done = draft.board.indexOf(sh.id) >= 0;
                        return '<span class="chat-bs-ship' + (done ? ' chat-bs-ship--set' : '') +
                            (n === draft.next && !done ? ' chat-bs-ship--next' : '') + '">' +
                            esc(sh.name) + ' <b>' + sh.len + '</b></span>';
                    }).join('') +
                '</div>' +
                '<div class="chat-arc-actions">' +
                    '<button class="chat-arc-btn" type="button" data-chat-bs-random>🎲 Random</button>' +
                    '<button class="chat-arc-btn" type="button" data-chat-bs-rotate ' +
                        'title="Which way the next ship lies">↻ Rotate — laying ' +
                        (draft.horiz ? '↔ across' : '↕ down') + '</button>' +
                    '<button class="chat-arc-btn" type="button" data-chat-bs-clear>Clear</button>' +
                    (placed === BS_FLEET.length
                        ? '<button class="chat-arc-btn chat-arc-btn--go" type="button" ' +
                          'data-chat-bs-commit="' + attr(game.id) + '">Commit fleet</button>'
                        : '') +
                    // Setup returns early with its own action row, so without
                    // this the creator lands here and has no way back out.
                    (_arcCanWithdraw(game)
                        ? '<button class="chat-arc-btn" type="button" ' +
                          'data-chat-arc-cancel="' + attr(game.id) + '">Withdraw</button>'
                        : '') +
                    (game.status === 'live'
                        ? '<button class="chat-arc-btn chat-arc-btn--bad" type="button" ' +
                          'data-chat-arc-resign="' + attr(game.id) + '">Resign</button>'
                        : '') +
                '</div>' +
                '<div class="chat-arc-note">Your layout stays on this machine. Only a ' +
                    'fingerprint of it goes to the room now — the fleet itself is revealed ' +
                    'at the end, and every answer you gave is checked against it.</div>' +
                (nextShip && placed < BS_FLEET.length
                    ? '<div class="chat-arc-status">Place your ' + esc(nextShip.name) +
                      ' (' + nextShip.len + ' cells, lying ' +
                      (draft.horiz ? 'across' : 'down') + ') — click where it starts. ' +
                      'Click a placed ship to pick it up again.</div>'
                    : '<div class="chat-arc-status">Fleet ready — commit when you are.</div>') +
                _arcRevealHtml(game) +
            '</div>';
        }

        // ── waiting for the opponent to finish placing ──
        var bothIn = st.commits.w && st.commits.b;
        var myShots = seat ? (st.shots[seat] || []) : (st.shots.w || []);
        var myResults = seat ? (st.results[seat] || []) : (st.results.w || []);
        var theirShots = seat ? (st.shots[foe] || []) : (st.shots.b || []);

        // their waters — what we have fired at
        var theirs = [];
        for (var t = 0; t < BS_W * BS_H; t++) {
            var at2 = myShots.indexOf(t);
            var mark = at2 >= 0 ? (myResults[at2] || '') : '';
            var cls = 'chat-bs-cell';
            if (mark === 'miss') cls += ' chat-bs-cell--miss';
            else if (mark === 'hit' || mark === 'sunk') cls += ' chat-bs-cell--hit';
            var live = bothIn && seat && !st.pending && !st.sunkAll &&
                       st.turn === seat && at2 < 0 && game.status === 'live' && state.canSend;
            theirs.push('<div class="' + cls + (live ? ' chat-bs-cell--fire' : '') + '"' +
                (live ? ' data-chat-bs-fire="' + t + '"' : '') +
                ' title="' + attr(_bsCellName(t)) + '"></div>');
        }
        // our waters — our fleet plus where they have fired
        var ourBoard = (secret && secret.board) || '.'.repeat(BS_W * BS_H);
        var ours = [];
        for (var o = 0; o < BS_W * BS_H; o++) {
            var cls2 = 'chat-bs-cell';
            if (ourBoard[o] !== '.') cls2 += ' chat-bs-cell--ship';
            if (theirShots.indexOf(o) >= 0) {
                cls2 += ourBoard[o] !== '.' ? ' chat-bs-cell--hit' : ' chat-bs-cell--miss';
            }
            ours.push('<div class="' + cls2 + '" title="' + attr(_bsCellName(o)) + '"></div>');
        }

        var status;
        if (game.status === 'over') {
            status = game.reason === 'cheating'
                ? esc(game.winner) + ' wins — the other fleet did not match what was committed'
                : esc(game.winner) + ' wins — fleet sunk';
        } else if (!bothIn) {
            status = 'Waiting for the other fleet to be laid out.';
        } else if (st.sunkAll) {
            status = 'All ships down. Both fleets are being revealed and checked.';
        } else if (st.pending) {
            status = st.pending === seat ? 'Answering…' : 'Waiting for their answer.';
        } else if (seat && st.turn === seat) {
            status = 'Your shot — pick a square in their waters.';
        } else {
            status = 'Waiting on ' + esc(window.ChatGames.toMove(game) || 'them') + '.';
        }

        var actions = '';
        if (state.canSend && seat && game.status === 'live') {
            actions += '<button class="chat-arc-btn chat-arc-btn--bad" type="button" ' +
                'data-chat-arc-resign="' + attr(game.id) + '">Resign</button>';
        }
        if (state.canSend && !seat && game.status === 'open') {
            actions += '<button class="chat-arc-btn chat-arc-btn--go" type="button" ' +
                'data-chat-arc-join="' + attr(game.id) + '">Join this game</button>';
        }
        // The board had no way out at all: no withdraw before anyone joined,
        // and nothing to take an idle seat with.
        if (_arcCanWithdraw(game)) {
            actions += '<button class="chat-arc-btn" type="button" data-chat-arc-cancel="' +
                attr(game.id) + '">Withdraw</button>';
        }
        if (state.canSend && !seat && game.status === 'live' && game.stale) {
            actions += '<button class="chat-arc-btn" type="button" data-chat-arc-claim="' +
                attr(game.id) + '">Take the idle seat</button>';
        }

        return '<div class="chat-arc-board-wrap">' +
            '<div class="chat-bs-title">Their waters</div>' +
            '<div class="chat-bs-grid chat-bs-grid--foe">' + theirs.join('') + '</div>' +
            '<div class="chat-bs-title">Your fleet' +
                (secret ? '' : ' <span class="chat-bs-lost">(this browser no longer has your ' +
                 'layout — it was stored locally)</span>') + '</div>' +
            '<div class="chat-bs-grid chat-bs-grid--own">' + ours.join('') + '</div>' +
            '<div class="chat-arc-status">' + status + '</div>' +
            (actions ? '<div class="chat-arc-actions">' + actions + _arcSyncActions(game) + '</div>'
                     : '') +
            _arcRevealHtml(game) +
        '</div>';
    }

    // Connect 4 board. Same shell as chess — players line, board, status,
    // actions, exports, reveal — so everything around it is shared. The board
    // itself is a 7x6 grid where the whole COLUMN is the click target: you
    // drop into a column, you do not place into a cell, and making people aim
    // at the right cell would be pretending otherwise.
    function _arcC4BoardHtml(game) {
        var CG = window.ChatGames;
        var cols = CG.C4_COLS, rows = CG.C4_ROWS;
        var body = String(game.fen || '').split(' ')[0] || '';
        var mySeat = _arcSeat(game);
        var myMove = _arcMyMove(game);
        var voting = _arcCanVote(game);
        var active = _arcActive(game);

        var cells = [];
        // Render top row first so the grid reads the way the board looks.
        for (var r = rows - 1; r >= 0; r--) {
            for (var c = 0; c < cols; c++) {
                var who = body[r * cols + c] || '.';
                var full = (body[(rows - 1) * cols + c] || '.') !== '.';
                var playable = active && !full && game.status === 'live' && state.canSend;
                cells.push('<div class="chat-arc-c4cell' +
                    (playable ? ' chat-arc-c4cell--live' : '') + '"' +
                    (playable ? ' data-chat-arc-col="' + c + '"' : '') + '>' +
                    '<span class="chat-arc-disc' +
                        (who === 'w' ? ' chat-arc-disc--w' : (who === 'b' ? ' chat-arc-disc--b' : '')) +
                    '"></span></div>');
            }
        }

        var toMove = CG.toMove(game);
        var statusLine;
        if (game.status === 'over') {
            statusLine = game.winner
                ? esc(game.winner) + ' wins — ' + esc(game.reason)
                : 'Draw — ' + esc(game.reason);
        } else if (game.desync) {
            statusLine = 'Frozen: a move arrived with a position that disagreed with ' +
                'this one, and neither can be proven right.';
        } else if (game.status === 'open') {
            statusLine = 'Waiting for an opponent to join.';
        } else if (myMove) {
            statusLine = 'Your move — pick a column.';
        } else if (CG.isRoomTurn(game)) {
            statusLine = 'The room is choosing' +
                (voting ? ' — pick a column to vote for it.'
                        : '. You are playing against them, so no ballot for you.');
        } else {
            statusLine = 'Waiting on ' + esc(toMove) + '.';
        }

        var actions = '';
        if (state.canSend && mySeat && game.status === 'live') {
            actions += '<button class="chat-arc-btn chat-arc-btn--bad" type="button" ' +
                'data-chat-arc-resign="' + attr(game.id) + '">Resign</button>';
        }
        if (state.canSend && !mySeat && game.status === 'open' &&
            (!game.isPrivate || game.invited === state.selfName)) {
            actions += '<button class="chat-arc-btn chat-arc-btn--go" type="button" ' +
                'data-chat-arc-join="' + attr(game.id) + '">Join this game</button>';
        }
        if (state.canSend && !mySeat && game.status === 'live' && game.stale) {
            actions += '<button class="chat-arc-btn" type="button" data-chat-arc-claim="' +
                attr(game.id) + '">Take the idle seat</button>';
        }
        var partial = game.partial
            ? '<div class="chat-arc-note">Picked up mid-game — the room archive had ' +
              'already rolled past the start.</div>' : '';

        return '<div class="chat-arc-board-wrap">' +
            '<div class="chat-arc-players">' +
                _arcWho(game.black, '🟡', toMove === game.black, game.roomSeat === 'b') +
            '</div>' +
            '<div class="chat-arc-c4board' +
                (game.status === 'over' ? ' chat-arc-board--over' : '') + '">' +
                cells.join('') + '</div>' +
            '<div class="chat-arc-players">' +
                _arcWho(game.white, '🔴', toMove === game.white, game.roomSeat === 'w') +
            '</div>' +
            partial + _arcBallotHtml(game) +
            '<div class="chat-arc-status">' + statusLine + '</div>' +
            _arcAckHtml(game) +
            (function () {
                var extra = actions + _arcSyncActions(game);
                return extra ? '<div class="chat-arc-actions">' + extra + '</div>' : '';
            })() +
            _arcRevealHtml(game) +
        '</div>';
    }

    // Click a square to drop the next unplaced ship there.
    function _bsPlaceAt(idx) {
        var draft = _bsDraft();
        if (!(idx >= 0 && idx < BS_W * BS_H)) return;
        // Clicking a placed ship picks it back up, so a misplacement is one
        // click to undo rather than a full Clear.
        var occupant = draft.board[idx];
        if (occupant !== '.') {
            draft.board = draft.board.split('').map(function (c) {
                return c === occupant ? '.' : c;
            }).join('');
            for (var f = 0; f < BS_FLEET.length; f++) {
                if (BS_FLEET[f].id === occupant) draft.next = f;
            }
            renderArcade();
            return;
        }
        // Next ship still needing a home.
        var ship = null;
        for (var i = 0; i < BS_FLEET.length; i++) {
            if (draft.board.indexOf(BS_FLEET[i].id) < 0) { ship = BS_FLEET[i]; draft.next = i; break; }
        }
        if (!ship) return;
        var cells = draft.board.split('');
        if (!_bsFits(cells, idx, draft.horiz, ship.len)) {
            if (typeof showToast === 'function') showToast('It does not fit there', 'error');
            return;
        }
        for (var j = 0; j < ship.len; j++) {
            cells[idx + (draft.horiz ? j : j * BS_W)] = ship.id;
        }
        draft.board = cells.join('');
        renderArcade();
    }

    // Publish only the fingerprint. The fleet itself stays here until the end.
    function _bsCommit(gid) {
        var draft = _bsDraft();
        if (_bsPlacedCount(draft.board) !== BS_FLEET.length) return;
        var H = window.ChatHash;
        if (!H) return;
        var salt = H.salt();
        // Secret saved BEFORE the send — a crash in between must never lose
        // the fleet the room now holds a fingerprint of. The draft is only
        // cleared when the commit actually went out, so a dropped send
        // leaves the layout on screen to commit again instead of wiping it.
        _bsSecret(gid, { salt: salt, board: draft.board });
        if (arcMove(gid, 'c:' + H.commit(salt, draft.board))) {
            state.arcade.bs = null;
        }
    }

    // Shared chrome for the two placed-piece boards: status line + actions.
    function _arcCellStatus(game, verb) {
        var CG = window.ChatGames;
        if (game.status === 'over') {
            return game.winner ? esc(game.winner) + ' wins — ' + esc(game.reason)
                               : 'Draw — ' + esc(game.reason);
        }
        if (game.desync) {
            return 'Frozen: a move arrived with a position that disagreed with ' +
                'this one, and neither can be proven right.';
        }
        if (game.status === 'open') return 'Waiting for an opponent to join.';
        if (_arcMyMove(game)) return 'Your move — ' + verb + '.';
        if (CG.isRoomTurn(game)) {
            return 'The room is choosing' +
                (_arcCanVote(game) ? ' — ' + verb + ' to vote for it.'
                                   : '. You are playing against them, so no ballot for you.');
        }
        return 'Waiting on ' + esc(CG.toMove(game)) + '.';
    }

    function _arcCellActions(game) {
        var mySeat = _arcSeat(game);
        var actions = '';
        if (state.canSend && mySeat && game.status === 'live') {
            actions += '<button class="chat-arc-btn chat-arc-btn--bad" type="button" ' +
                'data-chat-arc-resign="' + attr(game.id) + '">Resign</button>';
        }
        if (state.canSend && !mySeat && game.status === 'open' &&
            (!game.isPrivate || game.invited === state.selfName)) {
            actions += '<button class="chat-arc-btn chat-arc-btn--go" type="button" ' +
                'data-chat-arc-join="' + attr(game.id) + '">Join this game</button>';
        }
        if (state.canSend && !mySeat && game.status === 'live' && game.stale) {
            actions += '<button class="chat-arc-btn" type="button" data-chat-arc-claim="' +
                attr(game.id) + '">Take the idle seat</button>';
        }
        return actions;
    }

    function _arcOthBoardHtml(game) {
        var CG = window.ChatGames;
        var body = String(game.fen || '').split(' ')[0] || '';
        var turnChar = String(game.fen || '').split(' ')[1] || 'w';
        var live = game.status === 'live';
        var acting = live && _arcActive(game) && state.canSend;
        var legal = {};
        if (acting) {
            CG.othelloLegal(game.fen, turnChar).forEach(function (i) { legal[i] = 1; });
        }
        var stuck = acting && !Object.keys(legal).length;
        var cells = [];
        for (var i = 0; i < 64; i++) {
            var who = body[i] || '.';
            var playable = !!legal[i];
            // seat 'w' OPENS and plays the black discs (Othello's first mover)
            cells.push('<div class="chat-arc-othcell' +
                (playable ? ' chat-arc-othcell--live' : '') + '"' +
                (playable ? ' data-chat-arc-cell="' + i + '"' : '') + '>' +
                (who === 'w' ? '<span class="chat-arc-othdisc chat-arc-othdisc--dark"></span>'
                 : who === 'b' ? '<span class="chat-arc-othdisc chat-arc-othdisc--light"></span>'
                 : (playable ? '<span class="chat-arc-othdot"></span>' : '')) +
            '</div>');
        }
        var score = CG.othelloScore(game.fen);
        var toMove = CG.toMove(game);
        var actions = _arcCellActions(game);
        if (stuck) {
            actions = '<button class="chat-arc-btn chat-arc-btn--go" type="button" ' +
                'data-chat-arc-pass="' + attr(game.id) + '" ' +
                'title="No legal move anywhere — the turn passes">No moves — pass</button>' + actions;
        }
        return '<div class="chat-arc-board-wrap">' +
            '<div class="chat-arc-players">' +
                _arcWho(game.black, '⚪', toMove === game.black, game.roomSeat === 'b') +
                '<span class="chat-arc-othscore">⚫ ' + score.w + ' · ' + score.b + ' ⚪</span>' +
            '</div>' +
            '<div class="chat-arc-othboard' +
                (game.status === 'over' ? ' chat-arc-board--over' : '') + '">' +
                cells.join('') + '</div>' +
            '<div class="chat-arc-players">' +
                _arcWho(game.white, '⚫', toMove === game.white, game.roomSeat === 'w') +
            '</div>' +
            (game.partial ? '<div class="chat-arc-note">Picked up mid-game — the room ' +
                'archive had already rolled past the start.</div>' : '') +
            _arcBallotHtml(game) +
            '<div class="chat-arc-status">' + _arcCellStatus(game, 'pick a glowing square') + '</div>' +
            _arcAckHtml(game) +
            (function () {
                var extra = actions + _arcSyncActions(game);
                return extra ? '<div class="chat-arc-actions">' + extra + '</div>' : '';
            })() +
        '</div>';
    }

    function _arcGmkBoardHtml(game) {
        var CG = window.ChatGames;
        var body = String(game.fen || '').split(' ')[0] || '';
        var live = game.status === 'live';
        var acting = live && _arcActive(game) && state.canSend;
        // Ring the newest stone so the board reads at a glance.
        var last = -1;
        if (game.moves && game.moves.length) {
            var lm = game.moves[game.moves.length - 1];
            var lmStr = String((lm && lm.m) != null ? lm.m : lm);
            if (/^\d{1,3}$/.test(lmStr)) last = parseInt(lmStr, 10);
        }
        var cells = [];
        for (var i = 0; i < 225; i++) {
            var who = body[i] || '.';
            var playable = acting && who === '.';
            cells.push('<div class="chat-arc-gmkcell' +
                (playable ? ' chat-arc-gmkcell--live' : '') + '"' +
                (playable ? ' data-chat-arc-cell="' + i + '"' : '') + '>' +
                (who !== '.'
                    ? '<span class="chat-arc-stone chat-arc-stone--' +
                      (who === 'w' ? 'dark' : 'light') +
                      (i === last ? ' chat-arc-stone--last' : '') + '"></span>'
                    : '') +
            '</div>');
        }
        var toMove = CG.toMove(game);
        return '<div class="chat-arc-board-wrap">' +
            '<div class="chat-arc-players">' +
                _arcWho(game.black, '⚪', toMove === game.black, game.roomSeat === 'b') +
            '</div>' +
            '<div class="chat-arc-gmkboard' +
                (game.status === 'over' ? ' chat-arc-board--over' : '') + '">' +
                cells.join('') + '</div>' +
            '<div class="chat-arc-players">' +
                _arcWho(game.white, '⚫', toMove === game.white, game.roomSeat === 'w') +
            '</div>' +
            (game.partial ? '<div class="chat-arc-note">Picked up mid-game — the room ' +
                'archive had already rolled past the start.</div>' : '') +
            _arcBallotHtml(game) +
            '<div class="chat-arc-status">' + _arcCellStatus(game, 'place a stone') + '</div>' +
            _arcAckHtml(game) +
            (function () {
                var extra = _arcCellActions(game) + _arcSyncActions(game);
                return extra ? '<div class="chat-arc-actions">' + extra + '</div>' : '';
            })() +
        '</div>';
    }

    function _arcCellClick(idx) {
        var arc = state.arcade;
        var game = arc && arc.game ? _arcGame(arc.game) : null;
        if (!game || game.status !== 'live') return;
        if (game.variant !== 'othello' && game.variant !== 'gomoku') return;
        if (!_arcActive(game) || !state.canSend) return;
        var cap = game.variant === 'othello' ? 64 : 225;
        var n = parseInt(idx, 10);
        if (!(n >= 0 && n < cap)) return;
        if (_arcCanVote(game)) arcVote(game.id, String(n));
        else arcMove(game.id, String(n));
    }

    function _arcColumnClick(col) {
        var arc = state.arcade;
        var game = arc && arc.game ? _arcGame(arc.game) : null;
        if (!game || game.status !== 'live' || game.variant !== 'connect4') return;
        if (!_arcActive(game) || !state.canSend) return;
        if (!/^[0-6]$/.test(String(col))) return;
        if (_arcCanVote(game)) arcVote(game.id, String(col));
        else arcMove(game.id, String(col));
    }

    // ── Board interaction ───────────────────────────────────────────────
    // Click-to-select then click-to-move, with drag as an alternative. Click
    // first because it is the only one that works on a phone.

    function _arcTryMove(game, from, to) {
        var E = window.ChessEngine;
        var pos = E.fromFEN(game.fen);
        if (!pos) return false;
        // A pawn reaching the last rank needs a piece chosen before the move
        // can be sent — under-promotion is occasionally the only winning move,
        // so we ask rather than assuming a queen.
        var needsPromo = E.legalMoves(pos).some(function (m) {
            return m.from === from && m.to === to && m.promo;
        });
        if (needsPromo) {
            state.arcade.promo = { from: from, to: to };
            renderArcade();
            return true;
        }
        var uci = E.toAlg(from) + E.toAlg(to);
        if (!E.uciToMove(pos, uci)) return false;
        if (_arcCanVote(game)) arcVote(game.id, uci);
        else arcMove(game.id, uci);
        return true;
    }

    // "Have they seen it?" — free, from carriers we already send. Any move,
    // sync or state a player emits proves what they knew at the time, so no
    // acknowledgement round trip is needed to show this.
    function _arcAckHtml(game) {
        if (!game || game.status !== 'live' || game.desync) return '';
        var mySeat = _arcSeat(game);
        if (!mySeat || game.roomSeat) return '';
        var opp = mySeat === 'w' ? game.black : game.white;
        if (!opp) return '';
        if (_arcMyMove(game)) return '';            // it is our turn; nothing to confirm
        var mine = game.ply - 1;                    // the ply our last move occupied
        if (mine < 0) return '';
        var seen = (game.ack[opp] || -1) >= mine;
        if (seen) {
            return '<div class="chat-arc-ack chat-arc-ack--seen">✓ ' + esc(opp) +
                ' has seen your move</div>';
        }
        var mins = Math.floor((Date.now() - game.lastAt) / 60000);
        return '<div class="chat-arc-ack">◌ not acknowledged yet' +
            (mins >= 5 ? ' · ' + (mins >= 120 ? Math.floor(mins / 60) + 'h' : mins + 'm') +
                         ' — asking the room where the game is' : '') +
        '</div>';
    }

    // Actions that exist on every board regardless of variant.
    function _arcSyncActions(game) {
        if (!state.canSend || !_arcSeat(game)) return '';
        if (game.desync) {
            return '<button class="chat-arc-btn chat-arc-btn--go" type="button" ' +
                'data-chat-arc-accept="' + attr(game.id) + '" ' +
                'title="Take your opponent\'s position and carry on — the only ' +
                'thing that unfreezes a disagreement">Accept their position</button>';
        }
        if (game.status !== 'live') return '';
        return '<button class="chat-arc-btn chat-arc-btn--slim" type="button" ' +
            'data-chat-arc-sync="' + attr(game.id) + '" ' +
            'title="Ask the room where this game actually is">⟳ Sync</button>';
    }

    // The room's ballot for the current ply: what has been picked so far and
    // how close each option is to carrying.
    function _arcBallotHtml(game) {
        var CG = window.ChatGames;
        if (!CG.isRoomTurn(game)) return '';
        var E = window.ChessEngine;
        var pos = game.variant === 'chess' ? E.fromFEN(game.fen) : null;
        var names = Object.keys(game.votes || {});
        if (!names.length) {
            return '<div class="chat-arc-note">No votes yet. ' + game.voteK +
                ' people picking the same move commits it.</div>';
        }
        names.sort(function (a, b) {
            if (game.votes[b] !== game.votes[a]) return game.votes[b] - game.votes[a];
            return a < b ? -1 : 1;
        });
        return '<div class="chat-arc-ballot">' +
            '<div class="chat-arc-ballot-head">Room ballot · ' + game.voteK +
                ' to carry</div>' +
            names.map(function (uci) {
                var mv = pos && E.uciToMove(pos, uci);
                var label = mv ? E.toSAN(pos, mv)
                    : (game.variant === 'connect4' ? 'column ' + (parseInt(uci, 10) + 1) : uci);
                var n = game.votes[uci];
                return '<div class="chat-arc-ballotrow">' +
                    '<span class="chat-arc-ballotmv">' + esc(label) + '</span>' +
                    '<span class="chat-arc-ballotbar"><i style="width:' +
                        Math.min(100, Math.round(n / game.voteK * 100)) + '%"></i></span>' +
                    '<span class="chat-arc-ballotn">' + n + '/' + game.voteK + '</span>' +
                '</div>';
            }).join('') +
        '</div>';
    }

    function _arcSquareClick(sq) {
        var arc = state.arcade;
        var game = arc && arc.game ? _arcGame(arc.game) : null;
        if (!game || game.status !== 'live') return;
        var E = window.ChessEngine;
        var pos = E.fromFEN(game.fen);
        if (!pos) return;
        var voting = _arcCanVote(game);
        var actSide = voting ? game.roomSeat : _arcSeat(game);
        if (!_arcActive(game) || !state.canSend) return;   // spectators just look

        var piece = pos.board[sq];
        if (arc.sel >= 0 && _arcTryMove(game, arc.sel, sq)) return;
        // Selecting one of your own pieces (or re-selecting) never fails.
        if (piece && E.colorOf(piece) === actSide) {
            arc.sel = (arc.sel === sq) ? -1 : sq;
        } else {
            arc.sel = -1;
        }
        renderArcade();
    }

    // Drag is an ALTERNATIVE to click, never the only way in: click-to-move
    // is the one that works on a phone, so drag is bound as an enhancement on
    // top of it and both funnel into the same _arcTryMove.
    function _arcBindDrag() {
        var page = document.getElementById('chat-page');
        if (!page || page._arcDragBound) return;
        page._arcDragBound = true;
        var from = -1;
        page.addEventListener('dragstart', function (e) {
            var sq = e.target.closest && e.target.closest('[data-chat-arc-sq]');
            if (!sq || !_arcOn()) { return; }
            from = parseInt(sq.getAttribute('data-chat-arc-sq'), 10);
            if (state.arcade) { state.arcade.sel = from; renderArcade(); }
            try { e.dataTransfer.setData('text/plain', String(from)); } catch (err) { /* ok */ }
        });
        page.addEventListener('dragover', function (e) {
            if (!_arcOn()) return;
            if (e.target.closest && e.target.closest('[data-chat-arc-sq]')) e.preventDefault();
        });
        page.addEventListener('drop', function (e) {
            if (!_arcOn()) return;
            var sq = e.target.closest && e.target.closest('[data-chat-arc-sq]');
            if (!sq) return;
            e.preventDefault();
            var to = parseInt(sq.getAttribute('data-chat-arc-sq'), 10);
            var game = state.arcade.game ? _arcGame(state.arcade.game) : null;
            if (game && game.status === 'live' && from >= 0 && to >= 0 &&
                state.canSend && _arcActive(game)) {
                _arcTryMove(game, from, to);
            }
            from = -1;
        });
    }

    function renderUserPanel() {
        var host = q('[data-chat-userpanel]');
        if (!host) return;
        var name = state.selfName || 'You';
        var initials = String(name).replace(/[^A-Za-z0-9]/g, '').slice(0, 2).toUpperCase() || '?';
        // Your own strip was the one place the chosen avatar never reached —
        // message rows, the member list and the picker all had it.
        var av = _myAvatar();
        host.innerHTML =
            '<div class="chat-userpanel-av' + (av ? ' chat-userpanel-av--img' : '') + '">' +
                (av ? '<img src="/static/avatar/' + av + '.png" alt="" loading="lazy">'
                    : esc(initials)) +
            '</div>' +
            '<div class="chat-userpanel-main">' +
                '<div class="chat-userpanel-name">' + esc(name) + '</div>' +
                '<div class="chat-userpanel-sub">' +
                    (state.canSend ? 'Online' : 'Read-only') + '</div>' +
            '</div>' +
            (state.isAdmin
                ? '<button class="chat-userpanel-btn" type="button" data-chat-settings-btn ' +
                    'title="Chat settings">⚙</button>'
                : '') +
            '<button class="chat-userpanel-btn" type="button" data-chat-open-social="friends" ' +
                'title="Social & Lists (Friends, Blocklist, Bookmarks)">👥</button>';
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
        // The Arcade owns the whole head: the room controls below (history
        // search, pins, jukebox, the SoulSync-only filter) all act on the
        // message list, and there is no message list here.
        if (_arcOn()) {
            head.innerHTML = ((state.arcade.game || state.arcade.slots)
                ? '<button class="chat-thread-back" type="button" data-chat-arc-home ' +
                      'title="Back to the Arcade">&larr;</button>' +
                  '<span class="chat-head-title">' +
                      (state.arcade.slots ? '🎰 slots' : '🎲 game') + '</span>'
                : '<span class="chat-head-title">🎲 arcade</span>') +
                '<span class="chat-head-sub">no server — every board here is folded ' +
                    'out of this room&rsquo;s messages</span>' +
                (state.isAdmin ? '<button class="chat-cog-btn" type="button" ' +
                    'data-chat-settings-btn title="Chat settings">⚙</button>' : '');
            return;
        }
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
              '<button class="chat-filter-btn" type="button" data-chat-watch-btn ' +
              'title="Movie night — nominate something, the room votes, owners watch together">🎬 Movie night</button>' +
              '<button class="chat-filter-btn' + (state.ssOnly ? ' chat-filter-btn--on' : '') +
              '" type="button" data-chat-filter title="' +
              (state.ssOnly ? 'SoulSync only: hiding other Soulseek clients, and sending in SoulSync format'
                            : 'All messages: showing every Soulseek client, and sending in plain text they can read') + '">' +
              (state.ssOnly ? 'SoulSync only' : 'All messages') + '</button>' +
              (state.isAdmin ? '<button class="chat-cog-btn" type="button" data-chat-settings-btn ' +
                  'title="Chat settings">⚙</button>' : '')
            : '<span class="chat-head-title">' + esc(state.pmUser || '') +
              (isDev(state.pmUser) ? ' <span class="chat-dev-badge" title="SoulSync Creator & Lead Developer"><span class="chat-dev-badge-check">✔</span> DEV</span>' : '') +
              (isFriend(state.pmUser) ? ' <span class="chat-friend-badge" title="Friend">⭐ Friend</span>' : '') +
              '</span>' +
              '<span class="chat-head-sub">private message</span>' +
              '<div class="chat-head-actions" style="margin-left:auto;display:flex;align-items:center;gap:8px;">' +
              '<button type="button" class="chat-filter-btn" data-chat-browse-user="' + attr(state.pmUser || '') + '" title="Browse their files">📁 Browse Files</button>' +
              '<button type="button" class="chat-head-close-pm" data-chat-head-close-pm title="Close this conversation">✕ Close Chat</button>' +
              '</div>';
    }

    function renderComposer() {
        var form = q('[data-chat-composer]');
        var input = q('[data-chat-input]');
        if (!form || !input) return;
        // The Arcade is a view, not a channel — there is no message to send
        // into it, and a composer would imply one.
        if (_arcOn()) { form.hidden = true; return; }
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
        var isRoomCanSend = (state.view === 'room' && state.canSend);
        var bar = q('[data-chat-toolbar]');
        if (bar) bar.hidden = !isRoomCanSend;
        // GIF = sending a CDN URL through the room pipeline — room-only. The
        // emoji button stays everywhere (plain unicode is fine in PMs).
        var gifBtn = q('[data-chat-gif-btn]');
        if (gifBtn) gifBtn.hidden = !isRoomCanSend;
        // polls are a room thing (bus events mean nothing in a PM)
        var pollBtn = q('[data-chat-poll-btn]');
        if (pollBtn) pollBtn.hidden = !isRoomCanSend;
        var npBtn = q('[data-chat-np-btn]');
        if (npBtn) npBtn.hidden = !isRoomCanSend;
        var wantBtn = q('[data-chat-want-btn]');
        if (wantBtn) wantBtn.hidden = !isRoomCanSend;
        if (state.view !== 'room') { toggleEmojiPicker(true); toggleGifPicker(true); togglePollPop(true); }
        // last, because plain mode overrides the placeholder and hides the
        // rich controls this function just showed
        _syncModeBtn();
        if (state.connectionError) input.placeholder = state.connectionError;
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

    // ── edit composing ───────────────────────────────────────────────────────
    // Reuses the reply bar as the "editing…" banner (they're mutually
    // exclusive composer modes) and preloads the input with the current text.
    function startEdit(key, currentText) {
        if (state.view !== 'room' || !state.canSend || !key) return;
        cancelReply();
        state.editing = { key: key };
        var bar = q('[data-chat-reply-bar]');
        var who = q('[data-chat-reply-who]');
        var ex = q('[data-chat-reply-excerpt]');
        if (who) who.textContent = '✏ editing your message';
        if (ex) ex.textContent = String(currentText || '').slice(0, 100);
        if (bar) bar.hidden = false;
        var input = q('[data-chat-input]');
        if (input) {
            input.value = String(currentText || '');
            input.focus();
            input.setSelectionRange(input.value.length, input.value.length);
        }
    }

    function cancelEdit() {
        if (!state.editing) return;
        state.editing = null;
        var bar = q('[data-chat-reply-bar]');
        if (bar) bar.hidden = true;
        var input = q('[data-chat-input]');
        if (input) input.value = '';
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
            var isFr = isFriend(name);
            body.innerHTML = '<div class="chat-card-head">' + _avatar(name) +
                '<span class="chat-card-name">' + esc(name) + '</span>' +
                (isDev(name) ? '<span class="chat-dev-badge chat-dev-badge--lg" style="margin-left:8px;" title="SoulSync Creator & Lead Developer"><span class="chat-dev-badge-check">✔</span> DEV</span>' : '') +
                (isFr ? '<span class="chat-friend-badge" style="margin-left:8px;">⭐ Friend</span>' : '') +
                '</div>' +
                (isDev(name) ? '<div class="chat-card-sub" style="color:#a5b4fc;font-weight:700;font-size:12px;margin:2px 0 6px;">🛠️ SoulSync Creator &amp; Lead Developer</div>' : '') +
                '<div class="chat-card-info">Loading…</div>';
        }
        overlay.hidden = false;
        overlay.setAttribute('data-chat-user-card-for', name);
        var frBtn = overlay.querySelector('[data-chat-card-friend]');
        if (frBtn) {
            frBtn.hidden = state.selfName && name === state.selfName;
            frBtn.textContent = isFriend(name) ? '⭐ Remove Friend' : '⭐ Add Friend';
        }
        var blkBtn = overlay.querySelector('[data-chat-card-block]');
        if (blkBtn) {
            blkBtn.hidden = state.selfName && name === state.selfName;
            blkBtn.textContent = isBlocked(name) ? 'Unblock' : '🚫 Block';
        }
        var bmBtn = overlay.querySelector('[data-chat-card-bookmark]');
        if (bmBtn) {
            bmBtn.textContent = isPeerBookmarked(name) ? '★ Bookmarked' : '⭐ Bookmark';
        }
        var ignBtn = overlay.querySelector('[data-chat-card-ignore]');
        if (ignBtn) {
            ignBtn.hidden = state.selfName && name === state.selfName;
            ignBtn.textContent = isIgnored(name) ? 'Unmute' : 'Mute';
            ignBtn.title = isIgnored(name)
                ? 'Show this user’s messages again'
                : 'Hide this user’s messages (this browser only)';
        }
        // Challenge = a PRIVATE Arcade game with them in the invited seat.
        // The whole invite lifecycle (gm.new {o}, join gate, 'private' badge)
        // has been in the fold since P2 — this button is its first way in.
        var chBtn = overlay.querySelector('[data-chat-card-challenge]');
        if (chBtn) {
            chBtn.hidden = !state.canSend || !_arcReady() ||
                (state.selfName && name === state.selfName);
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

    // The Challenge button toggles a small variant row inside the card —
    // three choices, no second modal. Picking one sends gm.new {o: them}
    // and the normal _arcAfterSend flow lands you on the fresh board.
    function _arcChallengeRow(overlay) {
        var body = overlay.querySelector('[data-chat-user-card-body]');
        if (!body) return;
        var existing = body.querySelector('.chat-card-challenge');
        if (existing) { existing.remove(); return; }
        var div = document.createElement('div');
        div.className = 'chat-card-challenge';
        div.innerHTML = '<span class="chat-card-challenge-label">Pick the game — ' +
            'only they can take the seat</span>' +
            '<div class="chat-card-challenge-btns">' +
            [['chess', '♟ Chess'], ['connect4', '🔴 Connect 4'], ['battleship', '🚢 Battleship'],
             ['othello', '⚫ Othello'], ['gomoku', '⚪ Gomoku']]
                .map(function (v) {
                    return '<button class="chat-arc-btn" type="button" ' +
                        'data-chat-card-challenge-v="' + v[0] + '">' + v[1] + '</button>';
                }).join('') +
            '</div>';
        body.appendChild(div);
    }

    // Ping the invited user when a challenge NAMING THEM arrives. Once per
    // game per session, and only while the table is still open — replayed
    // archive carriers on a page load would otherwise re-announce every
    // stale table ever aimed at us.
    var _arcChallengeToasted = {};
    function _arcNoticeChallenges(fresh) {
        if (!state.selfName || !_arcReady()) return;
        (fresh || []).forEach(function (e) {
            if (!e || !e.p || e.p.k !== 'gm.new') return;
            if (e.p.o !== state.selfName || e.username === state.selfName) return;
            var gid = String(e.p.g || '');
            if (!gid || _arcChallengeToasted[gid]) return;
            _arcChallengeToasted[gid] = 1;
            var g = _gamesState().games[gid];
            if (!g || g.status !== 'open' || g.expired) return;
            if (typeof showToast === 'function') {
                showToast('⚔ ' + e.username + ' challenged you to ' +
                    (g.variant === 'connect4' ? 'Connect 4'
                        : g.variant === 'battleship' ? 'Battleship' : 'chess') +
                    ' — it’s waiting in the Arcade', 'info', 6000);
            }
        });
    }

    // ── slide-over peer hub & file explorer (better than nicotine+/slskd) ───────
    var _browse = { user: null, dirs: [], dir: null, files: [], crumbs: [{ name: 'Shares', path: null }] };

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

    function _fileQualityBadge(fn) {
        if (!fn) return '';
        var lower = fn.toLowerCase();
        if (lower.endsWith('.flac')) {
            return '<span class="chat-audio-badge chat-audio-badge--flac">FLAC</span>';
        }
        if (lower.endsWith('.wav')) {
            return '<span class="chat-audio-badge chat-audio-badge--wav">WAV</span>';
        }
        if (lower.endsWith('.mp3')) {
            var match = lower.match(/(320|256|192|v0|v2)/i);
            var label = match ? match[1].toUpperCase() : 'MP3';
            return '<span class="chat-audio-badge chat-audio-badge--mp3">' + esc(label) + '</span>';
        }
        if (lower.endsWith('.m4a') || lower.endsWith('.aac')) {
            return '<span class="chat-audio-badge chat-audio-badge--mp3">AAC</span>';
        }
        return '';
    }

    function _folderFormatBadge(dirName) {
        if (!dirName) return '';
        var s = String(dirName).toLowerCase();
        // Hi-Res / 24-bit FLAC
        if (/(24[\s\-]?bit|24\/96|24\/192|96khz|192khz|hi[\s\-]?res)/i.test(s)) {
            return '<span class="chat-folder-badge chat-folder-badge--flac">FLAC 24-bit</span>';
        }
        // Standard FLAC / Lossless / ALAC
        if (/(flac|lossless|alac)/i.test(s)) {
            return '<span class="chat-folder-badge chat-folder-badge--flac">FLAC</span>';
        }
        // Vinyl rips
        if (/(vinyl|lp[\s\-_]rip|vinylrip)/i.test(s)) {
            return '<span class="chat-folder-badge chat-folder-badge--vinyl">VINYL</span>';
        }
        // WAV
        if (/(\bwav\b|\.wav)/i.test(s)) {
            return '<span class="chat-folder-badge chat-folder-badge--wav">WAV</span>';
        }
        // MP3 320k
        if (/(320|320k|320kbps|cbr)/i.test(s)) {
            return '<span class="chat-folder-badge chat-folder-badge--320">320K</span>';
        }
        // V0 / V2 / VBR
        if (/(v0|v2|vbr)/i.test(s)) {
            return '<span class="chat-folder-badge chat-folder-badge--vbr">V0 VBR</span>';
        }
        return '';
    }

    function closeBrowse() {
        var overlay = q('[data-chat-browse-modal]');
        var backdrop = q('[data-chat-browse-backdrop]');
        if (overlay) {
            overlay.classList.remove('visible');
            setTimeout(function () { overlay.hidden = true; }, 320);
        }
        if (backdrop) {
            backdrop.classList.remove('visible');
            setTimeout(function () { backdrop.hidden = true; }, 320);
        }
        if (window.location.hash && window.location.hash.indexOf('#browse') === 0) {
            try { history.replaceState(null, '', window.location.pathname + window.location.search); } catch (e) {}
        }
    }

    function openBrowse(name) {
        if (!name) return;
        closeUserCard();
        var overlay = q('[data-chat-browse-modal]');
        var backdrop = q('[data-chat-browse-backdrop]');
        if (!overlay) return;
        _browse = {
            user: name,
            dirs: [],
            cache: {},
            expanded: {},
            selected: {},
            filter: '',
            formatFilter: 'all'
        };
        overlay.hidden = false;
        if (backdrop) backdrop.hidden = false;
        requestAnimationFrame(function () {
            overlay.classList.add('visible');
            if (backdrop) backdrop.classList.add('visible');
        });

        // Set Hero identity
        var title = q('[data-chat-browse-title]');
        if (title) {
            title.innerHTML = esc(name) + (isDev(name) ? ' <span class="chat-dev-badge chat-dev-badge--inline" title="SoulSync Creator & Lead Developer"><span class="chat-dev-badge-check">✔</span> DEV</span>' : '');
        }
        var av = q('[data-chat-browse-av]');
        if (av) av.textContent = (name[0] || '👤').toUpperCase();
        var statusText = q('[data-chat-browse-status-text]');
        if (statusText) statusText.textContent = isDev(name) ? 'SoulSync Creator & Lead Developer' : 'Connecting…';

        // Setup format filter pills
        var pBar = q('.chat-browse-format-filters');
        if (pBar) {
            pBar.querySelectorAll('.chat-fmt-pill').forEach(function (pill) {
                pill.classList.toggle('chat-fmt-pill--active', pill.getAttribute('data-chat-browse-fmt') === 'all');
            });
        }

        // Setup bookmark button & saved peers count
        var bmBtn = q('[data-chat-peer-bookmark]');
        if (bmBtn) {
            var isBm = isPeerBookmarked(name);
            bmBtn.textContent = isBm ? '★ Bookmarked' : '⭐ Bookmark';
            bmBtn.classList.toggle('chat-peer-bm-btn--active', isBm);
        }
        _updateSavedPeersBtn();

        // Setup friend and block quickbar buttons
        var frBtn = q('[data-chat-browse-friend]');
        if (frBtn) {
            frBtn.textContent = isFriend(name) ? '⭐ Friend (Remove)' : '⭐ Friend';
        }
        var blkBtn = q('[data-chat-browse-block]');
        if (blkBtn) {
            blkBtn.textContent = isBlocked(name) ? 'Unblock' : '🚫 Block';
        }

        // Reset telemetry
        var spd = q('[data-chat-browse-speed]');
        if (spd) { spd.textContent = '—'; spd.className = 'chat-peer-stat-val'; }
        var qEl = q('[data-chat-browse-queue]');
        if (qEl) qEl.textContent = '—';
        var slots = q('[data-chat-browse-slots]');
        if (slots) { slots.textContent = '—'; slots.className = 'chat-peer-stat-val'; }
        var fCount = q('[data-chat-browse-folders-count]');
        if (fCount) fCount.textContent = '—';
        var histPill = q('[data-chat-browse-history]');
        if (histPill) histPill.hidden = true;

        var inp = q('[data-chat-browse-search]');
        if (inp) { inp.value = ''; inp.placeholder = 'Filter folders or search files…'; }
        _syncBrowseDock();

        var body = q('[data-chat-browse-body]');
        if (body) body.innerHTML = '<div class="chat-gif-hint">Connecting to ' + esc(name) + '’s Soulseek node…</div>';

        // Update hash for browser Back navigation
        try { history.replaceState(null, '', '#browse/' + encodeURIComponent(name)); } catch (e) {}

        // Fetch User Info / Telemetry in parallel
        getJSON('/api/chat/user/' + encodeURIComponent(name)).then(function (res) {
            if (_browse.user !== name) return;
            var info = (res.ok && res.body.info) || {};
            var status = (res.ok && res.body.status) || {};
            var hist = (res.ok && res.body.history) || null;

            if (statusText) {
                var pres = status.presence || status.status ||
                    (status.isOnline === true ? 'Online' : (status.isOnline === false ? 'Offline' : 'Soulseek Peer'));
                statusText.textContent = String(pres);
            }
            if (spd) {
                spd.textContent = info.uploadSpeed ? (_fmtBytes(info.uploadSpeed) + '/s') : (status.uploadSpeed ? _fmtBytes(status.uploadSpeed) + '/s' : 'Available');
                spd.className = 'chat-peer-stat-val chat-peer-stat-val--good';
            }
            if (qEl) {
                qEl.textContent = info.queueLength != null ? String(info.queueLength) : '0';
            }
            if (slots) {
                slots.textContent = info.hasFreeUploadSlot ? 'Yes' : (info.uploadSlots ? String(info.uploadSlots) : 'Free');
                if (info.hasFreeUploadSlot) slots.className = 'chat-peer-stat-val chat-peer-stat-val--good';
            }
            if (hist && hist.downloads > 0 && histPill) {
                var ht = q('[data-chat-browse-history-text]');
                if (ht) {
                    ht.textContent = hist.downloads + ' downloads · ' +
                        (hist.success_rate != null ? hist.success_rate + '% success · ' : '') +
                        (hist.total_bytes > 0 ? _fmtBytes(hist.total_bytes) : '');
                }
                histPill.hidden = false;
            }
        });

        // Fetch Shares
        getJSON('/api/chat/user/' + encodeURIComponent(name) + '/shares').then(function (res) {
            if (_browse.user !== name) return;
            if (!res.ok) {
                if (body) {
                    body.innerHTML = '<div class="chat-gif-hint">' +
                        esc(res.body && res.body.error || 'Could not browse') + '</div>' +
                        '<div class="chat-browse-retry-row" style="text-align:center;margin-top:16px;">' +
                        '<button type="button" class="modal-button modal-button--primary" ' +
                            'data-chat-browse-retry>Try again</button></div>';
                }
                return;
            }
            _browse.dirs = res.body.directories || [];
            if (fCount) fCount.textContent = _browse.dirs.length + ' folders';
            renderBrowseTree('');
        });
    }

    function renderBrowseTree(filter) {
        var body = q('[data-chat-browse-body]');
        if (!body) return;
        _browse.filter = filter != null ? filter : (_browse.filter || '');
        var f = String(_browse.filter).toLowerCase();
        var fmt = _browse.formatFilter || 'all';

        var dirs = _browse.dirs.filter(function (d) {
            if (fmt === 'flac') {
                var s = d.name.toLowerCase();
                var isFlac = /(flac|24bit|24-bit|lossless|alac|vinyl)/i.test(s);
                var cached = _browse.cache[d.name];
                if (!isFlac && cached) isFlac = cached.some(function (x) { return /\.flac$/i.test(x.filename); });
                if (!isFlac) return false;
            } else if (fmt === '320') {
                var s = d.name.toLowerCase();
                var is320 = /(320|320k|320kbps|cbr)/i.test(s);
                var cached = _browse.cache[d.name];
                if (!is320 && cached) is320 = cached.some(function (x) { return /320/i.test(x.filename); });
                if (!is320) return false;
            } else if (fmt === 'vbr') {
                var s = d.name.toLowerCase();
                var isVbr = /(v0|v2|vbr|mp3)/i.test(s) && !/(320|flac|lossless)/i.test(s);
                var cached = _browse.cache[d.name];
                if (!isVbr && cached) isVbr = cached.some(function (x) { return /\.(mp3|aac|m4a)$/i.test(x.filename) && !/320/i.test(x.filename); });
                if (!isVbr) return false;
            }
            if (!f) return true;
            if (d.name.toLowerCase().indexOf(f) > -1) return true;
            var cached2 = _browse.cache[d.name];
            if (cached2 && cached2.some(function (x) { return x.filename.toLowerCase().indexOf(f) > -1; })) return true;
            return false;
        }).slice(0, 500);

        if (!dirs.length) {
            body.innerHTML = '<div class="chat-gif-hint">' +
                (_browse.dirs.length ? 'No folders match that filter' : 'Nothing shared') + '</div>';
            _syncBrowseDock();
            return;
        }

        body.innerHTML = dirs.map(function (d) {
            var isExp = !!_browse.expanded[d.name];
            var fmtBadge = _folderFormatBadge(d.name);
            return '<div class="chat-folder-tree-item' + (isExp ? ' chat-folder-tree-item--open' : '') + '" data-chat-folder-item="' + attr(d.name) + '">' +
                '<div class="chat-folder-header" data-chat-browse-toggle="' + attr(d.name) + '">' +
                    '<button type="button" class="chat-folder-chevron' + (isExp ? ' chat-folder-chevron--expanded' : '') + '" ' +
                            'data-chat-browse-toggle="' + attr(d.name) + '" title="' + (isExp ? 'Collapse' : 'Expand') + '">' +
                        '<svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>' +
                    '</button>' +
                    '<span class="chat-folder-icon">' + (isExp ? '📂' : '📁') + '</span>' +
                    '<div class="chat-folder-info" title="' + attr(d.name) + '">' +
                        '<div class="chat-folder-title-row">' +
                            '<span class="chat-folder-name">' + esc(_baseName(d.name)) + '</span>' +
                            fmtBadge +
                        '</div>' +
                        '<span class="chat-folder-meta">' + d.file_count + ' file' + (d.file_count === 1 ? '' : 's') + '</span>' +
                    '</div>' +
                    '<div class="chat-folder-actions">' +
                        '<button type="button" class="chat-folder-dl-btn" data-chat-browse-dl-folder="' + attr(d.name) + '" title="Download entire folder in 1 click">' +
                            '⬇ Download Folder</button>' +
                    '</div>' +
                '</div>' +
                '<div class="chat-folder-children" data-chat-browse-children="' + attr(d.name) + '"' + (isExp ? '' : ' hidden') + '>' +
                    (isExp ? _renderFolderChildrenHtml(d.name) : '') +
                '</div>' +
            '</div>';
        }).join('');

        _syncBrowseDock();
    }

    function _renderFolderChildrenHtml(dirName) {
        var files = _browse.cache[dirName];
        if (!files) {
            return '<div class="chat-folder-loading"><span class="chat-spinner-micro"></span> Loading files in ' + esc(_baseName(dirName)) + '…</div>';
        }
        var f = String(_browse.filter || '').toLowerCase();
        var visibleFiles = files.filter(function (x) {
            return !f || x.filename.toLowerCase().indexOf(f) > -1 || dirName.toLowerCase().indexOf(f) > -1;
        });
        if (!visibleFiles.length) {
            return '<div class="chat-folder-empty">No matching files</div>';
        }

        var allChecked = visibleFiles.every(function (x) { return !!_browse.selected[x.filename]; });

        var html = '<div class="chat-folder-subhead">' +
            '<label class="chat-file-row chat-file-row--folder-all">' +
                '<input type="checkbox" data-chat-browse-folder-all="' + attr(dirName) + '"' + (allChecked ? ' checked' : '') + '>' +
                '<span class="chat-file-name" style="font-weight:700;">Select all in this folder (' + visibleFiles.length + ' tracks)</span>' +
            '</label>' +
        '</div>';

        html += visibleFiles.map(function (x) {
            var isSel = !!_browse.selected[x.filename];
            var qBadge = _fileQualityBadge(x.filename);
            return '<label class="chat-file-row' + (isSel ? ' chat-file-row--selected' : '') + '">' +
                '<input type="checkbox" data-chat-browse-file="' + attr(x.filename) + '" data-chat-browse-size="' + (x.size || 0) + '" data-chat-browse-dir="' + attr(dirName) + '"' + (isSel ? ' checked' : '') + '>' +
                '<span class="chat-file-icon">🎵</span>' +
                '<span class="chat-file-name" title="' + attr(x.filename) + '">' + esc(_baseName(x.filename)) + '</span>' +
                qBadge +
                '<span class="chat-file-size">' + _fmtSize(x.size) + '</span>' +
                '<button type="button" class="chat-file-quick-dl" data-chat-browse-dl-single="' + attr(x.filename) + '" data-chat-browse-size="' + (x.size || 0) + '" title="Download this track">⬇</button>' +
            '</label>';
        }).join('');

        return html;
    }

    function _findBrowseFolderItem(dirName) {
        if (!dirName) return null;
        var body = q('[data-chat-browse-body]');
        if (!body) return null;
        var items = body.querySelectorAll('.chat-folder-tree-item');
        for (var i = 0; i < items.length; i++) {
            if (items[i].getAttribute('data-chat-folder-item') === dirName) {
                return items[i];
            }
        }
        return null;
    }

    function toggleBrowseFolder(dirName, itemEl) {
        if (!dirName) return;
        var willExpand = !_browse.expanded[dirName];
        _browse.expanded[dirName] = willExpand;

        var item = itemEl || _findBrowseFolderItem(dirName);
        var childrenEl = item ? item.querySelector('.chat-folder-children') : null;
        var chevron = item ? item.querySelector('.chat-folder-chevron') : null;
        var icon = item ? item.querySelector('.chat-folder-icon') : null;

        if (item) {
            if (willExpand) item.classList.add('chat-folder-tree-item--open');
            else item.classList.remove('chat-folder-tree-item--open');
        }
        if (chevron) {
            if (willExpand) chevron.classList.add('chat-folder-chevron--expanded');
            else chevron.classList.remove('chat-folder-chevron--expanded');
            chevron.setAttribute('title', willExpand ? 'Collapse' : 'Expand');
        }
        if (icon) {
            icon.textContent = willExpand ? '📂' : '📁';
        }

        if (childrenEl) {
            childrenEl.hidden = !willExpand;
            if (willExpand) {
                if (_browse.cache[dirName]) {
                    childrenEl.innerHTML = _renderFolderChildrenHtml(dirName);
                    _syncBrowseDock();
                } else {
                    childrenEl.innerHTML = '<div class="chat-folder-loading"><span class="chat-spinner-micro"></span> Loading files in ' + esc(_baseName(dirName)) + '…</div>';
                    var name = _browse.user;
                    getJSON('/api/chat/user/' + encodeURIComponent(name) + '/shares/files?dir=' + encodeURIComponent(dirName)).then(function (res) {
                        if (_browse.user !== name) return;
                        var curItem = item || _findBrowseFolderItem(dirName);
                        var curChildren = curItem ? curItem.querySelector('.chat-folder-children') : null;
                        if (!curChildren) return;
                        if (!res.ok) {
                            curChildren.innerHTML = '<div class="chat-folder-empty">' + esc(res.body && res.body.error || 'Could not load folder') + '</div>';
                            return;
                        }
                        _browse.cache[dirName] = res.body.files || [];
                        curChildren.innerHTML = _renderFolderChildrenHtml(dirName);
                        _syncBrowseDock();
                    });
                }
            }
        }
    }

    function expandAllBrowseFolders() {
        var dirs = _browse.dirs || [];
        dirs.forEach(function (d) {
            _browse.expanded[d.name] = true;
        });
        renderBrowseTree(_browse.filter || '');
        dirs.forEach(function (d) {
            if (!_browse.cache[d.name]) {
                var name = _browse.user;
                getJSON('/api/chat/user/' + encodeURIComponent(name) + '/shares/files?dir=' + encodeURIComponent(d.name)).then(function (res) {
                    if (_browse.user !== name || !_browse.expanded[d.name]) return;
                    if (res.ok) {
                        _browse.cache[d.name] = res.body.files || [];
                        var item = _findBrowseFolderItem(d.name);
                        var ch = item ? item.querySelector('.chat-folder-children') : null;
                        if (ch) ch.innerHTML = _renderFolderChildrenHtml(d.name);
                        _syncBrowseDock();
                    }
                });
            }
        });
    }

    function collapseAllBrowseFolders() {
        _browse.expanded = {};
        renderBrowseTree(_browse.filter || '');
    }

    function _syncBrowseDock() {
        var dock = q('[data-chat-browse-dock]');
        var countEl = q('[data-chat-dock-count]');
        var sizeEl = q('[data-chat-dock-size]');
        if (!dock) return;
        var selected = _browse.selected || {};
        var keys = Object.keys(selected);
        var count = keys.length;
        var totalBytes = 0;
        keys.forEach(function (k) {
            totalBytes += Number(selected[k].size) || 0;
        });

        if (count > 0) {
            dock.hidden = false;
            if (countEl) countEl.textContent = count;
            if (sizeEl) sizeEl.textContent = _fmtSize(totalBytes);
        } else {
            dock.hidden = true;
        }
    }

    function downloadFolder(dirName) {
        if (!_browse.user || !dirName) return;
        if (typeof showToast === 'function') showToast('Fetching files in ' + _baseName(dirName) + '…', 'info');
        var name = _browse.user;
        getJSON('/api/chat/user/' + encodeURIComponent(name) + '/shares/files?dir=' +
                encodeURIComponent(dirName)).then(function (res) {
            if (!res.ok || !res.body.files || !res.body.files.length) {
                if (typeof showToast === 'function') showToast('Could not read files for that folder', 'error');
                return;
            }
            _browse.cache[dirName] = res.body.files;
            var files = res.body.files.map(function (x) {
                return { filename: x.filename, size: x.size };
            });
            postJSON('/api/chat/user/' + encodeURIComponent(name) + '/download', { files: files }).then(function (dRes) {
                if (!dRes.ok) {
                    if (typeof showToast === 'function') showToast(dRes.body && dRes.body.error || 'Could not queue folder', 'error');
                    return;
                }
                var n = dRes.body.queued || 0;
                if (typeof showToast === 'function') {
                    showToast('Queued ' + n + ' files from ' + _baseName(dirName) + ' — check Downloads', 'success');
                }
            });
        });
    }

    function downloadSingleTrack(filePath, size) {
        if (!_browse.user || !filePath) return;
        postJSON('/api/chat/user/' + encodeURIComponent(_browse.user) + '/download',
                 { files: [{ filename: filePath, size: Number(size) || 0 }] }).then(function (res) {
            if (!res.ok) {
                if (typeof showToast === 'function') {
                    showToast(res.body && res.body.error || 'Could not queue track', 'error');
                }
                return;
            }
            if (typeof showToast === 'function') {
                showToast('Queued ' + _baseName(filePath) + ' — check Downloads', 'success');
            }
        });
    }

    function browseDownloadSelected() {
        var dl = q('[data-chat-browse-dl]');
        var selected = _browse.selected || {};
        var picked = Object.values(selected);
        if (!picked.length) {
            if (typeof showToast === 'function') showToast('Nothing selected', 'info');
            return;
        }
        if (dl) { dl.disabled = true; dl.textContent = 'Queueing…'; }
        postJSON('/api/chat/user/' + encodeURIComponent(_browse.user) + '/download',
                 { files: picked }).then(function (res) {
            if (dl) { dl.disabled = false; dl.textContent = 'Download Selected'; }
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
            _browse.selected = {};
            var body = q('[data-chat-browse-body]');
            if (body) {
                body.querySelectorAll('[data-chat-browse-file], [data-chat-browse-folder-all]').forEach(function (cb) {
                    cb.checked = false;
                });
                body.querySelectorAll('.chat-file-row--selected').forEach(function (r) {
                    r.classList.remove('chat-file-row--selected');
                });
            }
            _syncBrowseDock();
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
        { c: '/np',        a: '', d: 'share what you are playing right now' },
        { c: '/want',      a: '[query]', d: 'search & post an In Search Of (Wanted) card' },
        { c: '/iso',       a: '[query]', d: 'alias for /want' },
        { c: '/friends',   a: '', d: 'open your Friends list' },
        { c: '/blocklist', a: '', d: 'open your Blocklist' },
        { c: '/bookmarks', a: '', d: 'open your Bookmarked Peers' },
        { c: '/browse',    a: '<user>', d: "browse a peer's shared files" },
        { c: '/upload',    a: '', d: 'upload & share a file via filepost.dev' },
        { c: '/play',      a: '<song or link>', d: 'queue it on the jukebox' },
        { c: '/skip',      a: '', d: 'vote to skip the current track' },
        { c: '/tune',      a: '', d: 'tune in or out of the jukebox' },
        { c: '/topic',     a: '<text>', d: 'set the room topic' },
        { c: '/poll',      a: '<question>', d: 'start a room poll' },
        { c: '/pin',       a: '', d: 'pin the latest message' },
        { c: '/gif',       a: '<search>', d: 'find a GIF' },
        { c: '/shrug',     a: '[message]', d: 'appends \u00af\\_(\u30c4)_/\u00af' },
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
        var hits = SLASH_COMMANDS.filter(function (sc) {
            if (_plainOn() && (sc.c === '/np' || sc.c === '/want' || sc.c === '/iso' || sc.c === '/poll' || sc.c === '/upload' || sc.c === '/gif')) {
                return false;
            }
            return sc.c.indexOf(v) === 0;
        });
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
        if (cmd === '/want' || cmd === '/iso') {
            input.value = '';
            if (state.view !== 'room' || _plainOn() || !state.canSend) {
                if (typeof showToast === 'function') {
                    showToast('🔍 Wanted cards can only be posted in SoulSync rooms.', 'warning');
                }
                return;
            }
            openWantedModal();
            return;
        }
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
        if (cmd === 'np') {
            if (state.view !== 'room' || _plainOn() || !state.canSend) {
                toast('🎵 Now Playing cards can only be shared in SoulSync rooms.', 'warning');
                return true;
            }
            shareNowPlaying();
            return true;
        }
        if (cmd === 'want' || cmd === 'iso') {
            if (state.view !== 'room' || _plainOn() || !state.canSend) {
                toast('🔍 Wanted cards can only be posted in SoulSync rooms.', 'warning');
                return true;
            }
            openWantedModal(arg);
            return true;
        }
        if (cmd === 'friends') {
            openSocialModal('friends');
            return true;
        }
        if (cmd === 'blocklist') {
            openSocialModal('blocked');
            return true;
        }
        if (cmd === 'bookmarks') {
            openSocialModal('bookmarks');
            return true;
        }
        if (cmd === 'browse') {
            if (arg) openBrowse(arg);
            else toast('Specify a username: /browse <user>');
            return true;
        }
        if (cmd === 'upload') {
            var fileInp = q('[data-chat-attach-file]');
            if (fileInp) fileInp.click();
            return true;
        }
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

    // Stamp a room-message payload with the SAME envelope tags the composer
    // sends: channel + thread (SoulSync room only) and avatar. Every send path
    // must use this — a file share and the GIF picker built their payloads by
    // hand, skipped the tags, and their messages folded into #general no matter
    // which channel they were sent from (kvkarlsson's uploads-in-the-wrong-
    // channel report). Plain mode is only ever true in #general (_plainOn), so
    // the early return below never drops a channel the user was standing in.
    // One control, not two. The room filter already says which world you are
    // in: "SoulSync only" means you are talking to SoulSync clients, so the
    // envelope earns its keep. "All messages" means you can SEE the vanilla
    // Soulseek users in the room, and it would be a lie to look at them while
    // sending something they cannot read.
    //
    // A PM is already plaintext, so this is a ROOM idea only.
    //
    // And a #general idea only. Every other channel, and every thread, exists
    // INSIDE the envelope: a vanilla client is never "in" #bugs, it only ever
    // sees the flat room. So "talk plain so they can read it" means nothing
    // there, and what plain mode actually did was strip the tag and refile the
    // message into #general behind the sender's back (nanomite, Deathwing:
    // "sent in bugs but it's showing up in general?!"). The channel you are
    // standing in wins over the filter.
    function _plainOn() {
        if (state.view !== 'room' || state.ssOnly) return false;
        if (_chanRoom() && (state.thread ||
                (state.channel || CHAT_DEFAULT_CHANNEL) !== CHAT_DEFAULT_CHANNEL)) return false;
        return true;
    }

    function _syncModeBtn() {
        var hint = q('[data-chat-mode-hint]');
        var on = _plainOn() && state.canSend;
        if (hint) hint.hidden = !on;
        if (!on) {
            if (state.view === 'room' && state.canSend) {
                var bar = q('[data-chat-toolbar]');
                if (bar) bar.hidden = false;
                ['[data-chat-gif-btn]', '[data-chat-poll-btn]', '[data-chat-attach-btn]',
                 '[data-chat-np-btn]', '[data-chat-want-btn]'].forEach(function (sel) {
                    var el = q(sel);
                    if (el) el.hidden = false;
                });
            }
            return;
        }

        // none of these survive without an envelope. leaving them live would
        // let someone attach a template the send is about to refuse.
        var bar = q('[data-chat-toolbar]');
        if (bar) bar.hidden = true;                 // markdown IS the envelope
        ['[data-chat-gif-btn]', '[data-chat-poll-btn]', '[data-chat-attach-btn]',
         '[data-chat-np-btn]', '[data-chat-want-btn]'].forEach(function (sel) {
            var el = q(sel);
            if (el) el.hidden = true;
        });
        // renderComposer already set a placeholder; only the exception overrides it
        var input = q('[data-chat-input]');
        if (input) input.placeholder = 'Plain message — everyone in the room can read this…';
    }

    function _tagRoomPayload(payload) {
        payload.room = state.room || '';
        // plain text has no envelope, so there is nowhere to put an avatar, a
        // channel tag or a thread id. attaching them anyway would make the
        // server refuse a message the user had no way to know was tagged.
        // Rich cards (np, want, overlay, file) are NEVER sent as plain text.
        if (_plainOn()) {
            payload.plain = true;
            return payload;
        }
        if (_myAvatar()) payload.avatar = _myAvatar();
        if (_chanRoom()) {
            payload.chan = state.channel || CHAT_DEFAULT_CHANNEL;
            if (state.thread) {
                payload.thread = state.thread.id;
                payload.thread_name = state.thread.name || '';
            }
        }
        return payload;
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
            postJSON('/api/chat/room/message', _tagRoomPayload({
                message: url,
                file: { n: meta.name || 'file', s: meta.size || 0, m: meta.mime || '' },
            })).then(done);
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
        postJSON('/api/chat/room/message', _tagRoomPayload({ message: url })).then(function (res) {
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

    // One slow slskd answer used to WIPE a working chat with the full error
    // screen until the next good poll — on a busy install that read as "chat
    // is unavailable more often than it's available" (#1194, wishx). A poll
    // failure over a rendered room now keeps the room and shows a quiet
    // reconnecting pill; the full problem screen is reserved for a cold load
    // or three misses in a row (a real outage, not a hiccup).
    function pollProblem(msg, hasContent) {
        state.failStreak += 1;
        if (!hasContent || state.failStreak >= 3) {
            _reconnectPill(false);
            renderProblem(msg);
            return;
        }
        _reconnectPill(true);
    }

    function pollRecovered() {
        state.failStreak = 0;
        _reconnectPill(false);
    }

    function _reconnectPill(show) {
        var pill = q('[data-chat-reconnect]');
        if (!show) { if (pill) pill.remove(); return; }
        if (pill) return;
        var head = q('[data-chat-messages]');
        if (!head || !head.parentNode) return;
        pill = document.createElement('div');
        pill.setAttribute('data-chat-reconnect', '');
        pill.className = 'chat-reconnect-pill';
        pill.textContent = 'connection hiccup — retrying…';
        head.parentNode.insertBefore(pill, head);
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

    // ── message edits (envelope 'ed') ────────────────────────────────────
    // Soulseek cannot unsend, so an edit is a NEW message whose 'ed' names
    // the sender's own earlier message by key; its text is the replacement.
    // Vanilla clients honestly see both lines. SoulSync folds the edit onto
    // the original, shows "(edited)" and keeps every version — and a message
    // may be edited at most twice (Boulder's rule): later edit carriers stop
    // counting as edits and render as plain messages instead. Rules every
    // client computes identically:
    //   - only the AUTHOR's carriers apply (key starts with their name)
    //   - the first EDIT_MAX carriers in stream order win
    //   - a carrier whose target isn't in the loaded window still renders
    //     (as an ✏-annotated line) — nothing is ever invisible.
    var EDIT_MAX = 2;
    var _editsByKey = {};      // target key -> [replacement texts], set per render

    function _applyEdits(msgs) {
        var present = {};
        msgs.forEach(function (m) {
            if (!m.ed) present[_msgKey(m).slice(0, 160)] = 1;
        });
        var edits = {};
        var out = [];
        msgs.forEach(function (m) {
            var target = (typeof m.ed === 'string' && m.ed) ? m.ed : null;
            if (!target) { out.push(m); return; }
            var isAuthor = target.indexOf((m.username || '') + '|') === 0;
            var slot = isAuthor ? (edits[target] || (edits[target] = [])) : null;
            var applies = !!(slot && slot.length < EDIT_MAX);
            if (applies) slot.push(String(m.message || ''));
            if (!applies || !present[target]) {
                // Not a valid/countable edit, or the original has scrolled
                // out of the window — show the carrier itself.
                out.push(Object.assign({}, m, { _editOrphan: true }));
            }
        });
        return { list: out, edits: edits };
    }

    // The applied edits for a rendered message: null, or the version list
    // (original first is NOT included — m.message stays the original).
    function _editsFor(m) {
        var slot = _editsByKey[_msgKey(m).slice(0, 160)];
        return (slot && slot.length) ? slot : null;
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
    // Discovered avatars are persisted to localStorage so silent peers who
    // have spoken previously keep their faces across reloads.
    function _avatarMap() {
        var out = {};
        try {
            var cached = JSON.parse(localStorage.getItem('chat_avatar_cache') || '{}');
            if (cached && typeof cached === 'object') {
                Object.keys(cached).forEach(function (u) {
                    var cid = _avatarId(cached[u]);
                    if (cid) out[u] = cid;
                });
            }
        } catch (e) { /* ignore parse errors */ }
        if (window.ChatProtocol && window.ChatProtocol.reduceAvatars) {
            var reduced = window.ChatProtocol.reduceAvatars(_roomEvents(), CHAT_AVATARS);
            Object.keys(reduced || {}).forEach(function (u) { out[u] = reduced[u]; });
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
        try {
            localStorage.setItem('chat_avatar_cache', JSON.stringify(out));
        } catch (e) { /* quota exceeded or private mode */ }
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
        // Show which Soulseek identity the picker is using. Reserved avatars are
        // gated on this exact name, so when one is missing this line says why
        // instead of the option just silently not being there.
        var who = q('[data-chat-avwho]');
        if (who) {
            who.textContent = state.selfName
                ? 'Soulseek: ' + state.selfName
                : 'Soulseek name not reported by slskd yet';
        }
    }

    function pickAvatar(raw) {
        var n = _avatarId(raw);                    // 0 clears
        // localStorage is the fast local cache every send reads; the server copy
        // is the source of truth so the choice follows the account to another
        // browser. Write both — the local one first so nothing waits on a fetch.
        try { localStorage.setItem('chat_avatar', String(n)); } catch (e) { /* private mode */ }
        postJSON('/api/chat/settings', { avatar: n }).catch(function () { /* local still applies */ });
        renderAvatarPicker();
        renderUserPanel();          // the account strip carries it too
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
                // Check if this incoming message confirms an optimistic pending send
                var pendingIdx = -1;
                for (var pi = 0; pi < state.msgs.length; pi++) {
                    var pm = state.msgs[pi];
                    if (pm && pm._pending && pm.message === m.message) {
                        var sameUser = (pm.username === m.username) ||
                                       (pm.self && (m.self || m.direction === 'Out' || m.username === state.selfName));
                        if (sameUser) {
                            pendingIdx = pi;
                            break;
                        }
                    }
                }
                if (pendingIdx > -1) {
                    var oldKey = _msgKey(state.msgs[pendingIdx]);
                    delete byKey[oldKey];
                    state.msgs[pendingIdx] = m;
                    byKey[k] = m;
                    added++;
                    reactionsChanged = true;
                } else {
                    byKey[k] = m; state.msgs.push(m); added++;
                    if (_pingWorthy(m)) _chatPing();
                }
            } else {
                // Upgrade card metadata or clear pending flag on existing message
                if (existing._pending || (m.np && !existing.np) || (m.want && !existing.want) || (m.overlay && !existing.overlay)) {
                    Object.assign(existing, m);
                    delete existing._pending;
                    reactionsChanged = true;
                }
                var was = JSON.stringify(existing.reactions || []);
                existing.reactions = m.reactions || [];
                _reapplyPendingReactions(existing);
                if (JSON.stringify(existing.reactions || []) !== was) reactionsChanged = true;
            }
        });
        // Expire stale pending messages older than 45 seconds so failed sends don't hang indefinitely
        var nowMs = Date.now();
        state.msgs = state.msgs.filter(function (m) {
            if (!m._pending) return true;
            var msgTs = Date.parse(m.timestamp) || 0;
            return (nowMs - msgTs) < 45000;
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
            _unfurlPendingLinks(host);
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
                    state.connectionError = res.body && res.body.error || 'Chat connection unavailable';
                    state.canSend = false;
                    renderComposer();
                    pollProblem(res.body && res.body.error
                        ? res.body.error
                        : 'Chat is unavailable right now.', state.renderedOk);
                    return;
                }
                pollRecovered();
                state.connectionError = null;
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
                state.renderedOk = true;
                renderUsers(res.body.users);
                _ingestProtocol(res.body.protocol);
                _sendJoinBeacon();
                _jbxWatchdog();   // drive the queue even with the panel closed
            });
        } else {
            work = getJSON('/api/chat/conversations/' + encodeURIComponent(state.pmUser))
                .then(function (res) {
                    if (!res.ok) {
                        state.connectionError = res.body && res.body.error || 'Chat connection unavailable';
                        state.canSend = false;
                        renderComposer();
                        pollProblem(res.body && res.body.error || 'Conversation unavailable.',
                            state.renderedOk);
                        return;
                    }
                    pollRecovered();
                    state.connectionError = null;
                    state.canSend = !!res.body.can_send;
                    renderHead(); renderComposer();
                    var incoming = res.body.messages || [];
                    var nowMs = Date.now();
                    var pending = (state.pmMsgs || []).filter(function (m) {
                        if (!m || !m._pending) return false;
                        var msgTs = Date.parse(m.timestamp) || 0;
                        return (nowMs - msgTs) < 45000;
                    });
                    var unconfirmed = [];
                    pending.forEach(function (pm) {
                        var found = incoming.some(function (im) {
                            var selfMsg = im.self === true || im.direction === 'Out' || im.username === (state.selfName || 'you');
                            return selfMsg && im.message === pm.message;
                        });
                        if (!found) unconfirmed.push(pm);
                    });
                    state.pmMsgs = incoming.concat(unconfirmed);
                    renderMessages(state.pmMsgs);
                    state.renderedOk = true;
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
            _watchTeardown();            // and the party you joined belongs to the old room too
            state.jukebox.lastRendered = '';
            state.jukebox.nowSeen = null;   // new room, new event stream, new clock base
            state.pinsOpen = false;
            state.pollDismissedAt = null;
        }
        state.room = nextRoom;
        state.thread = null;         // threads are per-room (and home-room only)
        state.arcade = null;         // ditto the Arcade — games are room-scoped
        state.topicEditing = false;
        state.typing = {};
        state.typingArmedAt = Date.now() + 2000;   // archive replay isn't live typing
        state.pingArmed = false;                   // ...and archive mentions aren't new pings
        renderTyping();
        renderBusUI();
        state.msgs = []; state.loadingOlder = false; state.historyDone = false; state.renderedOk = false;
        cancelReply();
        cancelEdit();
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

    // ── rich link unfurling & inline web preview ─────────────────────────────
    var _linkPreviewCache = {};

    function _unfurlCardHtml(p) {
        var theme = p.theme_color || '#6366f1';
        var domain = p.domain || '';
        var site = p.site_name || domain || 'Website';
        var title = p.title || p.url;
        var desc = p.description ? String(p.description).slice(0, 260) : '';
        var thumb = p.image || '';

        return '<div class="chat-unfurl-card" data-unfurl-url="' + attr(p.url) + '" style="--unfurl-color:' + attr(theme) + '">' +
            '<div class="chat-unfurl-bar"></div>' +
            '<div class="chat-unfurl-main">' +
                '<div class="chat-unfurl-head">' +
                    '<span class="chat-unfurl-site">' +
                        (domain ? '<img class="chat-unfurl-favicon" src="https://www.google.com/s2/favicons?domain=' + attr(domain) + '&sz=32" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()">' : '') +
                        '<span>' + esc(site) + '</span>' +
                    '</span>' +
                    '<div class="chat-unfurl-actions">' +
                        '<button type="button" class="chat-unfurl-btn chat-unfurl-btn--preview" data-chat-preview-url="' + attr(p.url) + '" title="Open interactive inline preview">👁 Preview Inline</button>' +
                        '<a href="' + attr(p.url) + '" target="_blank" rel="noopener noreferrer" class="chat-unfurl-btn" title="Open in browser">↗ Open</a>' +
                        '<button type="button" class="chat-unfurl-btn chat-unfurl-btn--close" data-chat-unfurl-close title="Dismiss preview">✕</button>' +
                    '</div>' +
                '</div>' +
                '<a href="' + attr(p.url) + '" target="_blank" rel="noopener noreferrer" class="chat-unfurl-title">' + esc(title) + '</a>' +
                (desc ? '<div class="chat-unfurl-desc">' + esc(desc) + '</div>' : '') +
                (thumb ? '<div class="chat-unfurl-thumb-wrap"><img class="chat-unfurl-thumb" src="' + attr(thumb) + '" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="var w=this.closest(\'.chat-unfurl-thumb-wrap\'); if(w) w.remove();"></div>' : '') +
            '</div>' +
        '</div>';
    }

    function _attachUnfurlCard(line, p, container) {
        if (!line || !p || !p.url) return;
        if (line.querySelector('.chat-unfurl-card[data-unfurl-url="' + attr(p.url) + '"]')) return;
        var div = document.createElement('div');
        div.innerHTML = _unfurlCardHtml(p);
        var card = div.firstElementChild;
        if (card) {
            line.appendChild(card);
            if (state.stickBottom && container) {
                container.scrollTop = container.scrollHeight;
            }
        }
    }

    function _unfurlPendingLinks(container) {
        if (!container) return;
        var links = container.querySelectorAll('a.chat-link:not([data-unfurl-checked])');
        if (!links.length) return;

        links.forEach(function (link) {
            link.setAttribute('data-unfurl-checked', '1');
            var href = link.getAttribute('href') || '';
            if (!href || href.charAt(0) === '/' || href.indexOf('javascript:') === 0) return;
            if (IMG_RE.test(href)) return;
            if (_ytId(href) || _spotifyEmbedInfo(href) || _deezerEmbedInfo(href) || _soundcloudEmbedInfo(href)) return;

            var line = link.closest('.chat-line');
            if (!line) return;

            if (_linkPreviewCache[href]) {
                var cached = _linkPreviewCache[href];
                if (cached.ok && cached.preview && (cached.preview.title || cached.preview.description)) {
                    _attachUnfurlCard(line, cached.preview, container);
                }
                return;
            }

            getJSON('/api/chat/link-preview?url=' + encodeURIComponent(href)).then(function (res) {
                if (!res.ok || !res.body || !res.body.ok) {
                    _linkPreviewCache[href] = { ok: false };
                    return;
                }
                var p = res.body.preview || res.body;
                if (!p || (!p.title && !p.description)) {
                    _linkPreviewCache[href] = { ok: false };
                    return;
                }
                _linkPreviewCache[href] = { ok: true, preview: p };
                _attachUnfurlCard(line, p, container);
            }).catch(function () {
                _linkPreviewCache[href] = { ok: false };
            });
        });
    }

    function openWebPreviewModal(url) {
        var modal = document.getElementById('chat-web-preview-modal');
        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'chat-web-preview-modal';
            modal.className = 'chat-web-modal-overlay';
            modal.innerHTML =
                '<div class="chat-web-modal-dialog">' +
                    '<div class="chat-web-modal-header">' +
                        '<div class="chat-web-modal-title">' +
                            '<span class="chat-web-modal-lock">🔒</span>' +
                            '<span class="chat-web-modal-url"></span>' +
                        '</div>' +
                        '<div class="chat-web-modal-actions">' +
                            '<button type="button" class="chat-web-modal-btn" data-chat-web-modal-copy title="Copy Link">📋 Copy</button>' +
                            '<a href="#" target="_blank" rel="noopener noreferrer" class="chat-web-modal-btn chat-web-modal-ext" title="Open in External Tab">↗ External</a>' +
                            '<button type="button" class="chat-web-modal-btn chat-web-modal-close" title="Close Preview">✕</button>' +
                        '</div>' +
                    '</div>' +
                    '<div class="chat-web-modal-body">' +
                        '<iframe class="chat-web-modal-iframe" sandbox="allow-scripts allow-same-origin allow-forms allow-popups" referrerpolicy="no-referrer"></iframe>' +
                    '</div>' +
                '</div>';
            document.body.appendChild(modal);

            modal.querySelector('.chat-web-modal-close').addEventListener('click', function () {
                closeWebPreviewModal();
            });
            modal.addEventListener('click', function (e) {
                if (e.target === modal) closeWebPreviewModal();
            });
            modal.querySelector('[data-chat-web-modal-copy]').addEventListener('click', function () {
                var currUrl = modal.getAttribute('data-url');
                if (currUrl && navigator.clipboard) {
                    navigator.clipboard.writeText(currUrl);
                    _ovToast('Copied link to clipboard!', 'success');
                }
            });
        }
        modal.setAttribute('data-url', url);
        var domain = '';
        try { domain = new URL(url).hostname; } catch (e) { domain = url; }
        modal.querySelector('.chat-web-modal-url').textContent = domain + ' (' + url.slice(0, 50) + (url.length > 50 ? '…' : '') + ')';
        modal.querySelector('.chat-web-modal-ext').href = url;
        var ifr = modal.querySelector('.chat-web-modal-iframe');
        if (ifr) ifr.src = url;
        modal.classList.add('chat-web-modal-overlay--open');
    }

    function closeWebPreviewModal() {
        var modal = document.getElementById('chat-web-preview-modal');
        if (modal) {
            modal.classList.remove('chat-web-modal-overlay--open');
            var ifr = modal.querySelector('.chat-web-modal-iframe');
            if (ifr) ifr.src = 'about:blank';
        }
    }
    var _availRooms = null;

    // ── Discord-style Social & Lists Drawer (Friends, Blocklist, Bookmarks) ──
    var _socialTab = 'friends';
    var _socialFilter = '';

    function openSocialModal(tab) {
        if (tab) _socialTab = tab;
        closeUserCard();
        var drawer = q('[data-chat-social-drawer]');
        var backdrop = q('[data-chat-social-backdrop]');
        if (!drawer) return;
        drawer.hidden = false;
        if (backdrop) backdrop.hidden = false;
        requestAnimationFrame(function () {
            drawer.classList.add('visible');
            if (backdrop) backdrop.classList.add('visible');
        });
        _socialFilter = '';
        var fInp = drawer.querySelector('[data-chat-social-filter]');
        if (fInp) fInp.value = '';
        var aInp = drawer.querySelector('[data-chat-social-input]');
        if (aInp) {
            aInp.value = '';
            aInp.focus();
        }
        renderSocialList();
        _updateSocialBadges();
    }

    function closeSocialModal() {
        var drawer = q('[data-chat-social-drawer]');
        var backdrop = q('[data-chat-social-backdrop]');
        if (drawer) {
            drawer.classList.remove('visible');
            setTimeout(function () { drawer.hidden = true; }, 320);
        }
        if (backdrop) {
            backdrop.classList.remove('visible');
            setTimeout(function () { backdrop.hidden = true; }, 320);
        }
    }

    function renderSocialList() {
        var drawer = q('[data-chat-social-drawer]');
        if (!drawer || drawer.hidden) return;

        var friends = friendsSet();
        var blocked = blockedSet();
        var bookmarks = peerBookmarksSet();

        // Update tab navigation active state
        drawer.querySelectorAll('[data-chat-social-tab]').forEach(function (tabEl) {
            var tabId = tabEl.getAttribute('data-chat-social-tab');
            tabEl.classList.toggle('chat-social-nav-tab--active', tabId === _socialTab);
        });

        // Update add banner label & placeholder
        var addLabel = drawer.querySelector('[data-chat-social-add-label]');
        var addInp = drawer.querySelector('[data-chat-social-input]');
        if (_socialTab === 'friends') {
            if (addLabel) addLabel.textContent = 'ADD FRIEND';
            if (addInp) addInp.placeholder = 'Enter Soulseek username to add as friend…';
        } else if (_socialTab === 'blocked') {
            if (addLabel) addLabel.textContent = 'BLOCK USER';
            if (addInp) addInp.placeholder = 'Enter Soulseek username to block…';
        } else {
            if (addLabel) addLabel.textContent = 'BOOKMARK PEER';
            if (addInp) addInp.placeholder = 'Enter Soulseek username to bookmark…';
        }

        var listEl = drawer.querySelector('[data-chat-social-list]');
        if (!listEl) return;

        var items = [];
        if (_socialTab === 'friends') items = friends;
        else if (_socialTab === 'blocked') items = blocked;
        else items = bookmarks;

        var f = String(_socialFilter || '').trim().toLowerCase();
        if (f) {
            items = items.filter(function (u) { return String(u).toLowerCase().indexOf(f) > -1; });
        }

        var totalEl = drawer.querySelector('[data-chat-social-total]');
        if (totalEl) {
            var unit = _socialTab === 'friends' ? 'friend' : (_socialTab === 'blocked' ? 'blocked' : 'peer');
            totalEl.textContent = items.length + ' ' + unit + (items.length === 1 ? '' : 's');
        }

        if (!items.length) {
            var emptyIcon = _socialTab === 'friends' ? '⭐' : (_socialTab === 'blocked' ? '🚫' : '📚');
            var emptyTitle = _socialTab === 'friends' ? 'No friends yet' : (_socialTab === 'blocked' ? 'No blocked users' : 'No saved peers yet');
            var emptyDesc = _socialTab === 'friends'
                ? 'Add friends using the input above or click ⭐ Add Friend on any user in chat!'
                : (_socialTab === 'blocked'
                    ? 'Blocked users have their room messages and private messages completely hidden.'
                    : 'Click ⭐ Bookmark while exploring peer files or add their username above to save them.');
            if (f) {
                emptyTitle = 'No matches found';
                emptyDesc = 'No ' + _socialTab + ' match "' + esc(f) + '"';
            }
            listEl.innerHTML = '<div class="chat-social-empty">' +
                '<div style="font-size:32px;margin-bottom:8px;opacity:0.8;">' + emptyIcon + '</div>' +
                '<div style="font-weight:700;font-size:15px;color:#fff;margin-bottom:4px;">' + emptyTitle + '</div>' +
                '<div>' + emptyDesc + '</div>' +
            '</div>';
            return;
        }

        var avMap = _avatarMap();
        var onlineUsers = state.users || [];
        var onlineMap = {};
        onlineUsers.forEach(function (u) { onlineMap[String(u).toLowerCase()] = true; });

        listEl.innerHTML = items.map(function (u) {
            var av = avMap && avMap[u];
            var isOnline = !!onlineMap[String(u).toLowerCase()];

            var subText = '';
            if (_socialTab === 'friends') {
                subText = isOnline ? '⭐ Friend • Online in room' : '⭐ Friend • Soulseek user';
            } else if (_socialTab === 'blocked') {
                subText = '🚫 Blocked • Chat & PMs suppressed';
            } else {
                subText = isOnline ? '📚 Saved Peer • Online in room' : '📚 Saved Peer';
            }

            var html = '<div class="chat-social-row">' +
                '<div class="chat-social-row-av">' +
                    '<span class="chat-user-av" style="width:36px;height:36px;font-size:13px;">' +
                        _avatarHtml(u, av, 'chat-av--fill') +
                        '<span class="chat-user-dot' + (isOnline ? ' chat-user-dot--tuned' : '') + '"></span>' +
                    '</span>' +
                '</div>' +
                '<div class="chat-social-row-info">' +
                    '<div class="chat-social-row-name" data-chat-user="' + attr(u) + '" title="View user card">' + esc(u) + '</div>' +
                    '<div class="chat-social-row-sub">' + subText + '</div>' +
                '</div>' +
                '<div class="chat-social-row-actions">';

            if (_socialTab === 'friends') {
                html += '<button type="button" class="chat-social-action-btn" data-chat-browse-user="' + attr(u) + '" title="Browse their files">📁 Browse</button>' +
                        '<button type="button" class="chat-social-action-btn" data-chat-open-pm="' + attr(u) + '" title="Send direct message">💬 Message</button>' +
                        '<button type="button" class="chat-social-action-btn chat-social-action-btn--danger" data-chat-social-remove-friend="' + attr(u) + '" title="Remove from friends">✕ Remove</button>';
            } else if (_socialTab === 'blocked') {
                html += '<button type="button" class="chat-social-action-btn chat-social-action-btn--danger" data-chat-social-unblock="' + attr(u) + '" title="Unblock user">Unblock</button>';
            } else {
                html += '<button type="button" class="chat-social-action-btn" data-chat-browse-user="' + attr(u) + '" title="Browse their files">📁 Browse</button>' +
                        '<button type="button" class="chat-social-action-btn" data-chat-open-pm="' + attr(u) + '" title="Send direct message">💬 Message</button>' +
                        '<button type="button" class="chat-social-action-btn chat-social-action-btn--danger" data-chat-social-remove-bookmark="' + attr(u) + '" title="Remove bookmark">★ Remove</button>';
            }
            html += '</div></div>';
            return html;
        }).join('');
    }

    function addSocialUser() {
        var drawer = q('[data-chat-social-drawer]');
        if (!drawer) return;
        var inp = drawer.querySelector('[data-chat-social-input]');
        if (!inp) return;
        var val = String(inp.value || '').trim();
        if (!val) return;

        if (_socialTab === 'friends') {
            if (!isFriend(val)) toggleFriend(val);
            if (typeof showToast === 'function') showToast('Added ' + val + ' to friends', 'success');
        } else if (_socialTab === 'blocked') {
            if (!isBlocked(val)) toggleBlocked(val);
            if (typeof showToast === 'function') showToast('Blocked ' + val, 'info');
        } else {
            if (!isPeerBookmarked(val)) togglePeerBookmark(val);
            if (typeof showToast === 'function') showToast('Bookmarked peer ' + val, 'success');
        }
        inp.value = '';
        _updateSocialBadges();
        renderSocialList();
    }

    function _openSavedPeersPicker() {
        openSocialModal('bookmarks');
    }

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
        unhideDm(username);
        state.view = 'pm'; state.pmUser = username; state.lastStamp = null; state.stickBottom = true;
        state.searchMode = false; state.renderedOk = false;
        state.pmMsgs = [];
        state.renderedCount = 0; hideJumpPill(); state.newMarker = null;
        cancelReply();
        cancelEdit();
        state.topicEditing = false;
        renderHead(); renderComposer(); renderBusUI();   // hides the panels (audio keeps playing)
        var host = q('[data-chat-messages]');
        if (host) host.innerHTML = '<div class="chat-empty">Loading…</div>';
        refresh();
        if (state.convos) renderSide(state.convos);
    }

    function send() {
        var input = q('[data-chat-input]');
        if (!input) return;
        var text = (input.value || '').trim();
        if (!text || !state.canSend) return;
        // Belt and braces: the composer is hidden in the Arcade, but a stray
        // Enter must not post into whichever channel was last open — the user
        // is looking at a chessboard, not at that channel, so the message
        // would vanish from their view the moment it sent.
        if (_arcOn()) { input.value = ''; return; }
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
        if (state.view === 'room') _tagRoomPayload(payload);
        var sentReply = null;
        if (state.view === 'room' && state.replyTo) {
            payload.reply = state.replyTo;
            sentReply = state.replyTo;
        }
        var sentEdit = null;
        if (state.view === 'room' && state.editing && state.editing.key) {
            payload.edit = state.editing.key;
            sentEdit = state.editing.key;
        }
        postJSON(url, payload).then(function (res) {
            if (!res.ok) {
                if (typeof showToast === 'function') {
                    showToast(res.body && res.body.error || 'Message not sent', 'error');
                }
                input.value = input.value ? text + '\n' + input.value : text; // preserve newer typing too
                if (res.body && (res.body.code === 'slskd_disconnected' || res.body.code === 'slskd_unavailable')) {
                    state.connectionError = res.body.error; state.canSend = false; renderComposer();
                }
                return;
            }
            // Optimistic echo: store in state.msgs (room) or state.pmMsgs (PM) with _pending: true.
            // When renderMessages is called by refresh(), the message remains rendered in state
            // and does NOT vanish from the screen while waiting for the server echo.
            var myName = state.selfName || 'you';
            var nowIso = new Date().toISOString();
            if (state.view === 'room') {
                if (!sentEdit) {
                    var optMsg = {
                        username: myName,
                        message: text,
                        timestamp: nowIso,
                        self: true,
                        reply: sentReply || undefined,
                        rich: state.view === 'room' && !_plainOn(),
                        chan: state.channel || 'general',
                        _pending: true,
                    };
                    state.msgs = state.msgs || [];
                    state.msgs.push(optMsg);
                    state.lastStamp = null;
                    renderMessages(state.msgs);
                }
            } else if (state.view === 'pm') {
                var optPm = {
                    username: myName,
                    message: text,
                    timestamp: nowIso,
                    self: true,
                    direction: 'Out',
                    _pending: true,
                };
                state.pmMsgs = state.pmMsgs || [];
                state.pmMsgs.push(optPm);
                state.lastStamp = null;
                renderMessages(state.pmMsgs);
            }
            state.stickBottom = true;
            cancelReply();
            cancelEdit();
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
                var vid = t.getAttribute('data-chat-embed-yt');
                var jbxBtn = t.nextElementSibling && t.nextElementSibling.matches('[data-chat-jbx-add]') ? t.nextElementSibling : null;
                if (jbxBtn) jbxBtn.remove();
                t.outerHTML = '<div class="chat-embed-card chat-embed-card--yt" data-embed-yt="' + attr(vid) + '">' +
                    '<div class="chat-embed-card-head">' +
                        '<div class="chat-embed-card-brand">' +
                            '<span class="chat-embed-dot chat-embed-dot--yt"></span>' +
                            '<span class="chat-embed-brand-label">YouTube Video</span>' +
                        '</div>' +
                        '<div class="chat-embed-card-actions">' +
                            '<button type="button" class="chat-embed-card-btn chat-embed-card-btn--jbx" data-chat-jbx-add="' + attr(vid) + '" title="Queue to Room Jukebox">🎵 Jukebox</button>' +
                            '<a href="https://www.youtube.com/watch?v=' + encodeURIComponent(vid) + '" target="_blank" rel="noopener noreferrer" class="chat-embed-card-btn" title="Open on YouTube">↗ Watch</a>' +
                            '<button type="button" class="chat-embed-card-btn chat-embed-card-btn--close" data-chat-embed-close title="Close Player">✕</button>' +
                        '</div>' +
                    '</div>' +
                    '<div class="chat-embed-card-body">' +
                        '<span class="chat-embed-frame"><iframe src="https://www.youtube-nocookie.com/embed/' +
                        attr(vid) +
                        '?autoplay=1&rel=0&playsinline=1" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" allowfullscreen ' +
                        'referrerpolicy="strict-origin-when-cross-origin" loading="lazy"></iframe></span>' +
                    '</div>' +
                '</div>';
                return;
            }
            t = e.target.closest('[data-chat-jbx-add]');
            if (t) {
                var jvid = t.getAttribute('data-chat-jbx-add');
                if (jvid) _jbxPick({ id: jvid, title: 'YouTube Video' });
                return;
            }
            t = e.target.closest('[data-chat-embed-spotify]');
            if (t) {
                var spPath = t.getAttribute('data-chat-embed-spotify');
                var isAlbum = spPath.indexOf('album') === 0 || spPath.indexOf('playlist') === 0;
                var sph = isAlbum ? 352 : 152;
                t.outerHTML = '<div class="chat-embed-card chat-embed-card--spotify">' +
                    '<div class="chat-embed-card-head">' +
                        '<div class="chat-embed-card-brand">' +
                            '<span class="chat-embed-dot chat-embed-dot--sp"></span>' +
                            '<span class="chat-embed-brand-label">Spotify Player</span>' +
                        '</div>' +
                        '<div class="chat-embed-card-actions">' +
                            '<a href="https://open.spotify.com/' + attr(spPath) + '" target="_blank" rel="noopener noreferrer" class="chat-embed-card-btn" title="Open on Spotify">↗ Open</a>' +
                            '<button type="button" class="chat-embed-card-btn chat-embed-card-btn--close" data-chat-embed-close title="Close Player">✕</button>' +
                        '</div>' +
                    '</div>' +
                    '<div class="chat-embed-card-body">' +
                        '<iframe class="chat-embed-spotify-frame" src="https://open.spotify.com/embed/' + attr(spPath) + '?utm_source=generator&theme=0" ' +
                        'width="100%" height="' + sph + '" frameborder="0" allowfullscreen="" ' +
                        'allow="autoplay; clipboard-write; encrypted-media; fullscreen; picture-in-picture" ' +
                        'loading="lazy" referrerpolicy="no-referrer"></iframe>' +
                    '</div>' +
                '</div>';
                return;
            }
            t = e.target.closest('[data-chat-embed-deezer]');
            if (t) {
                var dzPath = t.getAttribute('data-chat-embed-deezer');
                var isDzAlb = dzPath.indexOf('album') === 0 || dzPath.indexOf('playlist') === 0;
                var dzh = isDzAlb ? 300 : 150;
                t.outerHTML = '<div class="chat-embed-card chat-embed-card--deezer">' +
                    '<div class="chat-embed-card-head">' +
                        '<div class="chat-embed-card-brand">' +
                            '<span class="chat-embed-dot chat-embed-dot--dz"></span>' +
                            '<span class="chat-embed-brand-label">Deezer Player</span>' +
                        '</div>' +
                        '<div class="chat-embed-card-actions">' +
                            '<a href="https://www.deezer.com/' + attr(dzPath) + '" target="_blank" rel="noopener noreferrer" class="chat-embed-card-btn" title="Open on Deezer">↗ Open</a>' +
                            '<button type="button" class="chat-embed-card-btn chat-embed-card-btn--close" data-chat-embed-close title="Close Player">✕</button>' +
                        '</div>' +
                    '</div>' +
                    '<div class="chat-embed-card-body">' +
                        '<iframe class="chat-embed-deezer-frame" src="https://widget.deezer.com/widget/dark/' + attr(dzPath) + '" ' +
                        'width="100%" height="' + dzh + '" frameborder="0" allowtransparency="true" ' +
                        'allow="encrypted-media; clipboard-write" loading="lazy" referrerpolicy="no-referrer"></iframe>' +
                    '</div>' +
                '</div>';
                return;
            }
            t = e.target.closest('[data-chat-embed-sc]');
            if (t) {
                var scRaw = decodeURIComponent(t.getAttribute('data-chat-embed-sc'));
                t.outerHTML = '<div class="chat-embed-card chat-embed-card--sc">' +
                    '<div class="chat-embed-card-head">' +
                        '<div class="chat-embed-card-brand">' +
                            '<span class="chat-embed-dot chat-embed-dot--sc"></span>' +
                            '<span class="chat-embed-brand-label">SoundCloud Player</span>' +
                        '</div>' +
                        '<div class="chat-embed-card-actions">' +
                            '<a href="' + attr(scRaw) + '" target="_blank" rel="noopener noreferrer" class="chat-embed-card-btn" title="Open on SoundCloud">↗ Open</a>' +
                            '<button type="button" class="chat-embed-card-btn chat-embed-card-btn--close" data-chat-embed-close title="Close Player">✕</button>' +
                        '</div>' +
                    '</div>' +
                    '<div class="chat-embed-card-body">' +
                        '<iframe class="chat-embed-sc-frame" width="100%" height="166" scrolling="no" frameborder="no" allow="autoplay" ' +
                        'src="https://w.soundcloud.com/player/?url=' + encodeURIComponent(scRaw) + '&color=%23ff5500&auto_play=true&hide_related=true&show_comments=false&show_user=true&show_reposts=false&show_teaser=false" ' +
                        'loading="lazy" referrerpolicy="no-referrer"></iframe>' +
                    '</div>' +
                '</div>';
                return;
            }
            t = e.target.closest('[data-chat-embed-close]');
            if (t) {
                var card = t.closest('.chat-embed-card');
                if (card) {
                    var ytid = card.getAttribute('data-embed-yt');
                    if (ytid) {
                        card.outerHTML = '<button type="button" class="chat-embed-chip chat-embed-chip--yt" data-chat-embed-yt="' +
                            attr(ytid) + '" title="Play YouTube video inline in chat">▶ play</button>' +
                            ' <button type="button" class="chat-embed-action chat-embed-action--jbx" data-chat-jbx-add="' +
                            attr(ytid) + '" title="Queue to Room Jukebox">🎵 jukebox</button>';
                    } else {
                        card.remove();
                    }
                }
                return;
            }
            t = e.target.closest('[data-chat-preview-url]');
            if (t) {
                var pUrl = t.getAttribute('data-chat-preview-url');
                var uCard = t.closest('.chat-unfurl-card');
                if (uCard) {
                    var existingFrame = uCard.querySelector('.chat-inline-web-frame');
                    if (existingFrame) {
                        existingFrame.remove();
                        t.classList.remove('chat-unfurl-btn--active');
                        t.textContent = '👁 Preview Inline';
                        return;
                    }
                    t.classList.add('chat-unfurl-btn--active');
                    t.textContent = '✕ Close Preview';

                    var pDomain = '';
                    try { pDomain = new URL(pUrl).hostname; } catch (err) { pDomain = pUrl; }

                    var frameContainer = document.createElement('div');
                    frameContainer.className = 'chat-inline-web-frame';
                    frameContainer.innerHTML =
                        '<div class="chat-inline-web-toolbar">' +
                            '<div class="chat-inline-web-ssl" title="Secure web preview"><span class="chat-inline-ssl-lock">🔒</span> ' + esc(pDomain) + '</div>' +
                            '<div class="chat-inline-web-actions">' +
                                '<button type="button" class="chat-inline-web-btn" data-chat-web-reload title="Reload frame">⟳</button>' +
                                '<button type="button" class="chat-inline-web-btn" data-chat-web-expand="' + attr(pUrl) + '" title="Expand to slide-over modal">⛶</button>' +
                                '<a href="' + attr(pUrl) + '" target="_blank" rel="noopener noreferrer" class="chat-inline-web-btn" title="Open in browser tab">↗</a>' +
                                '<button type="button" class="chat-inline-web-btn chat-inline-web-btn--close" data-chat-web-close title="Close inline preview">✕</button>' +
                            '</div>' +
                        '</div>' +
                        '<div class="chat-inline-web-body">' +
                            '<div class="chat-inline-web-loader">Loading preview…</div>' +
                            '<iframe src="' + attr(pUrl) + '" sandbox="allow-scripts allow-same-origin allow-forms allow-popups" ' +
                                'loading="lazy" referrerpolicy="no-referrer"></iframe>' +
                            '<div class="chat-inline-web-fallback" style="display:none;">' +
                                '<span>If this site restricts inline frame viewing (X-Frame-Options), </span>' +
                                '<a href="' + attr(pUrl) + '" target="_blank" rel="noopener noreferrer" class="chat-inline-fallback-btn">Open in New Tab ↗</a>' +
                            '</div>' +
                        '</div>';
                    uCard.appendChild(frameContainer);

                    var ifr = frameContainer.querySelector('iframe');
                    var loader = frameContainer.querySelector('.chat-inline-web-loader');
                    var fallback = frameContainer.querySelector('.chat-inline-web-fallback');

                    var loadTimer = setTimeout(function () {
                        if (loader) loader.style.display = 'none';
                        if (fallback) fallback.style.display = 'flex';
                    }, 4500);

                    ifr.onload = function () {
                        clearTimeout(loadTimer);
                        if (loader) loader.style.display = 'none';
                    };
                    ifr.onerror = function () {
                        clearTimeout(loadTimer);
                        if (loader) loader.style.display = 'none';
                        if (fallback) fallback.style.display = 'flex';
                    };
                }
                return;
            }
            t = e.target.closest('[data-chat-web-close]');
            if (t) {
                var fr = t.closest('.chat-inline-web-frame');
                if (fr) {
                    var pc = fr.closest('.chat-unfurl-card');
                    if (pc) {
                        var pb = pc.querySelector('[data-chat-preview-url]');
                        if (pb) {
                            pb.classList.remove('chat-unfurl-btn--active');
                            pb.textContent = '👁 Preview Inline';
                        }
                    }
                    fr.remove();
                }
                return;
            }
            t = e.target.closest('[data-chat-web-reload]');
            if (t) {
                var rf = t.closest('.chat-inline-web-frame');
                var rifr = rf && rf.querySelector('iframe');
                if (rifr) {
                    var osrc = rifr.src;
                    rifr.src = '';
                    rifr.src = osrc;
                }
                return;
            }
            t = e.target.closest('[data-chat-web-expand]');
            if (t) {
                var expUrl = t.getAttribute('data-chat-web-expand');
                if (expUrl) openWebPreviewModal(expUrl);
                return;
            }
            t = e.target.closest('[data-chat-unfurl-close]');
            if (t) {
                var uc = t.closest('.chat-unfurl-card');
                if (uc) uc.remove();
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
            t = e.target.closest('[data-chat-overlay-add]');
            if (t) { _adoptSharedOverlay(t); return; }
            t = e.target.closest('[data-chat-attach-btn]');
            if (t) { toggleAttachPanel(); return; }
            t = e.target.closest('[data-chat-attach-overlay]');
            if (t) { _pickOverlayToShare(); return; }
            t = e.target.closest('[data-chat-ovl-pick]');
            if (t) {
                _shareOverlayById(t.getAttribute('data-chat-ovl-pick'),
                                  t.getAttribute('data-chat-ovl-name'));
                return;
            }
            t = e.target.closest('[data-chat-ovl-close]');
            if (t) { _closeOverlayPicker(); return; }
            // Click the backdrop to dismiss, like every other chat modal.
            if (e.target.matches && e.target.matches('[data-chat-ovl-modal]')) {
                _closeOverlayPicker(); return;
            }
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
                cancelEdit();
                startReply(t.getAttribute('data-chat-reply-user'),
                           t.getAttribute('data-chat-reply-x'));
                return;
            }
            t = e.target.closest('[data-chat-edit-key]');
            if (t) {
                startEdit(t.getAttribute('data-chat-edit-key'),
                          t.getAttribute('data-chat-edit-text'));
                return;
            }
            t = e.target.closest('[data-chat-reply-cancel]');
            if (t) { cancelReply(); cancelEdit(); return; }
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
            t = e.target.closest('[data-chat-chan-mute]');
            if (t) {
                // The bell sits INSIDE the channel row button — handle it
                // first or the click would also switch channels.
                toggleChanMuted(t.getAttribute('data-chat-chan-mute'));
                return;
            }
            t = e.target.closest('[data-chat-chan]');
            if (t) { switchChannel(t.getAttribute('data-chat-chan')); return; }
            // ── Arcade ──
            t = e.target.closest('[data-chat-arc-home]');
            if (t) { openArcade(null); return; }
            t = e.target.closest('[data-chat-arc-new]');
            if (t) {
                arcNewGame(t.getAttribute('data-chat-arc-new'), '',
                           t.getAttribute('data-chat-arc-variant'),
                           t.getAttribute('data-chat-arc-room'));
                return;
            }
            t = e.target.closest('[data-chat-arc-col]');
            if (t) { _arcColumnClick(t.getAttribute('data-chat-arc-col')); return; }
            t = e.target.closest('[data-chat-arc-cell]');
            if (t) { _arcCellClick(t.getAttribute('data-chat-arc-cell')); return; }
            t = e.target.closest('[data-chat-arc-pass]');
            if (t) {
                var passGame = _arcGame(t.getAttribute('data-chat-arc-pass'));
                if (passGame && passGame.variant === 'othello' && state.canSend) {
                    if (_arcCanVote(passGame)) arcVote(passGame.id, 'p');
                    else arcMove(passGame.id, 'p');
                }
                return;
            }
            t = e.target.closest('[data-chat-arc-join]');
            if (t) { arcJoin(t.getAttribute('data-chat-arc-join')); return; }
            t = e.target.closest('[data-chat-arc-claim]');
            if (t) { arcClaim(t.getAttribute('data-chat-arc-claim')); return; }
            t = e.target.closest('[data-chat-arc-stake]');
            if (t) {
                state.arcade = state.arcade || {};
                state.arcade.stake = parseInt(t.getAttribute('data-chat-arc-stake'), 10) || 0;
                renderArcade();
                return;
            }
            t = e.target.closest('[data-chat-arc-cold]');
            if (t) {
                if (state.arcade) state.arcade.showCold = !state.arcade.showCold;
                renderArcade();
                return;
            }
            t = e.target.closest('[data-chat-arc-kill]');
            if (t) {
                e.stopPropagation();     // the card click would open the board
                var killId = t.getAttribute('data-chat-arc-kill');
                var doKill = function () {
                    sendProtocol('mod.gamekill', { id: killId });
                    if (typeof showToast === 'function') showToast('🛑 Game ended for the room', 'info');
                };
                if (typeof showConfirmDialog === 'function') {
                    showConfirmDialog({ title: 'End Game',
                        message: 'End this game for everyone in the room? The board disappears for all players.',
                        confirmText: 'End game', destructive: true }).then(function (ok) {
                        if (ok !== false) doKill();
                    });
                } else { doKill(); }
                return;
            }
            t = e.target.closest('[data-chat-arc-resign]');
            if (t) { arcResign(t.getAttribute('data-chat-arc-resign')); return; }
            t = e.target.closest('[data-chat-arc-draw]');
            if (t) { arcDraw(t.getAttribute('data-chat-arc-draw')); return; }
            t = e.target.closest('[data-chat-arc-pgn]');
            if (t) { arcDownloadPgn(t.getAttribute('data-chat-arc-pgn')); return; }
            t = e.target.closest('[data-chat-arc-pgncopy]');
            if (t) { arcCopyPgn(t.getAttribute('data-chat-arc-pgncopy')); return; }
            t = e.target.closest('[data-chat-arc-reveal]');
            if (t) {
                if (state.arcade) { state.arcade.reveal = !state.arcade.reveal; renderArcade(); }
                return;
            }
            t = e.target.closest('[data-chat-slot-open]');
            if (t) {
                if (state.arcade) { state.arcade.slots = true; state.arcade.game = null; }
                renderArcade(); renderHead(); renderChannels();
                return;
            }
            t = e.target.closest('[data-chat-slot-stake]');
            if (t) {
                _slotState().stake = parseInt(t.getAttribute('data-chat-slot-stake'), 10) || 5;
                renderArcade();
                return;
            }
            t = e.target.closest('[data-chat-slot-pull]');
            if (t) { _slotPull(); return; }
            t = e.target.closest('[data-chat-bs-place]');
            if (t) { _bsPlaceAt(parseInt(t.getAttribute('data-chat-bs-place'), 10)); return; }
            t = e.target.closest('[data-chat-bs-fire]');
            if (t) {
                var fg = state.arcade && state.arcade.game ? _arcGame(state.arcade.game) : null;
                if (fg) arcMove(fg.id, 's:' + _bsCellName(
                    parseInt(t.getAttribute('data-chat-bs-fire'), 10)));
                return;
            }
            t = e.target.closest('[data-chat-bs-random]');
            if (t) {
                var d = _bsDraft();
                d.board = _bsRandomBoard(); d.next = BS_FLEET.length;
                renderArcade();
                return;
            }
            t = e.target.closest('[data-chat-bs-rotate]');
            if (t) { _bsDraft().horiz = !_bsDraft().horiz; renderArcade(); return; }
            t = e.target.closest('[data-chat-bs-clear]');
            if (t) {
                var d2 = _bsDraft();
                d2.board = '.'.repeat(BS_W * BS_H); d2.next = 0;
                renderArcade();
                return;
            }
            t = e.target.closest('[data-chat-bs-commit]');
            if (t) { _bsCommit(t.getAttribute('data-chat-bs-commit')); return; }
            t = e.target.closest('[data-chat-arc-cancel]');
            if (t) { arcCancel(t.getAttribute('data-chat-arc-cancel')); return; }
            t = e.target.closest('[data-chat-arc-sync]');
            if (t) { arcSync(t.getAttribute('data-chat-arc-sync'), false); return; }
            t = e.target.closest('[data-chat-arc-accept]');
            if (t) {
                var agid = t.getAttribute('data-chat-arc-accept');
                showConfirmDialog({
                    title: 'Accept their position?',
                    message: 'This board and your opponent\'s disagreed, so the game was ' +
                             'frozen rather than guessing which was right. Accepting takes ' +
                             'their position and continues from there.',
                    confirmText: 'Accept and continue',
                }).then(function (ok) { if (ok) arcSync(agid, true); });
                return;
            }
            // LAST of the arcade handlers on purpose. A lobby card carries
            // data-chat-arc-open and CONTAINS the action buttons, so checking
            // it earlier made every Join / Withdraw / Take-the-seat click
            // resolve to the card and just open the game.
            t = e.target.closest('[data-chat-arc-open]');
            if (t) { openArcade(t.getAttribute('data-chat-arc-open')); return; }
            t = e.target.closest('[data-chat-arc-flip]');
            if (t) {
                if (state.arcade) { state.arcade.flip = !state.arcade.flip; renderArcade(); }
                return;
            }
            t = e.target.closest('[data-chat-arc-promo]');
            if (t) {
                var pick = t.getAttribute('data-chat-arc-promo');
                if (!state.arcade) return;
                var pg = state.arcade.promo;
                var pgame = pg && state.arcade.game ? _arcGame(state.arcade.game) : null;
                state.arcade.promo = null;
                state.arcade.sel = -1;
                if (pick && pg && pgame) {
                    var puci = window.ChessEngine.toAlg(pg.from) +
                               window.ChessEngine.toAlg(pg.to) + pick;
                    if (_arcCanVote(pgame)) arcVote(pgame.id, puci);
                    else arcMove(pgame.id, puci);
                } else {
                    renderArcade();
                }
                return;
            }
            t = e.target.closest('[data-chat-arc-sq]');
            if (t) { _arcSquareClick(parseInt(t.getAttribute('data-chat-arc-sq'), 10)); return; }
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
                // the filter also picks the SEND format, so a half-built rich
                // message cannot survive the flip to plain
                if (_plainOn()) { cancelReply(); cancelEdit(); toggleAttachPanel(true); closeWantedModal(); }
                renderHead(); renderComposer(); refresh();
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
            t = e.target.closest('[data-chat-hide-user]');
            if (t) {
                sendProtocol('mod.hide', { u: t.getAttribute('data-chat-hide-user'),
                                           ts: t.getAttribute('data-chat-hide-ts') });
                if (typeof showToast === 'function') showToast('🚫 Hidden for the room', 'success');
                return;
            }
            t = e.target.closest('[data-chat-unhide-user]');
            if (t) {
                sendProtocol('mod.unhide', { u: t.getAttribute('data-chat-unhide-user'),
                                             ts: t.getAttribute('data-chat-unhide-ts') });
                return;
            }
            t = e.target.closest('[data-chat-hidden-reveal]');
            if (t && !e.target.closest('[data-chat-unhide-user]')) {
                state.revealedHidden = state.revealedHidden || {};
                state.revealedHidden[t.getAttribute('data-chat-hidden-reveal')] = true;
                renderMessages(state.msgs || []);
                return;
            }
            t = e.target.closest('[data-chat-triv-send]');
            if (t) { _trivGuess(); return; }
            t = e.target.closest('[data-chat-triv-end]');
            if (t) {
                var tEnd = _trivState();
                if (tEnd && !tEnd.closed) {
                    sendProtocol('trv.end', { id: tEnd.id, ans: _trivAnsStore(tEnd.id) });
                }
                return;
            }
            t = e.target.closest('[data-chat-triv-dismiss]');
            if (t) {
                var tDis = _trivState();
                state.trivDismissedAt = tDis ? tDis.at : null;
                renderTrivia();
                return;
            }
            t = e.target.closest('[data-chat-triv-open]');
            if (t) {
                var trivOv = q('[data-chat-triv-modal]');
                if (trivOv) { trivOv.hidden = false; var tq = q('[data-chat-triv-q]'); if (tq) tq.focus(); }
                return;
            }
            t = e.target.closest('[data-chat-triv-close]');
            if (t) { var trivOv2 = q('[data-chat-triv-modal]'); if (trivOv2) trivOv2.hidden = true; return; }
            var trivOvBg = e.target.closest('[data-chat-triv-modal]');
            if (trivOvBg && e.target === trivOvBg) { trivOvBg.hidden = true; return; }
            t = e.target.closest('[data-chat-triv-start]');
            if (t) { _trivAsk(); return; }
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
            // ── Now Playing & Wanted action buttons ──
            t = e.target.closest('[data-chat-np-btn]') || e.target.closest('[data-chat-np-quick]');
            if (t) { shareNowPlaying(); return; }
            t = e.target.closest('[data-chat-want-btn]');
            if (t) { openWantedModal(); return; }
            t = e.target.closest('[data-chat-want-close]');
            if (t) { closeWantedModal(); return; }
            var wantOv = e.target.closest('[data-chat-want-backdrop]');
            if (wantOv) { closeWantedModal(); return; }
            t = e.target.closest('[data-want-src]');
            if (t) {
                document.querySelectorAll('[data-want-src]').forEach(function (b) {
                    b.classList.remove('chat-want-src-btn--active');
                });
                t.classList.add('chat-want-src-btn--active');
                _wantActiveSource = t.getAttribute('data-want-src') || 'auto';
                searchWanted();
                return;
            }
            t = e.target.closest('[data-chat-want-clear]');
            if (t) {
                var wInp = q('[data-chat-want-input]');
                if (wInp) { wInp.value = ''; wInp.focus(); }
                searchWanted();
                return;
            }
            t = e.target.closest('[data-chat-want-post-idx]');
            if (t) {
                var idx = parseInt(t.getAttribute('data-chat-want-post-idx'), 10);
                var resEl = q('[data-chat-want-results]');
                if (resEl && resEl._items && resEl._items[idx]) {
                    postWantedCard(resEl._items[idx]);
                }
                return;
            }
            t = e.target.closest('[data-chat-card-stream]');
            if (t) {
                var c1 = t.closest('.chat-np-card');
                if (c1) {
                    _streamFromCard(
                        c1.getAttribute('data-np-title') || '',
                        c1.getAttribute('data-np-artist') || '',
                        c1.getAttribute('data-np-album') || ''
                    );
                }
                return;
            }
            t = e.target.closest('[data-chat-card-dl]');
            if (t) {
                var c2 = t.closest('.chat-np-card') || t.closest('.chat-wanted-card');
                if (c2) {
                    _openDownloadForCard(
                        c2.getAttribute('data-np-artist') || c2.getAttribute('data-wanted-artist') || '',
                        c2.getAttribute('data-np-title') || c2.getAttribute('data-wanted-title') || '',
                        c2.getAttribute('data-np-album') || c2.getAttribute('data-wanted-album') || '',
                        c2.getAttribute('data-np-id') || c2.getAttribute('data-wanted-id') || '',
                        c2.getAttribute('data-np-src') || c2.getAttribute('data-wanted-src') || '',
                        parseInt(c2.getAttribute('data-np-dur') || c2.getAttribute('data-wanted-dur') || '0', 10),
                        c2.getAttribute('data-np-img') || c2.getAttribute('data-wanted-img') || '',
                        c2.getAttribute('data-wanted-type') || (c2.classList.contains('chat-wanted-card') ? 'album' : 'single')
                    );
                }
                return;
            }
            t = e.target.closest('[data-chat-card-artist]');
            if (t) {
                var c3 = t.closest('.chat-np-card') || t.closest('.chat-wanted-card');
                if (c3) {
                    _openArtistPage(
                        c3.getAttribute('data-np-artist') || c3.getAttribute('data-wanted-artist') || '',
                        c3.getAttribute('data-np-id') || c3.getAttribute('data-wanted-id') || '',
                        c3.getAttribute('data-np-src') || c3.getAttribute('data-wanted-src') || ''
                    );
                }
                return;
            }
            t = e.target.closest('[data-chat-card-search]');
            if (t) {
                var c4 = t.closest('.chat-np-card') || t.closest('.chat-wanted-card');
                if (c4) {
                    _searchFromCard(
                        c4.getAttribute('data-np-artist') || c4.getAttribute('data-wanted-artist') || '',
                        c4.getAttribute('data-np-title') || c4.getAttribute('data-wanted-title') || ''
                    );
                }
                return;
            }
            t = e.target.closest('[data-chat-wanted-have]');
            if (t) {
                _onHaveWanted(
                    t.getAttribute('data-wanted-user') || '',
                    t.getAttribute('data-wanted-title') || '',
                    t.getAttribute('data-wanted-artist') || ''
                );
                return;
            }
            t = e.target.closest('[data-chat-wanted-wishlist]') || e.target.closest('[data-chat-card-wishlist]');
            if (t) {
                var c5 = t.closest('.chat-wanted-card') || t.closest('.chat-np-card');
                if (c5) {
                    var isWanted = c5.classList.contains('chat-wanted-card');
                    _addWantedToWishlist({
                        t: c5.getAttribute(isWanted ? 'data-wanted-title' : 'data-np-title') || '',
                        a: c5.getAttribute(isWanted ? 'data-wanted-artist' : 'data-np-artist') || '',
                        al: c5.getAttribute(isWanted ? 'data-wanted-album' : 'data-np-album') || '',
                        id: c5.getAttribute(isWanted ? 'data-wanted-id' : 'data-np-id') || '',
                        src: c5.getAttribute(isWanted ? 'data-wanted-src' : 'data-np-src') || '',
                        img: c5.getAttribute(isWanted ? 'data-wanted-img' : 'data-np-img') || '',
                        ty: isWanted ? (c5.getAttribute('data-wanted-type') || 'album') : 'track',
                        dur: c5.getAttribute(isWanted ? 'data-wanted-dur' : 'data-np-dur') || 0,
                        y: c5.getAttribute(isWanted ? 'data-wanted-year' : 'data-np-year') || ''
                    });
                }
                return;
            }
            // ── movie night ──
            t = e.target.closest('[data-chat-watch-btn]');
            if (t) { _openWatchModal(); return; }
            t = e.target.closest('[data-chat-watch-searchclose]');
            if (t) { _closeWatchModal(); return; }
            var watchOv = e.target.closest('[data-chat-watch-modal]');
            if (watchOv && e.target === watchOv) { _closeWatchModal(); return; }
            t = e.target.closest('[data-chat-watch-nomshow]');
            if (t) {
                var wsr = state.watch.searchResults[parseInt(t.getAttribute('data-chat-watch-nomshow'), 10)];
                var sEl = q('[data-chat-watch-se-s]'), eEl = q('[data-chat-watch-se-e]');
                var ws = sEl ? parseInt(sEl.value, 10) : NaN;
                var we = eEl ? parseInt(eEl.value, 10) : NaN;
                if (wsr && ws >= 0 && we >= 0) _watchNominate(wsr, ws, we);
                return;
            }
            // "Start now" — nominate AND start in one gesture. Checked BEFORE
            // the card's own nominate handler, which would otherwise swallow
            // the click (the button lives inside the card).
            t = e.target.closest('[data-chat-watch-now]');
            if (t) {
                var nr = state.watch.searchResults[parseInt(t.getAttribute('data-chat-watch-now'), 10)];
                if (nr) _watchNominate(nr, null, null, true);
                return;
            }
            t = e.target.closest('[data-chat-watch-nowshow]');
            if (t) {
                var nsr = state.watch.searchResults[parseInt(t.getAttribute('data-chat-watch-nowshow'), 10)];
                var nsS = q('[data-chat-watch-se-s]'), nsE = q('[data-chat-watch-se-e]');
                var ns = nsS ? parseInt(nsS.value, 10) : -1, ne = nsE ? parseInt(nsE.value, 10) : -1;
                if (nsr && ns >= 0 && ne >= 0) _watchNominate(nsr, ns, ne, true);
                return;
            }
            t = e.target.closest('[data-chat-watch-nom]');
            if (t && !e.target.closest('.chat-watch-sepick')) {
                var wi = parseInt(t.getAttribute('data-chat-watch-nom'), 10);
                var wr = state.watch.searchResults[wi];
                if (!wr) return;
                if (wr.kind === 'show') {
                    // an episode nomination needs S+E — expand the card
                    state.watch.pickShow = (state.watch.pickShow === wi) ? -1 : wi;
                    var wgrid = q('[data-chat-watch-searchgrid]');
                    if (wgrid) wgrid.innerHTML = _watchResultCards();
                    var wse = q('[data-chat-watch-se-s]');
                    if (wse) wse.focus();
                } else {
                    _watchNominate(wr, null, null);
                }
                return;
            }
            t = e.target.closest('[data-chat-watch-vote]');
            if (t) { sendProtocol('watch.vote', { o: t.getAttribute('data-chat-watch-vote') }); return; }
            t = e.target.closest('[data-chat-watch-start]');
            if (t) {
                // Pressing ▶ IS the opt-in gesture — making the person who
                // started the showing hunt for a second button to watch their
                // own pick was the wrong reading of "playback is opt-in".
                state.watch.autoJoin = t.getAttribute('data-chat-watch-start');
                sendProtocol('watch.start', { o: state.watch.autoJoin });
                return;
            }
            t = e.target.closest('[data-chat-watch-unnom]');
            if (t) { sendProtocol('watch.unnom', { o: t.getAttribute('data-chat-watch-unnom') }); return; }
            t = e.target.closest('[data-chat-watch-grab]');
            if (t) { _watchGrab(t.getAttribute('data-chat-watch-grab')); return; }
            t = e.target.closest('[data-chat-watch-join]');
            if (t) { _watchJoin(); return; }
            t = e.target.closest('[data-chat-watch-leave]');
            if (t) { _watchTeardown(); renderWatch(); return; }
            t = e.target.closest('[data-chat-watch-pause]');
            if (t) { sendProtocol('watch.pause', {}); return; }
            t = e.target.closest('[data-chat-watch-resume]');
            if (t) { sendProtocol('watch.resume', {}); return; }
            t = e.target.closest('[data-chat-watch-end]');
            if (t) {
                var wst = _watchState();
                if (wst.now && wst.now.by !== state.selfName && _selfIsMod() &&
                        typeof showConfirmDialog === 'function') {
                    // a moderator ending someone ELSE's party deserves a beat
                    showConfirmDialog({
                        title: 'End the party?',
                        message: 'This ends ' + wst.now.by + '\'s showing for the whole room.',
                        confirmText: 'End it',
                        destructive: true,
                    }).then(function (okd) { if (okd !== false) sendProtocol('watch.end', {}); });
                } else {
                    sendProtocol('watch.end', {});
                }
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
            t = e.target.closest('[data-chat-close-pm]');
            if (t) {
                var userToClose = t.getAttribute('data-chat-close-pm');
                if (userToClose) {
                    hideDm(userToClose);
                    if (state.view === 'pm' && state.pmUser === userToClose) {
                        openRoom(state.room || undefined);
                    }
                    if (state.convos) renderSide(state.convos);
                    if (typeof showToast === 'function') showToast('Conversation closed', 'info');
                }
                return;
            }
            t = e.target.closest('[data-chat-head-close-pm]');
            if (t) {
                if (state.view === 'pm' && state.pmUser) {
                    var uClosed = state.pmUser;
                    hideDm(uClosed);
                    openRoom(state.room || undefined);
                    if (state.convos) renderSide(state.convos);
                    if (typeof showToast === 'function') showToast('Conversation closed', 'info');
                }
                return;
            }
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
            t = e.target.closest('[data-chat-card-friend]');
            if (t) {
                var fCard = q('[data-chat-user-card]');
                var uFor = fCard && fCard.getAttribute('data-chat-user-card-for');
                if (uFor) {
                    var isNowF = toggleFriend(uFor);
                    t.textContent = isNowF ? '⭐ Remove Friend' : '⭐ Add Friend';
                    if (typeof showToast === 'function') {
                        showToast(isNowF ? 'Added ' + uFor + ' to friends' : 'Removed from friends', 'info');
                    }
                }
                return;
            }
            t = e.target.closest('[data-chat-card-block]');
            if (t) {
                var bCard = q('[data-chat-user-card]');
                var uToBlk = bCard && bCard.getAttribute('data-chat-user-card-for');
                if (uToBlk) {
                    var isNowBlk = toggleBlocked(uToBlk);
                    t.textContent = isNowBlk ? 'Unblock' : '🚫 Block';
                    if (typeof showToast === 'function') {
                        showToast(isNowBlk ? 'Blocked ' + uToBlk : 'Unblocked ' + uToBlk, 'info');
                    }
                }
                return;
            }
            t = e.target.closest('[data-chat-card-bookmark]');
            if (t) {
                var bmCard = q('[data-chat-user-card]');
                var uToBm = bmCard && bmCard.getAttribute('data-chat-user-card-for');
                if (uToBm) {
                    var isNowBm = togglePeerBookmark(uToBm);
                    t.textContent = isNowBm ? '★ Bookmarked' : '⭐ Bookmark';
                    _updateSavedPeersBtn();
                    if (typeof showToast === 'function') {
                        showToast(isNowBm ? 'Bookmarked ' + uToBm : 'Bookmark removed', 'info');
                    }
                }
                return;
            }
            t = e.target.closest('[data-chat-card-challenge-v]');
            if (t) {
                var vOv = q('[data-chat-user-card]');
                var vOpp = vOv && vOv.getAttribute('data-chat-user-card-for');
                if (vOpp) {
                    arcNewGame('w', vOpp, t.getAttribute('data-chat-card-challenge-v'), false);
                    closeUserCard();
                    if (typeof showToast === 'function') {
                        showToast('Challenge sent — ' + vOpp + ' is the only one who can join', 'success');
                    }
                }
                return;
            }
            t = e.target.closest('[data-chat-card-challenge]');
            if (t) {
                var cOv = q('[data-chat-user-card]');
                if (cOv) _arcChallengeRow(cOv);
                return;
            }
            t = e.target.closest('[data-chat-browse-user]');
            if (t) {
                openBrowse(t.getAttribute('data-chat-browse-user'));
                return;
            }
            t = e.target.closest('[data-chat-card-browse]');
            if (t) {
                var bOv = q('[data-chat-user-card]');
                openBrowse(bOv && bOv.getAttribute('data-chat-user-card-for'));
                return;
            }
            t = e.target.closest('[data-chat-peer-bookmark]');
            if (t) {
                if (_browse.user) {
                    var isBm2 = togglePeerBookmark(_browse.user);
                    t.textContent = isBm2 ? '★ Bookmarked' : '⭐ Bookmark';
                    t.classList.toggle('chat-peer-bm-btn--active', isBm2);
                    _updateSavedPeersBtn();
                    if (typeof showToast === 'function') {
                        showToast(isBm2 ? 'Peer bookmarked' : 'Bookmark removed', 'info');
                    }
                }
                return;
            }
            t = e.target.closest('[data-chat-open-social]');
            if (t) {
                var tabChoice = t.getAttribute('data-chat-open-social') || 'friends';
                openSocialModal(tabChoice);
                return;
            }
            t = e.target.closest('[data-chat-social-close]');
            if (t) { closeSocialModal(); return; }
            t = e.target.closest('[data-chat-social-backdrop]');
            if (t) { closeSocialModal(); return; }
            t = e.target.closest('[data-chat-social-tab]');
            if (t) {
                _socialTab = t.getAttribute('data-chat-social-tab') || 'friends';
                renderSocialList();
                return;
            }
            t = e.target.closest('[data-chat-social-add-btn]');
            if (t) {
                addSocialUser();
                return;
            }
            t = e.target.closest('[data-chat-social-remove-friend]');
            if (t) {
                var rf = t.getAttribute('data-chat-social-remove-friend');
                if (rf) {
                    toggleFriend(rf);
                    renderSocialList();
                    if (typeof showToast === 'function') showToast('Removed ' + rf + ' from friends', 'info');
                }
                return;
            }
            t = e.target.closest('[data-chat-social-unblock]');
            if (t) {
                var ub = t.getAttribute('data-chat-social-unblock');
                if (ub) {
                    toggleBlocked(ub);
                    renderSocialList();
                    if (typeof showToast === 'function') showToast('Unblocked ' + ub, 'info');
                }
                return;
            }
            t = e.target.closest('[data-chat-social-remove-bookmark]');
            if (t) {
                var rb = t.getAttribute('data-chat-social-remove-bookmark');
                if (rb) {
                    togglePeerBookmark(rb);
                    renderSocialList();
                    if (typeof showToast === 'function') showToast('Removed bookmark for ' + rb, 'info');
                }
                return;
            }
            t = e.target.closest('[data-chat-browse-saved-peers]');
            if (t) {
                _openSavedPeersPicker();
                return;
            }
            t = e.target.closest('[data-chat-saved-peer-remove]');
            if (t) {
                var pRem = t.getAttribute('data-chat-saved-peer-remove');
                if (pRem) {
                    togglePeerBookmark(pRem);
                    _updateSavedPeersBtn();
                    var pRow = t.closest('.chat-room-row');
                    if (pRow) pRow.remove();
                    if (typeof showToast === 'function') showToast('Bookmark removed', 'info');
                }
                return;
            }
            t = e.target.closest('[data-chat-browse-friend]');
            if (t) {
                if (_browse.user) {
                    var isFrNow = toggleFriend(_browse.user);
                    t.textContent = isFrNow ? '⭐ Friend (Remove)' : '⭐ Friend';
                    if (typeof showToast === 'function') {
                        showToast(isFrNow ? 'Added ' + _browse.user + ' to friends' : 'Removed from friends', 'info');
                    }
                }
                return;
            }
            t = e.target.closest('[data-chat-browse-block]');
            if (t) {
                if (_browse.user) {
                    var isBlkNow = toggleBlocked(_browse.user);
                    t.textContent = isBlkNow ? 'Unblock' : '🚫 Block';
                    if (typeof showToast === 'function') {
                        showToast(isBlkNow ? 'Blocked ' + _browse.user : 'Unblocked ' + _browse.user, 'info');
                    }
                }
                return;
            }
            t = e.target.closest('[data-chat-browse-fmt]');
            if (t) {
                var fmtChoice = t.getAttribute('data-chat-browse-fmt') || 'all';
                _browse.formatFilter = fmtChoice;
                var pBarEl = q('.chat-browse-format-filters');
                if (pBarEl) {
                    pBarEl.querySelectorAll('.chat-fmt-pill').forEach(function (pill) {
                        pill.classList.toggle('chat-fmt-pill--active', pill === t);
                    });
                }
                renderBrowseTree(_browse.filter);
                return;
            }
            t = e.target.closest('[data-chat-browse-dl-single]');
            if (t) {
                downloadSingleTrack(t.getAttribute('data-chat-browse-dl-single'), t.getAttribute('data-chat-browse-size'));
                return;
            }
            t = e.target.closest('[data-chat-browse-dl-folder]');
            if (t) {
                downloadFolder(t.getAttribute('data-chat-browse-dl-folder'));
                return;
            }
            t = e.target.closest('[data-chat-browse-toggle]');
            if (t) {
                var folderItem = t.closest('.chat-folder-tree-item');
                var dir = t.getAttribute('data-chat-browse-toggle') || (folderItem && folderItem.getAttribute('data-chat-folder-item'));
                toggleBrowseFolder(dir, folderItem);
                return;
            }
            t = e.target.closest('[data-chat-browse-expand-all]');
            if (t) { expandAllBrowseFolders(); return; }
            t = e.target.closest('[data-chat-browse-collapse-all]');
            if (t) { collapseAllBrowseFolders(); return; }
            t = e.target.closest('[data-chat-dock-clear]');
            if (t) {
                _browse.selected = {};
                var bBody = q('[data-chat-browse-body]');
                if (bBody) {
                    bBody.querySelectorAll('[data-chat-browse-file], [data-chat-browse-folder-all]').forEach(function (cb) {
                        cb.checked = false;
                    });
                    bBody.querySelectorAll('.chat-file-row--selected').forEach(function (r) {
                        r.classList.remove('chat-file-row--selected');
                    });
                }
                _syncBrowseDock();
                return;
            }
            t = e.target.closest('[data-chat-browse-msg]');
            if (t) {
                var peerName = _browse.user;
                closeBrowse();
                if (peerName) openPm(peerName);
                return;
            }
            t = e.target.closest('[data-chat-browse-challenge]');
            if (t) {
                var peerName2 = _browse.user;
                closeBrowse();
                if (peerName2) openUserCard(peerName2);
                return;
            }
            t = e.target.closest('[data-chat-browse-dl]');
            if (t) { browseDownloadSelected(); return; }
            t = e.target.closest('[data-chat-browse-close]');
            if (t) { closeBrowse(); return; }
            t = e.target.closest('[data-chat-browse-backdrop]');
            if (t) { closeBrowse(); return; }
            var bmo = e.target.closest('[data-chat-browse-modal]');
            if (bmo && e.target === bmo) { closeBrowse(); return; }
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
        var watchSearchForm = q('[data-chat-watch-searchform]');
        if (watchSearchForm) watchSearchForm.addEventListener('submit', function (e) { e.preventDefault(); _watchSearchSubmit(); });
        var watchSearchIn = q('[data-chat-watch-searchinput]');
        if (watchSearchIn) watchSearchIn.addEventListener('input', _watchQueueSearch);
        var socFilterIn = q('[data-chat-social-filter]');
        if (socFilterIn) {
            socFilterIn.addEventListener('input', function (e) {
                _socialFilter = e.target.value;
                renderSocialList();
            });
        }
        // The trivia answer input is re-rendered with its card, so Enter is
        // caught by delegation on the page rather than a per-render listener.
        var chatPageEl = document.getElementById('chat-page');
        if (chatPageEl) {
            chatPageEl.addEventListener('keydown', function (e) {
                if (e.key === 'Enter' && e.target && e.target.matches) {
                    if (e.target.matches('[data-chat-triv-guess]')) {
                        e.preventDefault();
                        _trivGuess();
                    } else if (e.target.matches('[data-chat-social-input]')) {
                        e.preventDefault();
                        addSocialUser();
                    }
                }
            });
        }

        document.addEventListener('change', function (e) {
            if (e.target && e.target.matches('[data-chat-browse-file]')) {
                var fn = e.target.getAttribute('data-chat-browse-file');
                var sz = Number(e.target.getAttribute('data-chat-browse-size')) || 0;
                var row = e.target.closest('.chat-file-row');
                if (e.target.checked) {
                    _browse.selected[fn] = { size: sz };
                    if (row) row.classList.add('chat-file-row--selected');
                } else {
                    delete _browse.selected[fn];
                    if (row) row.classList.remove('chat-file-row--selected');
                }
                _syncBrowseDock();
            } else if (e.target && e.target.matches('[data-chat-browse-folder-all]')) {
                var dir = e.target.getAttribute('data-chat-browse-folder-all');
                var files = _browse.cache[dir] || [];
                var chk = e.target.checked;
                var container = e.target.closest('.chat-folder-children');
                files.forEach(function (x) {
                    if (chk) {
                        _browse.selected[x.filename] = { size: x.size || 0 };
                    } else {
                        delete _browse.selected[x.filename];
                    }
                });
                if (container) {
                    container.querySelectorAll('[data-chat-browse-file]').forEach(function (cb) {
                        cb.checked = chk;
                        var r = cb.closest('.chat-file-row');
                        if (r) {
                            if (chk) r.classList.add('chat-file-row--selected');
                            else r.classList.remove('chat-file-row--selected');
                        }
                    });
                }
                _syncBrowseDock();
            }
        });

        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') {
                var bm = q('[data-chat-browse-modal]');
                if (bm && !bm.hidden) { closeBrowse(); }
                var socD = q('[data-chat-social-drawer]');
                if (socD && !socD.hidden) { closeSocialModal(); }
                var wantD = q('[data-chat-want-modal]');
                if (wantD && !wantD.hidden) { closeWantedModal(); }
            }
        });

        var wantInputEl = q('[data-chat-want-input]');
        if (wantInputEl) {
            wantInputEl.addEventListener('input', function () {
                clearTimeout(_wantSearchTimer);
                _wantSearchTimer = setTimeout(searchWanted, 350);
            });
            wantInputEl.addEventListener('keydown', function (e) {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    clearTimeout(_wantSearchTimer);
                    searchWanted();
                }
            });
        }
        _bindChatDragAndDrop();

        window.addEventListener('popstate', function () {
            var bm = q('[data-chat-browse-modal]');
            if (bm && !bm.hidden && (!window.location.hash || window.location.hash.indexOf('#browse') !== 0)) {
                closeBrowse();
            }
        });

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
            if (e.target && e.target.matches('[data-chat-watch-vol]')) {
                // Mirrors the jukebox's: local only, persisted, never on the bus.
                var wv = parseInt(e.target.value, 10);
                if (wv >= 0 && wv <= 100) {
                    state.watch.vol = wv;
                    try { localStorage.setItem('chat_watch_vol', String(wv)); } catch (err) { /* ignore */ }
                    var wvid = q('[data-chat-watch-video]');
                    if (wvid) wvid.volume = wv / 100;
                }
                return;
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
                renderBrowseTree(e.target.value.trim());
            }
        });

        document.addEventListener('change', function (e) {
            var cb = e.target.closest('[data-chat-browse-file]');
            if (cb) {
                var fn = cb.getAttribute('data-chat-browse-file');
                var sz = Number(cb.getAttribute('data-chat-browse-size')) || 0;
                var row = cb.closest('.chat-file-row');
                if (cb.checked) {
                    _browse.selected[fn] = { filename: fn, size: sz };
                    if (row) row.classList.add('chat-file-row--selected');
                } else {
                    delete _browse.selected[fn];
                    if (row) row.classList.remove('chat-file-row--selected');
                }
                var dir = cb.getAttribute('data-chat-browse-dir');
                if (dir) {
                    var folderAll = q('[data-chat-browse-folder-all="' + attr(dir) + '"]');
                    if (folderAll) {
                        var cached = _browse.cache[dir] || [];
                        folderAll.checked = cached.length > 0 && cached.every(function (x) { return !!_browse.selected[x.filename]; });
                    }
                }
                _syncBrowseDock();
                return;
            }

            var fAll = e.target.closest('[data-chat-browse-folder-all]');
            if (fAll) {
                var dirName = fAll.getAttribute('data-chat-browse-folder-all');
                var files = _browse.cache[dirName] || [];
                var checked = fAll.checked;
                files.forEach(function (x) {
                    if (checked) {
                        _browse.selected[x.filename] = { filename: x.filename, size: x.size };
                    } else {
                        delete _browse.selected[x.filename];
                    }
                });
                var chEl = q('[data-chat-browse-children="' + attr(dirName) + '"]');
                if (chEl) {
                    chEl.querySelectorAll('[data-chat-browse-file]').forEach(function (c) {
                        c.checked = checked;
                        var r = c.closest('.chat-file-row');
                        if (r) {
                            if (checked) r.classList.add('chat-file-row--selected');
                            else r.classList.remove('chat-file-row--selected');
                        }
                    });
                }
                _syncBrowseDock();
                return;
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
        _updateSocialBadges();
        if (state.configured !== true) {
            getJSON('/api/chat/status').then(function (res) {
                state.configured = !!(res.ok && res.body.configured);
                state.homeRoom = (res.body && res.body.room) || 'SoulSync';
                state.room = state.room || state.homeRoom;
                state.connectionError = res.body && res.body.connected === false ? res.body.error || 'Soulseek is not connected' : null;
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

    // Bound the log without eating live games. The old flat slice(-300)
    // treated a chess move and a typing blip as equals, so a chatty room
    // (typ/hello/jukebox events) evicted a game's gm.new and early moves
    // MID-SESSION — the refold then degraded the game to 'partial', dropped
    // inferred seats and reset the board to the last checkpoint while you
    // were playing it. Ephemeral chatter keeps the old bound; game carriers
    // get a deeper one (two full-length games' worth). Order is preserved —
    // the fold depends on stream order.
    function _trimProtocolLog(log) {
        var keptGm = 0, keptOther = 0;
        var out = [];
        for (var i = log.length - 1; i >= 0; i--) {
            var e = log[i];
            var k = e && e.p && e.p.k;
            if (typeof k === 'string' && k.slice(0, 3) === 'gm.') {
                if (keptGm < 1200) { out.push(e); keptGm++; }
            } else if (keptOther < 300) {
                out.push(e); keptOther++;
            }
        }
        out.reverse();
        return out;
    }

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
        if (log.length > 300) state.protocolLog = _trimProtocolLog(log);
        if (fresh.length) {
            // presence: a protocol event proves SoulSync — refresh buckets
            renderUsersList();
            renderBusUI();
            _arcAnswerSyncs(fresh);
            _arcNoticeChallenges(fresh);
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
        // Suppressed in Plain Mode (All Messages view) and when the user
        // has no custom avatar to announce, stopping empty noise lines.
        if (!state.canSend || !state.room || state.beaconed[state.room]) return;
        if (_plainOn() || !_myAvatar()) return;
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
        if (state.view !== 'room' || !state.canSend || _plainOn()) return;
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
        _arcMaybeSync();         // ask, if a game has gone quiet on us
        _bsTick();               // answer shots at us, and reveal when sunk
        renderArcade();          // no-op unless the Arcade view is open
        renderJukebox();
        renderWatch();
        renderPinbar();
        renderPoll();
        renderTrivia();
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
                        (state.canSend && _selfIsMod()
                            ? '<button class="chat-pin-del" type="button" title="Unpin" ' +
                              'data-chat-pin-del-u="' + attr(pin.u) + '" data-chat-pin-del-ts="' + attr(pin.ts) + '">×</button>'
                            : '') +
                    '</div>';
                }).join('')
                : '<div class="chat-side-none">' +
                    (_selfIsMod() ? 'Nothing pinned yet — hover a message and hit 📌'
                                  : 'Nothing pinned yet') + '</div>');
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

    // ── trivia (trv.ask / trv.guess / trv.end — the stream is the buzzer) ──
    // State is a pure fold (chat-protocol.js reduceTrivia) with ChatHash
    // injected; every client checks every guess against the asker's hash and
    // the FIRST correct one in stream order wins the pot. Fingerprint-gated
    // repaint: the card holds a text input, and a poll-tick innerHTML rewrite
    // would eat whatever you were typing.
    var _trivLastPaint = '';

    function _trivState() {
        var CP = window.ChatProtocol;
        if (!CP || !CP.reduceTrivia) return null;
        var H = window.ChatHash;
        return CP.reduceTrivia(_roomEvents(), H ? H.sha256 : null);
    }

    function _trivAnsStore(id, ans) {
        try {
            var map = JSON.parse(localStorage.getItem('chatTrivAns') || '{}');
            if (ans !== undefined) {
                map[id] = ans;
                var keys = Object.keys(map);
                while (keys.length > 20) { delete map[keys.shift()]; }
                localStorage.setItem('chatTrivAns', JSON.stringify(map));
            }
            return map[id] || '';
        } catch (e) { return ''; }
    }

    function _settleTrivia(t) {
        // Same client-local idempotent booking as the wagers: winner books
        // +pot, asker books -pot, each against their OWN play-money bank,
        // marked settled BEFORE the POST so a retry can never double-pay.
        if (!t || !t.winner || !t.pot || !state.selfName) return;
        if (state.selfName !== t.winner && state.selfName !== t.by) return;
        var key = t.id;
        try {
            var done = JSON.parse(localStorage.getItem('chatTrivSettled') || '{}');
            if (done[key]) return;
            done[key] = 1;
            var ks = Object.keys(done);
            while (ks.length > 100) { delete done[ks.shift()]; }
            localStorage.setItem('chatTrivSettled', JSON.stringify(done));
        } catch (e) { return; }
        var delta = state.selfName === t.winner ? t.pot : -t.pot;
        postJSON('/api/chat/arcade/bank', { delta: delta });
    }

    function renderTrivia() {
        var host = q('[data-chat-triv]');
        if (!host) return;
        var t = _trivState();
        // Settlement is independent of what you're LOOKING at — a pot won
        // while you sat in a PM still books when the fold next runs.
        _settleTrivia(t);
        var show = !!(t && state.view === 'room' &&
                      !(t.closed && state.trivDismissedAt === t.at));
        if (!show) {
            host.hidden = true;
            if (host.innerHTML) host.innerHTML = '';
            _trivLastPaint = '';
            return;
        }
        var paint = [t.id, t.at, t.guesses.length, t.closed ? 1 : 0, t.winner,
                     state.canSend ? 1 : 0].join('|');
        if (paint === _trivLastPaint && !host.hidden) return;   // keep the input alive
        _trivLastPaint = paint;
        host.hidden = false;
        var mine = t.by === state.selfName;
        var tail = t.guesses.slice(-4).map(function (g) {
            return '<span class="chat-triv-guess' + (g.ok ? ' chat-triv-guess--win' : '') + '">' +
                esc(g.u) + ': ' + esc(g.a) + (g.ok ? ' ✓' : ' ✗') + '</span>';
        }).join('');
        var footer;
        if (t.winner) {
            footer = '<div class="chat-triv-winline">🏆 <b>' + esc(t.winner) + '</b> takes ' +
                (t.pot ? 'the 🪙' + t.pot + ' pot' : 'it') + ' — “' + esc(t.winAnswer) + '”</div>';
        } else if (t.closed) {
            footer = '<div class="chat-triv-winline">Nobody got it' +
                (t.answer ? ' — the answer was “' + esc(t.answer) + '”' +
                    (t.verified ? '' : ' <span class="chat-jbx-meta">(unverified)</span>') : '') +
                '.</div>';
        } else if (state.canSend && !mine) {
            footer = '<div class="chat-triv-row">' +
                '<input class="chat-input chat-triv-in" data-chat-triv-guess type="text" ' +
                    'maxlength="120" placeholder="Your answer…" autocomplete="off">' +
                '<button class="chat-send-btn" type="button" data-chat-triv-send>Answer</button>' +
            '</div>';
        } else {
            footer = '<div class="chat-jbx-meta">' +
                (mine ? 'Your question — you can\'t win your own pot.'
                      : 'Watching only — sending is admin-only here.') + '</div>';
        }
        host.innerHTML =
            '<div class="chat-poll-head">🎓 <b>' + esc(t.q) + '</b>' +
                '<span class="chat-jbx-meta">' +
                    (t.pot ? '🪙' + t.pot + ' pot · ' : '') + 'asked by ' + esc(t.by) + '</span>' +
                (!t.closed && state.canSend && (mine || _selfIsMod())
                    ? '<button class="chat-fmt-btn" type="button" data-chat-triv-end>End &amp; reveal</button>'
                    : '') +
                (t.closed
                    ? '<button class="chat-pin-del" type="button" title="Dismiss" data-chat-triv-dismiss>×</button>'
                    : '') +
            '</div>' +
            (tail ? '<div class="chat-triv-tail">' + tail + '</div>' : '') +
            footer;
    }

    function _trivGuess() {
        var t = _trivState();
        var inp = q('[data-chat-triv-guess]');
        if (!t || t.closed || !inp) return;
        var a = String(inp.value || '').trim();
        if (!a) return;
        inp.value = '';
        sendProtocol('trv.guess', { id: t.id, a: a.slice(0, 120) });
    }

    function _trivAsk() {
        var qEl = q('[data-chat-triv-q]'), aEl = q('[data-chat-triv-a]'), pEl = q('[data-chat-triv-pot]');
        var H = window.ChatHash, CP = window.ChatProtocol;
        if (!qEl || !aEl || !H || !CP) return;
        var question = String(qEl.value || '').trim();
        var answer = String(aEl.value || '').trim();
        if (!question || !answer) {
            if (typeof showToast === 'function') showToast('A question and its answer are both required', 'error');
            return;
        }
        var pot = Math.max(0, Math.min(500, parseInt((pEl && pEl.value) || '0', 10) || 0));
        var id = '';
        for (var i = 0; i < 10; i++) id += 'abcdefghijklmnopqrstuvwxyz0123456789'[Math.floor(Math.random() * 36)];
        _trivAnsStore(id, answer);              // for End & reveal later
        sendProtocol('trv.ask', {
            id: id, q: question.slice(0, 200),
            h: H.sha256(CP.normalizeTriviaAnswer(answer)), pot: pot,
        });
        qEl.value = ''; aEl.value = ''; if (pEl) pEl.value = '';
        var ov = q('[data-chat-triv-modal]');
        if (ov) ov.hidden = true;
        state.trivDismissedAt = null;
        if (typeof showToast === 'function') {
            showToast('🎓 Question is live in the room' + (pot ? ' — 🪙' + pot + ' on the line' : ''), 'success');
        }
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

    // ── movie night (watch-together — a pure fold over watch.* carriers) ──
    // The ballot/party state lives on the bus (chat-protocol.js reduceWatch):
    // every client folds the same nominations, votes and party clock.
    // OWNERSHIP is the one personal ingredient — each SoulSync probes its own
    // video library (/api/video/watch/owned) and renders "you have this" or a
    // GRAB into the wishlist pipeline. Phase 1 = ballot + ownership + grab;
    // the in-page video panel is phase 2.
    function _watchState() {
        var CP = window.ChatProtocol;
        return (CP && CP.reduceWatch) ? CP.reduceWatch(_roomEvents())
                                      : { noms: [], now: null, tally: { total: 0 }, history: [] };
    }

    function _watchLabel(e) {
        return esc(e.ti || ('#' + e.id)) +
            (e.y ? ' <span class="chat-jbx-meta">(' + esc(e.y) + ')</span>' : '') +
            (e.kd === 't' ? ' <span class="chat-watch-se">S' + e.s + 'E' + e.e + '</span>' : '');
    }

    function _watchPoster(e, cls) {
        // OUR OWN library art first. The bus only ever carries a TMDB CDN
        // poster (a library row's artwork can be a tokened Plex/Jellyfin URL
        // and must never be broadcast), so an owned title usually arrives with
        // no `po` at all and every card fell back to the 🎬 placeholder. The
        // local proxy path comes from our own ownership probe and is safe.
        var mine = state.watch.art[e.key];
        if (mine) return '<img class="' + cls + '" src="' + attr(mine) + '" alt="" loading="lazy">';
        // po rides the bus (hostile) — render only an https URL, never a path.
        return (e.po && /^https:\/\//.test(e.po))
            ? '<img class="' + cls + '" src="' + attr(e.po) + '" alt="" loading="lazy">'
            : '<div class="' + cls + ' chat-watch-poster--ph">🎬</div>';
    }

    function _watchOwnChip(key, entry) {
        if (state.watch.ownedDenied) return '';
        var own = state.watch.owned[key];
        if (own === true) return '<span class="chat-watch-own">✓ you have this</span>';
        if (own !== false) return '';                      // probe still out
        if (state.watch.grabbed[key]) return '<span class="chat-watch-own chat-watch-own--want">grabbing…</span>';
        return '<button class="chat-arc-btn chat-watch-grab" type="button" ' +
            'data-chat-watch-grab="' + attr(key) + '" ' +
            'title="You don\'t have this — send it to the video wishlist and search now">Grab</button>';
    }

    // ── the room (P2) ────────────────────────────────────────────────────────
    // Phase 1 gave the room a ballot; this is the part that makes it a place you
    // ENTER. Playback is opt-in exactly like the jukebox's Tune in — nothing
    // plays until a real gesture, because a chat page that starts blasting a
    // film when someone else presses ▶ is a hostile page. And a user is in the
    // jukebox OR the party, never both: they are two audio sources competing for
    // the same ears.

    function _watchStreamUrl(now) {
        var u = '/api/video/watch/stream?kd=' + encodeURIComponent(now.kd) +
                '&id=' + encodeURIComponent(now.id);
        if (now.kd === 't') u += '&s=' + now.s + '&e=' + now.e;
        return u;
    }

    function _watchJoinChip(now) {
        if (state.watch.ownedDenied) return '';
        if (!(now.key in state.watch.owned)) {
            // The probe is still out. Saying so matters: an empty action row
            // here is indistinguishable from "this is broken", and the probe
            // is exactly when a user is looking hardest for the play button.
            return '<span class="chat-watch-own chat-watch-own--wait">checking your library…</span>';
        }
        if (state.watch.owned[now.key] !== true) return '';   // no file here, nothing to join
        if (state.watch.joined === now.key) {
            return '<button class="chat-arc-btn" type="button" data-chat-watch-leave ' +
                'title="Stop playing here — the party carries on without you">⏏ Leave</button>';
        }
        // Probe BEFORE the click so the verdict can be on the button itself.
        // Finding out your copy is AC3 after joining is finding out too late.
        if (!(now.key in state.watch.playAhead)) _watchProbePlayable(now, true);
        var ahead = state.watch.playAhead[now.key];
        var bad = ahead && ahead.verdict === 'no';
        return '<button class="chat-arc-btn' + (bad ? '' : ' chat-arc-btn--go') + '" type="button" ' +
            'data-chat-watch-join title="' +
            attr(bad ? (ahead.reasons || []).join(' · ') + ' — you can still try'
                     : 'Play your copy, synced to the room') + '">' +
            (bad ? '▶ Join anyway' : '▶ Join party') + '</button>' +
            (bad ? '<span class="chat-watch-own chat-watch-own--bad" title="' +
                       attr((ahead.reasons || []).join(' · ')) + '">⚠ won\'t play here</span>' : '');
    }

    // The screen is mounted ONCE per showing and then left alone. Re-rendering it
    // with the rest of the card would tear the <video> element down on every room
    // event and restart the film; only a change of showing (or leaving) touches it.
    function _watchMountStage(now) {
        var host = q('[data-chat-watch-stage]');
        if (!host) return;
        var want = (now && state.watch.joined === now.key) ? now.key : '';
        if (host.getAttribute('data-mounted') === want) { _watchStageWarn(); return; }
        host.setAttribute('data-mounted', want);
        host.hidden = !want;
        if (!want) { host.innerHTML = ''; return; }
        host.innerHTML =
            '<video class="chat-watch-video" data-chat-watch-video playsinline controls ' +
                'preload="metadata" src="' + attr(_watchStreamUrl(now)) + '"></video>' +
            // Its own volume, like the jukebox's — persisted, and independent of
            // the room. The browser's native control disappears when the audio
            // track can't decode, which is exactly when you go looking for it.
            '<div class="chat-watch-stage-bar">' +
                '<span class="chat-watch-volic">🔊</span>' +
                '<input class="chat-jbx-vol chat-watch-vol" data-chat-watch-vol type="range" ' +
                    'min="0" max="100" value="' + state.watch.vol + '" title="Volume (just yours)">' +
            '</div>' +
            '<div class="chat-watch-stage-warn" data-chat-watch-warn hidden></div>';
        var v = host.querySelector('[data-chat-watch-video]');
        if (v) {
            v.volume = Math.max(0, Math.min(100, state.watch.vol)) / 100;
            v.addEventListener('error', function () {
                // The browser is the final authority on playability — when it
                // refuses, say so plainly instead of leaving a black rectangle.
                state.watch.err = (state.watch.play && state.watch.play.reasons || []).join(' · ') ||
                    'It may be an unsupported codec or container.';
                _watchStageWarn();
            });
        }
        _watchStageWarn();
    }

    function _watchStageWarn() {
        var el = q('[data-chat-watch-warn]');
        if (!el) return;
        var p = state.watch.play;
        // 'no' MUST be shown. It used to be suppressed — only 'maybe' rendered —
        // which meant the single worst case was the silent one: Austin Powers
        // (1997) is h264 + AC3, so the picture played and the sound never could,
        // and the UI said nothing at all. A verdict nobody sees is not a verdict.
        var text = '';
        if (state.watch.err) {
            text = 'Your browser refused this file. ' + state.watch.err;
        } else if (p && (p.verdict === 'no' || p.verdict === 'maybe') && (p.reasons || []).length) {
            text = (p.verdict === 'no' ? '⚠ ' : '') + p.reasons.join(' · ');
        }
        el.textContent = text;
        el.hidden = !text;
        el.classList.toggle('chat-watch-stage-warn--hard', !!(p && p.verdict === 'no' && !state.watch.err));
    }

    function _watchTeardown() {
        if (state.watch.drift) { clearInterval(state.watch.drift); state.watch.drift = null; }
        state.watch.joined = '';
        state.watch.play = null;
        state.watch.err = '';
        // Take the screen down HERE rather than waiting for the next render: the
        // drift loop can tear down mid-showing (party ended between ticks), and
        // until something re-rendered the film would keep playing to nobody.
        _watchMountStage(null);
    }

    function _watchJoin() {
        var st = _watchState();
        if (!st.now) return;
        // One pair of ears: joining a showing tunes you out of the jukebox.
        if (state.jukebox.tunedIn) { _jbxTuneOut(); renderJukebox(); }
        state.watch.joined = st.now.key;
        state.watch.err = '';
        renderWatch();
        _watchProbePlayable(st.now);
        _watchArm();
    }

    function _watchProbePlayable(now, ahead) {
        var sig = now.key;
        if (ahead) {
            // Pre-join probe: cache under playAhead so the Join button can carry
            // the verdict. Marked immediately so a re-render can't refire it.
            if (sig in state.watch.playAhead) return;
            state.watch.playAhead[sig] = null;
            fetch('/api/video/watch/playable?kd=' + encodeURIComponent(now.kd) +
                  '&id=' + encodeURIComponent(now.id) +
                  (now.kd === 't' ? '&s=' + now.s + '&e=' + now.e : ''))
                .then(function (r) { return r.json(); })
                .then(function (d) { state.watch.playAhead[sig] = d || null; renderWatch(); })
                .catch(function () { delete state.watch.playAhead[sig]; });
            return;
        }
        if (state.watch.playFetching === sig) return;
        state.watch.playFetching = sig;
        var u = '/api/video/watch/playable?kd=' + encodeURIComponent(now.kd) +
                '&id=' + encodeURIComponent(now.id) +
                (now.kd === 't' ? '&s=' + now.s + '&e=' + now.e : '');
        fetch(u).then(function (r) { return r.json(); }).then(function (d) {
            state.watch.playFetching = '';
            if (state.watch.joined !== sig) return;
            state.watch.play = d || null;
            renderWatch();
        }).catch(function () {
            // The probe is an assist. Failing it must never stop the element
            // from trying — the browser is the final authority anyway.
            state.watch.playFetching = '';
        });
    }

    // Re-anchor the element to the fold: the party's position is DERIVED state
    // (started-at + pause/resume on the bus), so the video is corrected toward
    // it rather than the other way round. Nothing about the local player is
    // ever published — a viewer scrubbing their own copy must not move the room.
    function _watchArm() {
        if (state.watch.drift) clearInterval(state.watch.drift);
        state.watch.drift = setInterval(_watchSync, 5000);
        setTimeout(_watchSync, 0);
    }

    function _watchSync() {
        var v = q('[data-chat-watch-video]');
        var st = _watchState();
        if (!v || !st.now || state.watch.joined !== st.now.key) { _watchTeardown(); return; }
        var CP = window.ChatProtocol;
        var want = (CP.watchPosition(st.now, Date.now()) || 0) / 1000;
        if (st.now.paused) {
            if (!v.paused) v.pause();
        } else if (v.paused) {
            var pr = v.play();
            if (pr && pr.catch) pr.catch(function () { /* autoplay policy — controls are there */ });
        }
        // 2s of slack: seeking on every tick would stutter, and nobody notices
        // a second of drift in a film.
        if (isFinite(v.duration) && Math.abs(v.currentTime - want) > 2) {
            try { v.currentTime = Math.min(want, Math.max(0, v.duration - 0.5)); } catch (e) { /* not seekable yet */ }
        }
    }

    function renderWatch() {
        var host = q('[data-chat-watch]');
        if (!host) return;
        var st = (state.view === 'room') ? _watchState() : null;
        var show = !!(st && (st.noms.length || st.now));
        host.hidden = !show;
        if (!show) {
            host.innerHTML = '';
            // The party ended (or we navigated away from the room) — take the
            // screen down with it rather than leaving a film playing to nobody.
            if (state.watch.joined) _watchTeardown();
            _watchMountStage(null);
            return;
        }
        _watchFetchOwned(st);
        // A NEW showing supersedes whatever we joined: the old party is over.
        if (state.watch.joined && (!st.now || st.now.key !== state.watch.joined)) _watchTeardown();

        // Deferred intents, resolved against the FOLD rather than guessed at
        // locally: we never compute a nomination key here (that logic lives in
        // chat-protocol's reducer and must have exactly one home), we just wait
        // for the key we asked for to show up.
        if (state.watch.autoStart) {
            var pending = st.noms.filter(function (n) { return n.key === state.watch.autoStart; })[0];
            if (pending) {
                state.watch.autoJoin = pending.key;
                state.watch.autoStart = '';
                sendProtocol('watch.start', { o: pending.key });
            }
        }
        if (state.watch.autoJoin && st.now && st.now.key === state.watch.autoJoin) {
            state.watch.autoJoin = '';
            // Only if this box can actually play it — otherwise leave the Grab
            // path alone rather than mounting a screen with nothing behind it.
            if (state.watch.owned[st.now.key] === true && state.watch.joined !== st.now.key) {
                _watchJoin();
                return;                       // _watchJoin re-renders
            }
        }
        var can = state.canSend;
        var html = '<div class="chat-watch-headrow">🎬 <b>Movie night</b>' +
            '<span class="chat-jbx-meta">' +
                (st.now ? 'showing now' : st.noms.length + ' nominated · ' +
                    st.tally.total + ' vote' + (st.tally.total === 1 ? '' : 's')) + '</span>' +
            (can ? '<button class="chat-fmt-btn chat-watch-nombtn" type="button" ' +
                       'data-chat-watch-btn>+ Nominate</button>' : '') +
        '</div>';

        if (st.now) {
            var CP = window.ChatProtocol;
            var pos = CP.watchPosition(st.now, Date.now());
            var mine = st.now.by === state.selfName;
            var boss = can && (mine || _selfIsMod());
            html += '<div class="chat-watch-now">' +
                _watchPoster(st.now, 'chat-watch-poster') +
                '<div class="chat-watch-now-main">' +
                    '<div class="chat-watch-now-ti">' + _watchLabel(st.now) + '</div>' +
                    '<div class="chat-jbx-meta">' +
                        (st.now.paused ? '⏸ paused at ' : '🔴 live · ') +
                        _fmtSecs(Math.floor((pos || 0) / 1000)) +
                        ' · started by ' + esc(st.now.by) + '</div>' +
                    '<div class="chat-watch-now-acts">' +
                        (boss ? '<button class="chat-arc-btn" type="button" data-chat-watch-' +
                                (st.now.paused ? 'resume">▶ Resume' : 'pause">⏸ Pause') + '</button>' +
                                '<button class="chat-arc-btn" type="button" data-chat-watch-end>⏹ End</button>'
                              : '') +
                        _watchOwnChip(st.now.key, st.now) +
                        _watchJoinChip(st.now) +
                    '</div>' +
                '</div>' +
            '</div>';
        }

        html += st.noms.map(function (n) {
            var canPull = can && (n.by === state.selfName || _selfIsMod());
            return '<div class="chat-watch-row">' +
                _watchPoster(n, 'chat-watch-thumb') +
                '<div class="chat-watch-row-main">' +
                    '<div class="chat-watch-row-ti">' + _watchLabel(n) + '</div>' +
                    '<div class="chat-jbx-meta">by ' + esc(n.by) +
                        (n.votes ? ' · ' + n.votes + ' vote' + (n.votes === 1 ? '' : 's') : '') +
                    '</div>' +
                '</div>' +
                // The controls get their own wrapper so they can drop to a
                // second line in a narrow rail. Unwrapped, they were flex
                // siblings that shrank below their own text and collided.
                '<div class="chat-watch-row-acts">' +
                    _watchOwnChip(n.key, n) +
                    (can ? '<button class="chat-arc-btn" type="button" title="Vote for this one" ' +
                               'data-chat-watch-vote="' + attr(n.key) + '">👍' +
                               (n.votes ? ' ' + n.votes : '') + '</button>' +
                           '<button class="chat-arc-btn chat-arc-btn--go" type="button" ' +
                               'title="Start the party with this" ' +
                               'data-chat-watch-start="' + attr(n.key) + '">▶ Play</button>'
                         : (n.votes ? '<span class="chat-jbx-meta">👍 ' + n.votes + '</span>' : '')) +
                    (canPull ? '<button class="chat-pin-del" type="button" title="Withdraw this nomination" ' +
                                   'data-chat-watch-unnom="' + attr(n.key) + '">×</button>' : '') +
                '</div>' +
            '</div>';
        }).join('');
        host.innerHTML = html;
        _watchMountStage(st.now);
    }

    function _watchFetchOwned(st) {
        if (state.watch.ownedDenied || Date.now() < state.watch.ownedRetryAt) return;
        var entries = (st.noms || []).slice();
        if (st.now) entries.push(st.now);
        var need = entries.filter(function (e) { return !(e.key in state.watch.owned); });
        if (!need.length) return;
        var sig = need.map(function (e) { return e.key; }).sort().join(',');
        if (state.watch.ownedFetching === sig) return;
        state.watch.ownedFetching = sig;
        postJSON('/api/video/watch/owned', {
            items: need.map(function (e) {
                return e.kd === 't' ? { kd: 't', id: e.id, s: e.s, e: e.e }
                                    : { kd: 'm', id: e.id };
            }),
        }).then(function (res) {
            state.watch.ownedFetching = '';
            if (res.ok && res.body && res.body.owned) {
                Object.assign(state.watch.owned, res.body.owned);
                if (res.body.art) Object.assign(state.watch.art, res.body.art);
                renderWatch();
            } else if (res.status === 403) {
                state.watch.ownedDenied = true;    // music-only profile: no video side
            } else {
                state.watch.ownedRetryAt = Date.now() + 60000;
            }
        }).catch(function () {
            state.watch.ownedFetching = '';
            state.watch.ownedRetryAt = Date.now() + 60000;
        });
    }

    function _watchGrab(key) {
        var st = _watchState();
        var entry = null;
        (st.noms || []).concat(st.now ? [st.now] : []).forEach(function (e) {
            if (e.key === key) entry = e;
        });
        if (!entry || state.watch.grabbed[key]) return;
        state.watch.grabbed[key] = 1;
        renderWatch();
        // ONE hydrated call: the server enriches the bare bus context (id +
        // title + poster) into a full wishlist row — year/detail blob for
        // movies, episode title/still/air date/season poster for episodes —
        // then fires the manual search. The bus fields ride along as
        // fallbacks so the grab lands even if TMDB is unreachable.
        var p = { kd: entry.kd === 't' ? 't' : 'm', id: entry.id, ti: entry.ti || '' };
        if (entry.y) p.y = entry.y;
        if (entry.po) p.po = entry.po;
        if (entry.kd === 't') { p.s = entry.s; p.e = entry.e; }
        postJSON('/api/video/watch/grab', p).then(function (res) {
            if (!res.ok || !(res.body && res.body.success)) {
                delete state.watch.grabbed[key];
                if (typeof showToast === 'function') {
                    showToast((res.body && res.body.error) || 'Grab failed — is the video side set up?', 'error');
                }
                renderWatch();
                return;
            }
            if (typeof showToast === 'function') {
                showToast('🎬 Grabbing — it\'s on the video wishlist and searching now', 'success');
            }
        }).catch(function () { delete state.watch.grabbed[key]; renderWatch(); });
    }

    // ── movie night picker (TMDB search via the video side) ─────────────
    function _openWatchModal() {
        var ov = q('[data-chat-watch-modal]');
        if (!ov) return;
        ov.hidden = false;
        var grid = q('[data-chat-watch-searchgrid]');
        if (grid && !state.watch.searchResults.length) grid.innerHTML = _watchResultCards();
        var inp = q('[data-chat-watch-searchinput]');
        if (inp) { inp.focus(); inp.select(); }
    }

    function _closeWatchModal() {
        var ov = q('[data-chat-watch-modal]');
        if (ov) ov.hidden = true;
        if (_watchSearchTimer) { clearTimeout(_watchSearchTimer); _watchSearchTimer = null; }
        _watchSearchSeq += 1;                  // orphan any in-flight response
        state.watch.searchResults = [];
        state.watch.pickShow = -1;
    }

    function _watchResultCards() {
        var results = state.watch.searchResults;
        if (!results.length) {
            return '<div class="chat-watch-resnote">Type at least two letters — this searches YOUR library.<br>' +
                'Movie night runs on what someone can actually press play on.</div>';
        }
        return results.map(function (r, i) {
            var isShow = r.kind === 'show';
            var picking = state.watch.pickShow === i;
            var rating = (typeof r.rating === 'number' && r.rating > 0) ? r.rating.toFixed(1) : '';
            return '<div class="chat-watch-rescard' + (picking ? ' chat-watch-rescard--picking' : '') + '"' +
                ' role="button" tabindex="0" data-chat-watch-nom="' + i + '">' +
                '<div class="chat-watch-resposter">' +
                    // r.art is OUR poster proxy path (server-built, library id) —
                    // never a remote URL from the wire.
                    (r.art ? '<img src="' + attr(r.art) + '" alt="" loading="lazy">' : '🎬') +
                '</div>' +
                '<div class="chat-watch-resmain">' +
                    '<div class="chat-watch-restitle" title="' + attr(r.title || '') + '">' + esc(r.title || '') +
                        (r.year ? '<span class="chat-watch-resyear">' + esc(String(r.year)) + '</span>' : '') +
                    '</div>' +
                    '<div class="chat-watch-resmeta">' +
                        '<span class="chat-watch-reskind' + (isShow ? ' chat-watch-reskind--show' : '') + '">' +
                            (isShow ? 'SHOW' : 'MOVIE') + '</span>' +
                        (rating ? '<span>★ ' + rating + '</span>' : '') +
                        (isShow && r.episode_count
                            ? '<span>' + (r.owned_count || 0) + '/' + r.episode_count + ' episodes on hand</span>'
                            : '') +
                    '</div>' +
                    (picking
                        ? '<div class="chat-watch-sepick">' +
                              '<label>Season <input class="chat-input chat-watch-sein" data-chat-watch-se-s type="number" min="0" max="999" value="1"></label>' +
                              '<label>Episode <input class="chat-input chat-watch-sein" data-chat-watch-se-e type="number" min="0" max="9999" value="1"></label>' +
                              '<button class="chat-send-btn" type="button" data-chat-watch-nomshow="' + i + '">Nominate S·E</button>' +
                              '<button class="chat-send-btn" type="button" data-chat-watch-nowshow="' + i + '">Start now</button>' +
                          '</div>'
                        : '') +
                '</div>' +
                '<span class="chat-watch-resact">' +
                    (picking ? '' :
                        (isShow ? 'Pick episode ▸'
                                : '<button class="chat-arc-btn chat-arc-btn--go chat-watch-resnow" type="button" ' +
                                      'data-chat-watch-now="' + i + '" ' +
                                      'title="Put it on right now — skips the ballot">Start now</button>' +
                                  '<span class="chat-watch-resnom">Nominate ▸</span>')) + '</span>' +
            '</div>';
        }).join('');
    }

    // Live search: debounced as-you-type, with a sequence token so a slow
    // early response can never clobber the results of a later keystroke.
    var _watchSearchTimer = null;
    var _watchSearchSeq = 0;

    function _watchQueueSearch() {
        if (_watchSearchTimer) clearTimeout(_watchSearchTimer);
        _watchSearchTimer = setTimeout(_watchSearchSubmit, 300);
    }

    function _watchSearchSubmit() {
        if (_watchSearchTimer) { clearTimeout(_watchSearchTimer); _watchSearchTimer = null; }
        var inp = q('[data-chat-watch-searchinput]');
        var grid = q('[data-chat-watch-searchgrid]');
        if (!inp) return;
        var qtext = String(inp.value || '').trim();
        var seq = ++_watchSearchSeq;
        if (qtext.length < 2) {
            state.watch.searchResults = [];
            state.watch.pickShow = -1;
            if (grid) grid.innerHTML = _watchResultCards();
            return;
        }
        // Empty grid gets a searching note; existing results stay put until
        // the fresh ones land (no flicker while typing).
        if (grid && !state.watch.searchResults.length) {
            grid.innerHTML = '<div class="chat-watch-resnote">Searching…</div>';
        }
        getJSON('/api/video/watch/library?q=' + encodeURIComponent(qtext)).then(function (res) {
            if (seq !== _watchSearchSeq) return;          // a newer keystroke owns the grid
            state.watch.pickShow = -1;
            var results = (res.ok && res.body.results) || [];
            state.watch.searchResults = results;
            if (grid) grid.innerHTML = results.length ? _watchResultCards() :
                '<div class="chat-watch-resnote">' +
                (res.status === 403 ? 'Video access is disabled for this profile.'
                                    : 'Nothing in your library matches “' + esc(qtext) + '”.') + '</div>';
        }).catch(function () {
            if (seq !== _watchSearchSeq) return;
            if (grid) grid.innerHTML = '<div class="chat-watch-resnote">Search failed — try again.</div>';
        });
    }

    // ``now`` = nominate AND start it the moment the fold sees the nomination.
    // Alone in a room, nominate → vote → start is three gestures of ceremony to
    // watch your own film; the ballot still exists untouched for real parties.
    function _watchNominate(r, s, e, now) {
        var p = { id: String(r.tmdb_id), kd: (s != null) ? 't' : 'm',
                  ti: String(r.title || '').slice(0, 120) };
        if (r.year) p.y = String(r.year).slice(0, 4);
        // Only the server-vetted TMDB CDN poster rides the bus (r.po) — a
        // library row's raw artwork path can be a tokened Plex/Jellyfin URL,
        // which must never be broadcast into a public Soulseek room.
        if (r.po && /^https:\/\/image\.tmdb\.org\//.test(r.po)) p.po = String(r.po).slice(0, 200);
        if (s != null) { p.s = s; p.e = e; }
        if (now) {
            // The key is the reducer's to compute — mirror its shape ONCE here
            // only to know what to wait for, and let renderWatch confirm the
            // nomination actually landed before starting anything.
            state.watch.autoStart = (s != null)
                ? 't:' + p.id + ':' + s + 'x' + e
                : 'm:' + p.id;
        }
        sendProtocol('watch.nom', p);
        _closeWatchModal();
        if (typeof showToast === 'function') {
            showToast(now ? '🎬 Starting…' : '🎬 Nominated — the room votes', 'success');
        }
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
            if (d && d.messages && d.messages.length) {
                mergeMessages(d.messages);
                _clearTypingFor(d.messages);
                renderMessages(state.msgs);
            } else {
                refresh();               // live update fallback
            }
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
                        // Arcade HTML builders — usernames and results come off
                        // Soulseek, so the same escaping contract applies here
                        _arcLobbyHtml: _arcLobbyHtml, _arcBoardHtml: _arcBoardHtml,
                        _arcSidebarHtml: _arcSidebarHtml, _arcPgn: _arcPgn,
                        _arcBsBoardHtml: _arcBsBoardHtml, _arcSlotHtml: _arcSlotHtml,
                        _bsPlaceAt: _bsPlaceAt, _bsDraft: _bsDraft,
                        _slotPayout: function (r, s2) { return _slotPayout(r, s2); },
                        renderUserPanel: renderUserPanel, renderGuilds: renderGuilds,
                        openWebPreviewModal: openWebPreviewModal, closeWebPreviewModal: closeWebPreviewModal,
                        _testSetSelf: function (n) { state.selfName = n; },
                        // send format: the filter/channel rules that decide
                        // envelope vs plain (tests/js/chat_send_format_harness.mjs)
                        _plainOn: _plainOn, _tagRoomPayload: _tagRoomPayload,
                        _testSetState: function (patch) {
                            Object.keys(patch || {}).forEach(function (k) { state[k] = patch[k]; });
                        } };
})();
