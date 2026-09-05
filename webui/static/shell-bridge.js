// SoulSync shell bridge glue
// Keep this file loaded after init.js so the legacy shell runtime state is ready.

function getWebRouter() {
    return window.SoulSyncWebRouter ?? null;
}

function showLegacyPage(pageId) {
    document.querySelectorAll('.page').forEach(page => {
        page.classList.remove('active');
    });
    const page = document.getElementById(`${pageId}-page`);
    if (page) {
        page.classList.add('active');
    }
    const reactHost = document.getElementById('webui-react-root');
    if (reactHost) {
        reactHost.classList.remove('active');
    }
}

function setActivePageChrome(pageId) {
    // Only manage MUSIC nav buttons (they carry data-page). The video sidebar owns
    // its own highlight via .nav-button[data-video-page]; clearing those here wiped
    // the video selection on the first nav — it only re-stuck on a second click,
    // which hits navigateToPage's same-page early-return so this never ran. (#sidebar)
    document.querySelectorAll('.nav-button[data-page]').forEach(btn => {
        btn.classList.remove('active');
        btn.removeAttribute('aria-current');
    });
    const navButton = document.querySelector(`[data-page="${pageId}"]`);
    if (navButton) {
        navButton.classList.add('active');
        navButton.setAttribute('aria-current', 'page');
    } else if (pageId === 'artist-detail') {
        // Artist detail is a Library context, so keep the sidebar anchored there.
        const libraryBtn = document.querySelector('[data-page="library"]');
        if (libraryBtn) {
            libraryBtn.classList.add('active');
            libraryBtn.setAttribute('aria-current', 'page');
        }
    }
    currentPage = pageId;
    if (typeof _updateSidebarLibraryBreadcrumb === 'function') _updateSidebarLibraryBreadcrumb();
    if (typeof _gsUpdateVisibility === 'function') _gsUpdateVisibility();
    // Defer to next frame so the page switch paints before particle/orb reinitialization
    requestAnimationFrame(() => {
        if (window.pageParticles && window._particlesEnabled !== false) window.pageParticles.setPage(pageId);
        if (window.workerOrbs) window.workerOrbs.setPage(pageId);
    });
}

function showReactHost(pageId) {
    document.querySelectorAll('.page').forEach(page => {
        page.classList.remove('active');
    });
    const host = document.getElementById('webui-react-root');
    if (host) {
        host.classList.add('active');
    }
    currentPage = pageId;
    if (typeof _gsUpdateVisibility === 'function') _gsUpdateVisibility();
    if (window.pageParticles && window._particlesEnabled !== false) window.pageParticles.setPage(pageId);
    if (window.workerOrbs) window.workerOrbs.setPage(pageId);
}

function activateLegacyPath(pathname) {
    const router = getWebRouter();
    const targetPage = router?.resolvePageId?.(pathname) || _getPageFromPath(pathname);
    if (!targetPage) return;

    if (!isPageAllowed(targetPage)) {
        const home = getProfileHomePage();
        if (home !== targetPage) {
            navigateToPage(home, { replace: true });
        }
        return;
    }

    // If the page was already shown optimistically (forward nav), skip re-activation
    // to avoid a duplicate data load. Back-button nav always has _optimisticNavPageId = null.
    if (typeof _optimisticNavPageId !== 'undefined' && _optimisticNavPageId === targetPage) {
        _optimisticNavPageId = null;
        return;
    }
    _optimisticNavPageId = null;

    notifyPageWillChange(targetPage);

    // A REACT-owned page must never go through activatePage(): showLegacyPage
    // strips `active` from #webui-react-root and hands it to `#<page>-page`,
    // which no longer exists once that page's vanilla markup has been deleted.
    // The host goes dark and nothing replaces it — React is still mounted, just
    // hidden, which is why navigating elsewhere and back "fixes" it.
    //
    // This is reachable from the VIDEO side: its URLs (/video-dashboard) match
    // no React route, so the catch-all route ($.tsx -> LegacyRouteController)
    // calls in here, and _getPageFromPath maps any unknown path to 'dashboard'
    // (init.js 2961). Switching back to music then leaves a blank page.
    //
    // syncActivePageFromLocation below already does this check; this function
    // simply never got it. The `!legacyPageElement` arm covers a page that is
    // still marked legacy in the manifest but whose markup has gone.
    const route = router?.routeManifest?.find((entry) => entry.pageId === targetPage);
    const legacyPageElement = document.getElementById(`${targetPage}-page`);
    if (route?.kind === 'react' || !legacyPageElement) {
        showReactHost(targetPage);
        setActivePageChrome(targetPage);
        return;
    }
    activatePage(targetPage, { forceReload: true });
}

