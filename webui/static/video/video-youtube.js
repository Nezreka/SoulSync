/* Shared YouTube-channel helpers for the video side: detect a pasted channel
 * link, render channel/video cards, and follow/unfollow. Used by the search
 * page (paste a link → Follow), the wishlist YouTube tab, and the watchlist.
 * Scoped under window.VideoYoutube; all CSS is .vyt-* (never touches music wl-*).
 */
(function () {
    'use strict';

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    // A pasted YouTube *channel* reference: a full channel URL or a bare @handle.
    // Deliberately NOT bare words, so normal title searches don't trigger it.
    function isChannelRef(q) {
        q = (q || '').trim();
        if (/^@[\w.\-]{2,}$/.test(q)) return true;
        if (!/youtube\.com/i.test(q)) return false;
        return /youtube\.com\/(@[\w.\-]+|channel\/UC[\w\-]+|c\/[\w.\-]+|user\/[\w.\-]+)/i.test(q);
    }

    // A pasted YouTube *playlist* link (or bare PL/OL/FL/UU id). Rejects mixes (RD…)
    // and personal lists (WL/LL) — same rule as the backend parse_playlist_id.
    function isPlaylistRef(q) {
        q = (q || '').trim();
        var m = /[?&]list=([A-Za-z0-9_-]+)/.exec(q);
        var id = m ? m[1] : (/^(PL|OL|FL|UU)[A-Za-z0-9_-]{8,}$/.test(q) ? q : null);
        return !!(id && !/^(RD|UL|LL|WL)/.test(id));
    }

    function fmtDate(iso) {
        if (!iso) return '';
        var d = new Date(iso.length <= 10 ? iso + 'T00:00:00' : iso);
        if (isNaN(d)) return '';
        return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
    }

    function initials(s) {
        var w = String(s || '').trim().split(/\s+/).filter(Boolean);
        return (w.slice(0, 2).map(function (x) { return x[0]; }).join('') || '▶').toUpperCase();
    }

    // Route YouTube CDN images through our same-origin proxy so hotlink/CORS
    // policy can't blank them out. Non-YouTube / already-proxied urls pass through.
    function img(url) {
        if (!url) return url;
        if (url.indexOf('//') === 0) url = 'https:' + url;   // protocol-relative
        if (/^https:\/\/([\w-]+\.)*(ytimg\.com|ggpht\.com|googleusercontent\.com)\//i.test(url))
            return '/api/video/img?u=' + encodeURIComponent(url);
        return url;
    }

    function avatar(ch, cls) {
        var url = ch && (ch.poster_url || ch.avatar_url);
        var ini = esc(initials(ch && ch.title));
        if (url) {
            // If the image can't load, swap to the initials chip (never an empty circle).
            return '<img class="' + cls + '" src="' + esc(img(url)) + '" alt="" loading="lazy" ' +
                'onerror="this.outerHTML=\'<span class=&quot;' + cls + ' vyt-avatar--ph&quot;>' + ini + '</span>\'">';
        }
        return '<span class="' + cls + ' vyt-avatar--ph">' + ini + '</span>';
    }

    // A single wished video tile (thumbnail, title, date, remove).
    function videoCard(v) {
        var thumb = v.still_url
            ? '<img class="vyt-vid-img" src="' + esc(v.still_url) + '" alt="" loading="lazy" ' +
              'onerror="this.parentNode.classList.add(\'vyt-vid-thumb--none\')">'
            : '';
        var date = fmtDate(v.published_at);
        return '<div class="vyt-vid" data-vyt-vid="' + esc(v.youtube_id) + '">' +
            '<div class="vyt-vid-thumb' + (v.still_url ? '' : ' vyt-vid-thumb--none') + '">' + thumb +
                '<button class="vyt-vid-rm" type="button" data-vyt-rm="video" data-id="' + esc(v.youtube_id) +
                '" title="Remove">&#10005;</button></div>' +
            '<div class="vyt-vid-body">' +
                '<span class="vyt-vid-title" title="' + esc(v.title) + '">' + esc(v.title || 'Untitled') + '</span>' +
                (date ? '<span class="vyt-vid-date">' + esc(date) + '</span>' : '') +
            '</div></div>';
    }

    // The search-page result: avatar, title/handle, a strip of recent stills, Follow.
    function searchCard(ch, following) {
        var strip = (ch.videos || []).slice(0, 6).map(function (v) {
            return v.thumbnail_url
                ? '<span class="vyt-strip-cell"><img src="' + esc(img(v.thumbnail_url)) + '" alt="" loading="lazy" ' +
                  'onerror="this.parentNode.style.display=\'none\'"></span>'
                : '';
        }).join('');
        var sub = [];
        if (ch.handle) sub.push(esc(ch.handle));
        if (ch.video_count != null) sub.push(esc(ch.video_count) + ' videos');
        return '<div class="vyt-chip" data-vyt-channel="' + esc(ch.youtube_id) + '">' +
            '<div class="vyt-chip-head">' +
                avatar(ch, 'vyt-chip-avatar') +
                '<div class="vyt-chip-meta">' +
                    '<span class="vyt-chip-badge">YouTube channel</span>' +
                    '<span class="vyt-chip-title">' + esc(ch.title) + '</span>' +
                    (sub.length ? '<span class="vyt-chip-sub">' + sub.join(' · ') + '</span>' : '') +
                '</div>' +
                followBtn(following) +
            '</div>' +
            (strip ? '<div class="vyt-strip">' + strip + '</div>' : '') +
        '</div>';
    }

    function followBtn(following) {
        return '<button class="vyt-follow' + (following ? ' vyt-follow--on' : '') + '" type="button" ' +
            'data-vyt-follow>' + (following ? '✓ Following' : '+ Follow') + '</button>';
    }

    // A pasted-playlist result chip (mirrors searchCard): cover, title, owner +
    // count, and an Add-to-watchlist toggle (data-vyt-follow-playlist).
    function playlistCard(pl, following) {
        var cover = pl.thumbnail_url
            ? '<img class="vyt-chip-avatar" src="' + esc(img(pl.thumbnail_url)) + '" alt="" loading="lazy" ' +
              'onerror="this.outerHTML=\'<span class=&quot;vyt-chip-avatar vyt-avatar--ph&quot;>▤</span>\'">'
            : '<span class="vyt-chip-avatar vyt-avatar--ph">▤</span>';
        var sub = [];
        if (pl.channel_title) sub.push(esc(pl.channel_title));
        if (pl.video_count != null) sub.push(esc(pl.video_count) + ' videos');
        return '<div class="vyt-chip" data-vyt-playlist="' + esc(pl.playlist_id) + '">' +
            '<div class="vyt-chip-head">' + cover +
                '<div class="vyt-chip-meta">' +
                    '<span class="vyt-chip-badge">YouTube playlist</span>' +
                    '<span class="vyt-chip-title">' + esc(pl.title) + '</span>' +
                    (sub.length ? '<span class="vyt-chip-sub">' + sub.join(' · ') + '</span>' : '') +
                '</div>' +
                '<button class="library-artist-watchlist-btn' + (following ? ' watching' : '') + '" type="button" ' +
                'data-vyt-follow-playlist>' +
                    '<span class="watchlist-icon">' + (following ? '✓' : '＋') + '</span>' +
                    '<span class="watchlist-text">' + (following ? 'In Watchlist' : 'Add to Watchlist') + '</span>' +
                '</button>' +
            '</div></div>';
    }

    function post(url, body) {
        return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body || {}) }).then(function (r) { return r.ok ? r.json() : null; });
    }

    // Following a channel WATCHLISTS it (like a show) — it doesn't auto-wish all
    // its videos, so we send only the channel fields, not its video list.
    function follow(channel) {
        var ch = channel || {};
        return post('/api/video/youtube/follow',
            { channel: { youtube_id: ch.youtube_id, title: ch.title, avatar_url: ch.avatar_url } });
    }
    function unfollow(youtubeId) { return post('/api/video/youtube/unfollow', { youtube_id: youtubeId }); }
    function followPlaylist(pl) {
        var p = pl || {};
        return post('/api/video/youtube/playlist/follow', { playlist: {
            playlist_id: p.playlist_id, title: p.title, thumbnail_url: p.thumbnail_url } });
    }
    function unfollowPlaylist(playlistId) {
        return post('/api/video/youtube/playlist/unfollow', { playlist_id: playlistId });
    }
    function removeWish(scope, sourceId) {
        return post('/api/video/youtube/wishlist/remove', { scope: scope, source_id: sourceId });
    }
    function addVideos(channel, videos) {
        return post('/api/video/youtube/wishlist/add', { channel: channel, videos: videos });
    }

    // mm:ss / h:mm:ss from a seconds count
    function fmtDuration(sec) {
        sec = parseInt(sec, 10);
        if (!sec || sec < 0) return '';
        var h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
        var mm = (h && m < 10) ? '0' + m : '' + m, ss = s < 10 ? '0' + s : '' + s;
        return (h ? h + ':' + mm : m + '') + ':' + ss;
    }
    function compactCount(n) {
        n = parseInt(n, 10);
        if (!n && n !== 0) return '';
        if (n >= 1e9) return (n / 1e9).toFixed(1).replace(/\.0$/, '') + 'B';
        if (n >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
        if (n >= 1e3) return (n / 1e3).toFixed(1).replace(/\.0$/, '') + 'K';
        return '' + n;
    }

    function searchChannels(q) {
        return fetch('/api/video/youtube/search?q=' + encodeURIComponent(q), { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; });
    }

    // A compact channel card for the search results grid (→ opens the channel page).
    function channelResultCard(ch) {
        var sub = ch.subscriber_count ? compactCount(ch.subscriber_count) + ' subscribers' : (ch.handle ? esc(ch.handle) : '');
        return '<a class="vyt-result" href="#" data-vyt-open-channel="' + esc(ch.youtube_id) + '">' +
            '<span class="vyt-result-art">' + avatar(ch, 'vyt-result-avatar') + '</span>' +
            '<span class="vyt-result-info">' +
                '<span class="vyt-result-badge">YouTube</span>' +
                '<span class="vyt-result-title" title="' + esc(ch.title) + '">' + esc(ch.title) + '</span>' +
                (sub ? '<span class="vyt-result-sub">' + sub + '</span>' : '') +
            '</span></a>';
    }

    function resolve(ref) {
        return fetch('/api/video/youtube/resolve?url=' + encodeURIComponent(ref),
            { headers: { Accept: 'application/json' } }).then(function (r) { return r.ok ? r.json() : (r.status === 404 ? r.json() : null); });
    }

    window.VideoYoutube = {
        esc: esc, isChannelRef: isChannelRef, isPlaylistRef: isPlaylistRef, fmtDate: fmtDate, avatar: avatar, img: img,
        fmtDuration: fmtDuration, compactCount: compactCount,
        videoCard: videoCard, searchCard: searchCard, playlistCard: playlistCard, followBtn: followBtn,
        follow: follow, unfollow: unfollow, followPlaylist: followPlaylist, unfollowPlaylist: unfollowPlaylist,
        removeWish: removeWish, addVideos: addVideos, resolve: resolve,
        searchChannels: searchChannels, channelResultCard: channelResultCard,
    };
})();
