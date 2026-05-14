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
    document.querySelectorAll('.nav-button').forEach(btn => {
        btn.classList.remove('active');
    });
    const navButton = document.querySelector(`[data-page="${pageId}"]`);
    if (navButton) {
        navButton.classList.add('active');
    } else if (pageId === 'artist-detail') {
        // Artist detail is a Library context, so keep the sidebar anchored there.
        const libraryBtn = document.querySelector('[data-page="library"]');
        if (libraryBtn) {
            libraryBtn.classList.add('active');
        }
    }
    currentPage = pageId;
    if (typeof _updateSidebarLibraryBreadcrumb === 'function') _updateSidebarLibraryBreadcrumb();
    if (typeof _gsUpdateVisibility === 'function') _gsUpdateVisibility();
    const downloadSidebar = document.getElementById('discover-download-sidebar');
    if (downloadSidebar) {
        if (pageId === 'discover') {
            const activeDownloads = typeof discoverDownloads !== 'undefined'
                ? Object.keys(discoverDownloads).length
                : 0;
            if (activeDownloads > 0 && typeof updateDiscoverDownloadBar === 'function') {
                updateDiscoverDownloadBar();
            }
        } else {
            downloadSidebar.classList.add('hidden');
        }
    }
    if (window.pageParticles && window._particlesEnabled !== false) window.pageParticles.setPage(pageId);
    if (window.workerOrbs) window.workerOrbs.setPage(pageId);
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

    notifyPageWillChange(targetPage);
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
    showReactHost(pageId) {
        showReactHost(pageId);
    },
    navigateToArtistDetail(artistId, artistName, sourceOverride, options) {
        return navigateToArtistDetail(artistId, artistName, sourceOverride, options);
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

window.addEventListener('popstate', syncActivePageFromLocation);
window.dispatchEvent(new CustomEvent(SHELL_BRIDGE_READY_EVENT));