function syncActivePageFromLocation() {
    const router = getWebRouter();
    const targetPage = router?.resolvePageId?.(window.location.pathname) || _getPageFromPath(window.location.pathname);
    if (!targetPage) return;

    if (!isPageAllowed(targetPage)) {
        const home = getProfileHomePage();
        if (home !== targetPage) {
            navigateToPage(home, { replace: true });
        }
        return;
    }

    notifyPageWillChange(targetPage);
    const route = router?.routeManifest?.find((entry) => entry.pageId === targetPage);
    if (route?.kind === 'react') {
        showReactHost(targetPage);
    } else {
        showLegacyPage(targetPage);
    }
    setActivePageChrome(targetPage);
}

const SHELL_BRIDGE_READY_EVENT = 'ss:webui-shell-bridge-ready';

function openDownloadMissingAlbumWorkflow(input) {
    if (typeof openDownloadMissingModalForArtistAlbum !== 'function') {
        throw new Error('Download workflow host is not ready yet');
    }

    return openDownloadMissingModalForArtistAlbum(
        input.virtualPlaylistId,
        input.playlistName,
        input.tracks,
        input.album,
        input.artist,
        false,
    );
}

function openAddToWishlistAlbumWorkflow(input) {
    if (typeof openAddToWishlistModal !== 'function') {
        throw new Error('Wishlist workflow host is not ready yet');
    }

    return openAddToWishlistModal(input.album, input.artist, input.tracks, input.albumType);
}

window.SoulSyncWorkflowActions = {
    openDownloadMissingAlbum: openDownloadMissingAlbumWorkflow,
    openAddToWishlistAlbum: openAddToWishlistAlbumWorkflow,
    notify(message, type) {
        if (typeof showToast === 'function') {
            showToast(message, type);
        }
    },
};

window.SoulSyncWebShellBridge = {
    getCurrentProfileContext() {
        if (!currentProfile) return null;
        return {
            profileId: currentProfile.id,
            isAdmin: !!currentProfile.is_admin,
        };
    },
    isPageAllowed(pageId) {
        return isPageAllowed(pageId);
    },
    getProfileHomePage() {
        return getProfileHomePage();
    },
    resolveLegacyPath(pathname) {
        return getWebRouter()?.resolvePageId?.(pathname) ?? null;
    },
    setActivePageChrome(pageId) {
        setActivePageChrome(pageId);
    },
    activateLegacyPath(pathname) {
        activateLegacyPath(pathname);
    },
    cancelSimilarArtistsLoad() {
        if (typeof cancelSimilarArtistsLoad === 'function') {
            cancelSimilarArtistsLoad();
        }
    },
    showReactHost(pageId) {
        showReactHost(pageId);
    },
    navigateToArtistDetail(artistId, artistName, sourceOverride, options) {
        return navigateToArtistDetail(artistId, artistName, sourceOverride, options);
    },
    navigateToLabelDetail(labelId, labelName, options) {
        return navigateToLabelDetail(labelId, labelName, options);
    },
    playLibraryTrack(track, albumTitle, artistName) {
        return playLibraryTrack(track, albumTitle, artistName);
    },
    startStream(searchResult) {
        return startStream(searchResult);
    },
    showLoadingOverlay(message) {
        return showLoadingOverlay(message);
    },
    hideLoadingOverlay() {
        return hideLoadingOverlay();
    },
};

// A touch that MOVED before lifting is a scroll, not a tap. Tracked here rather
// than in init.js because both nav paths below are document-level CAPTURE
// listeners: capture runs outermost-first, so a guard bound to .sidebar can
// never run before them, no matter what it does. This has to sit in front of
// the same handlers it protects.
const _TAP_SLOP_PX = 8;   // a tap wobbles a few px; a drag does not
let _touchOrigin = null;
let _touchDragged = false;

document.addEventListener('touchstart', (event) => {
    _touchOrigin = event.touches.length === 1
        ? { x: event.touches[0].clientX, y: event.touches[0].clientY }
        : null;
    _touchDragged = false;
}, { passive: true, capture: true });

document.addEventListener('touchmove', (event) => {
    if (!_touchOrigin) return;
    // Distance, not just vertical travel: a drag across the drawer moves mostly
    // sideways, and a Y-only check waves it straight through.
    const dx = event.touches[0].clientX - _touchOrigin.x;
    const dy = event.touches[0].clientY - _touchOrigin.y;
    if (Math.hypot(dx, dy) > _TAP_SLOP_PX) _touchDragged = true;
}, { passive: true, capture: true });

function _consumeTouchDrag() {
    if (!_touchDragged) return false;
    _touchDragged = false;   // one suppression per gesture; never latch
    return true;
}

function _handleShellLinkClick(event) {
    if (event.defaultPrevented || event.button !== 0 || _isModifiedLinkClick(event)) return;

    // The gesture that produced this click was a scroll. Swallow it, or dragging
    // the mobile drawer opens whichever entry the finger started on.
    if (_consumeTouchDrag()) {
        event.preventDefault();
        event.stopPropagation();
        return;
    }

    const anchor = event.target?.closest?.('a[href]');
    if (!anchor || (anchor.target && anchor.target !== '_self')) return;
    if (anchor.hasAttribute('download')) return;

    // In-card controls (source/watchlist badges, etc.) handle their OWN click — don't let
    // this capture-phase handler hijack it into the surrounding card's navigation. Their
    // bubble-phase handlers preventDefault, but that runs after capture, so we opt out here.
    if (event.target?.closest?.('.source-card-icon, [data-no-card-nav]')) return;

    const href = anchor.getAttribute('href');
    if (!href || href === '#' || href.startsWith('javascript:')) return;

    const pathname = anchor.pathname || new URL(anchor.href, window.location.href).pathname;
    const navPageId = anchor.matches('.nav-button[data-page]') ? anchor.getAttribute('data-page') : null;
    if (navPageId) {
        event.preventDefault();
        // iss29-B01: `navigateToPage` early-returns on `pageId === currentPage`
        // (init.js:3175), and every Library V2 sub-view keeps currentPage at
        // 'library' — so from /library?artist=42 the sidebar's own "Library"
        // button did nothing at all: no URL change, no view change, no
        // feedback. Sending a React page to its BASE path instead both escapes
        // that guard and does what the click means: drop the sub-view's search
        // params and show the page itself.
        const navRoute = getWebRouter()?.routeManifest?.find((entry) => entry.pageId === navPageId);
        if (navRoute?.kind === 'react' && getWebRouter()?.navigateToHref) {
            notifyPageWillChange(navPageId);
            showReactHost(navPageId);
            setActivePageChrome(navPageId);
            navigateOrFollow(getWebRouter(), navRoute.path, pathname);
            return;
        }
        void navigateToPage(navPageId);
        return;
    }

    if (pathname.startsWith('/artist-detail/')) {
        _handleArtistDetailLinkClick(event, pathname, anchor);
        return;
    }

    // Label cards render plain /label-detail/<id> hrefs (search results,
    // watchlist). Left to the browser that's a FULL page reload — app
    // reboot, in-memory search gone — and it skips navigateToLabelDetail,
    // so the label page's Back button loses its return target.
    if (pathname.startsWith('/label-detail/')) {
        _handleLabelDetailLinkClick(event, pathname, anchor);
        return;
    }

    _handleReactRouteLinkClick(event, pathname, anchor);
}

/**
 * Hand a plain anchor pointing at a React-owned route back to the SPA router.
 *
 * iss29-B03: a search result for an artist Library V2 knows is a bare
 * `<a href="/library?artist=7">` — no onClick, and TanStack does not intercept
 * raw anchors. Nothing above matched it, so the browser performed a FULL
 * document load. The href travels whole, query string included.
 */
function _handleReactRouteLinkClick(event, pathname, anchor) {
    const router = getWebRouter();
    if (!router?.navigateToHref) return;

    const targetPage = router.resolvePageId?.(pathname);
    if (!targetPage) return;
    const route = router.routeManifest?.find((entry) => entry.pageId === targetPage);
    if (route?.kind !== 'react') return;
    if (!isPageAllowed(targetPage)) return;

    event.preventDefault();
    notifyPageWillChange(targetPage);
    showReactHost(targetPage);
    setActivePageChrome(targetPage);
    navigateOrFollow(router, `${pathname}${anchor.search || ''}${anchor.hash || ''}`, anchor.href);
}

/** Navigate, and if the router rejects it, fall back to a real page load.
 *
 * These call sites `preventDefault()` the click first, so a rejected promise
 * used to leave a dead link: the browser did not follow it, the router did not
 * take it, and `void` discarded the reason. `location.assign` is the honest
 * fallback -- the user asked to go somewhere, so go there, even if it costs a
 * full reload (frontend-audit FE-09).
 */
function navigateOrFollow(router, href, fallbackHref) {
    try {
        const result = router.navigateToHref(href);
        if (result && typeof result.catch === 'function') {
            result.catch((error) => {
                console.error('SPA navigation to', href, 'failed:', error);
                window.location.assign(fallbackHref ?? href);
            });
        }
    } catch (error) {
        console.error('SPA navigation to', href, 'threw:', error);
        window.location.assign(fallbackHref ?? href);
    }
}

function _handleArtistDetailLinkClick(event, pathname, anchor) {
    const parts = pathname.split('/').filter(Boolean);
    if (parts.length < 3) return;

    // Keep the semantic link, but hand the click back to the SPA router so
    // artist detail navigations stay in-app when the link is left-clicked.
    const source = decodeURIComponent(parts[1] || '');
    const artistId = decodeURIComponent(parts.slice(2).join('/'));
    if (!source || !artistId) return;

    // Some sources (Bandcamp) have no numeric-ID lookup API — the artist's
    // display name has to travel with the click, or the destination page
    // has nothing to resolve against. The card already stashes it as a data
    // attribute (renderCompactSection); the href's own ?name= query (set by
    // buildArtistDetailPath) is the fallback for anchors that don't.
    const artistName = anchor?.dataset?.artistName
        || new URLSearchParams(anchor?.search || '').get('name')
        || '';

    event.preventDefault();
    void navigateToPage('artist-detail', {
        artistId,
        artistSource: source,
        artistName,
        forceReload: true,
    });
}

function _handleLabelDetailLinkClick(event, pathname, anchor) {
    const parts = pathname.split('/').filter(Boolean);
    if (parts.length < 2) return;

    const labelId = decodeURIComponent(parts.slice(1).join('/'));
    if (!labelId) return;
    // Without the navigator the default navigation is still a working page
    // — just the slow one — so only claim the click when we can do better.
    if (typeof navigateToLabelDetail !== 'function') return;

    const name = new URLSearchParams(anchor?.search || '').get('name') || null;
    event.preventDefault();
    navigateToLabelDetail(labelId, name);
}

function _isModifiedLinkClick(event) {
    return event.metaKey || event.ctrlKey || event.shiftKey || event.altKey;
}

window.addEventListener('popstate', syncActivePageFromLocation);
document.addEventListener('click', _handleShellLinkClick, true);

// Fire nav on pointerdown (fires on press, 100-200ms before click) for instant sidebar response.
// navigateToPage's early-return guard (pageId === currentPage) prevents double-navigation on click.
//
// MOUSE AND PEN ONLY. With a mouse, pressing is unambiguous: you don't scroll
// by pressing, so acting on pointerdown is pure latency win. On touch it is the
// opposite — a press is also the FIRST FRAME of a scroll, and there is no
// movement yet to tell the two apart. Navigating here meant that dragging the
// drawer to scroll it opened whichever entry the finger happened to land on,
// every time. The drag guard in init.js (TAP_SLOP_PX) could never help: it
// arbitrates the click, and the click came long after this had already
// navigated. Touch now falls through to the normal click path, where that
// guard sees the movement and swallows the tap.
document.addEventListener('pointerdown', (event) => {
    if (event.pointerType === 'touch') return;
    if (event.button !== 0 || _isModifiedLinkClick(event)) return;
    const btn = event.target?.closest?.('.nav-button[data-page]');
    if (!btn) return;
    const pageId = btn.getAttribute('data-page');
    if (pageId) void navigateToPage(pageId);
}, true);

window.dispatchEvent(new CustomEvent(SHELL_BRIDGE_READY_EVENT));
