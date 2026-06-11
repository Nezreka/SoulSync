// Bulk playlist selection + sequential processing across Sync page sources.

let activeSyncSelectionSource = null;

const SYNC_TERMINAL_PHASES = ['sync_complete', 'downloading', 'download_complete'];

function getSyncSourceKeyFromTab(tabId) {
    const map = {
        spotify: 'spotify',
        tidal: 'tidal',
        deezer: 'deezer',
        'deezer-link': 'deezer-link',
        qobuz: 'qobuz',
        youtube: 'youtube',
        'spotify-public': 'spotify-public',
        'itunes-link': 'itunes-link',
        beatport: 'beatport',
        'listenbrainz-sync': 'listenbrainz-sync',
        'lastfm-sync': 'lastfm-sync',
        'soulsync-discovery-sync': 'soulsync-discovery-sync',
    };
    return map[tabId] || null;
}

function onSyncTabChanged(tabId) {
    selectedPlaylists.clear();
    activeSyncSelectionSource = getSyncSourceKeyFromTab(tabId);
    updateSyncActionsUI();
}

function getActiveSyncBulkConfig() {
    if (!activeSyncSelectionSource) return null;
    return SYNC_BULK_SOURCES[activeSyncSelectionSource] || null;
}

function isSyncBulkTabActive() {
    const config = getActiveSyncBulkConfig();
    if (!config) return false;
    const tab = document.getElementById(config.tabContentId);
    return !!(tab && tab.classList.contains('active'));
}

function getCardSelectId(card, config) {
    if (!card || !config) return null;
    if (config.idFromCard) return config.idFromCard(card);
    if (config.idAttr === 'playlistId') return card.dataset.playlistId;
    if (config.idAttr === 'urlHash') return card.dataset.urlHash;
    if (config.idAttr === 'lbMbid') return card.dataset.lbMbid;
    if (config.idAttr === 'ssdId') return card.dataset.ssdId;
    if (config.idAttr === 'chartHash') return card.dataset.chartHash;
    return card.dataset.syncSelectId || null;
}

function applySyncPlaylistSelectionToCards(sourceKey) {
    const config = SYNC_BULK_SOURCES[sourceKey];
    if (!config) return;
    document.querySelectorAll(config.cardQuery).forEach(card => {
        const id = getCardSelectId(card, config);
        if (id) card.classList.toggle('selected', selectedPlaylists.has(id));
    });
}

function toggleSyncPlaylistSelection(event, sourceKey, playlistId, cardEl) {
    const config = SYNC_BULK_SOURCES[sourceKey];
    if (!config) return;

    const container = document.getElementById(config.containerId);
    if (container?.classList.contains('selection-disabled')) return;

    const card = cardEl || event?.currentTarget;
    if (!card || !playlistId) return;

    if (event?.target?.closest?.('button') && !event.target.closest('.playlist-card-action-btn')) {
        return;
    }
    if (event?.target?.tagName === 'BUTTON' && event.target.classList.contains('playlist-card-action-btn')) {
        return;
    }

    const isSelected = !card.classList.contains('selected');
    card.classList.toggle('selected', isSelected);
    if (isSelected) {
        selectedPlaylists.add(playlistId);
    } else {
        selectedPlaylists.delete(playlistId);
    }
    updateSyncActionsUI();
}

function selectAllSyncPlaylists(sourceKey) {
    const config = SYNC_BULK_SOURCES[sourceKey];
    if (!config || (sequentialSyncManager && sequentialSyncManager.isRunning)) return;

    const ids = config.getIds();
    if (!ids.length) return;

    selectedPlaylists.clear();
    ids.forEach(id => selectedPlaylists.add(id));
    applySyncPlaylistSelectionToCards(sourceKey);
    updateSyncActionsUI();
}

function clearSyncPlaylistSelection() {
    if (sequentialSyncManager && sequentialSyncManager.isRunning) return;

    const sourceKey = activeSyncSelectionSource;
    selectedPlaylists.clear();
    if (sourceKey) applySyncPlaylistSelectionToCards(sourceKey);
    updateSyncActionsUI();
}

function waitUntilSyncStep(predicate, timeoutMs = 600000, intervalMs = 1000) {
    return new Promise((resolve, reject) => {
        const started = Date.now();
        const tick = () => {
            if (predicate()) return resolve();
            if (sequentialSyncManager && !sequentialSyncManager.isRunning) {
                return reject(new Error('Cancelled'));
            }
            if (Date.now() - started > timeoutMs) {
                return reject(new Error('Timed out waiting for playlist step'));
            }
            setTimeout(tick, intervalMs);
        };
        tick();
    });
}

function isTerminalSyncPhase(phase) {
    return SYNC_TERMINAL_PHASES.includes(phase);
}

async function bulkAutoAdvancePhaseSource(sourceKey, playlistId) {
    const handlers = {
        tidal: () => bulkAutoAdvanceTidal(playlistId),
        qobuz: () => bulkAutoAdvanceQobuz(playlistId),
        'deezer-link': () => bulkAutoAdvanceDeezerLink(playlistId),
        youtube: () => bulkAutoAdvanceYouTube(playlistId),
        'spotify-public': () => bulkAutoAdvanceSpotifyPublic(playlistId),
        'itunes-link': () => bulkAutoAdvanceITunesLink(playlistId),
        beatport: () => bulkAutoAdvanceBeatport(playlistId),
        'listenbrainz-sync': () => bulkAutoAdvanceListenBrainz(playlistId),
        'lastfm-sync': () => bulkAutoAdvanceListenBrainz(playlistId),
        'soulsync-discovery-sync': () => bulkAutoAdvanceSoulsyncDiscovery(playlistId),
    };
    const fn = handlers[sourceKey];
    if (!fn) throw new Error(`Bulk sync not supported for source: ${sourceKey}`);
    await fn();
}

async function bulkAutoAdvanceTidal(playlistId) {
    const urlHash = `tidal_${playlistId}`;
    for (let step = 0; step < 12; step++) {
        const state = tidalPlaylistStates[playlistId];
        if (!state) return;
        const phase = state.phase || 'fresh';

        if (isTerminalSyncPhase(phase)) return;

        if (phase === 'fresh') {
            if (!state.playlist?.tracks?.length) {
                const resp = await fetch(`/api/tidal/playlist/${playlistId}`);
                if (resp.ok) {
                    const fullData = await resp.json();
                    if (fullData.tracks?.length) {
                        state.playlist.tracks = fullData.tracks.map(t => ({
                            id: t.id, name: t.name, artists: t.artists || [],
                            album: t.album || '', duration_ms: t.duration_ms || 0,
                            track_number: t.track_number || 0,
                        }));
                    }
                }
            }
            const response = await fetch(`/api/tidal/discovery/start/${playlistId}`, { method: 'POST' });
            const result = await response.json();
            if (result.error) throw new Error(result.error);
            state.phase = 'discovering';
            updateTidalCardPhase(playlistId, 'discovering');
            startTidalDiscoveryPolling(urlHash, playlistId);
            await waitUntilSyncStep(() => {
                const p = tidalPlaylistStates[playlistId]?.phase;
                return p && p !== 'fresh' && p !== 'discovering';
            });
            continue;
        }

        if (phase === 'discovering') {
            await waitUntilSyncStep(() => {
                const p = tidalPlaylistStates[playlistId]?.phase;
                return p && p !== 'fresh' && p !== 'discovering';
            });
            continue;
        }

        if (phase === 'discovered') {
            await startTidalPlaylistSync(urlHash);
            await waitUntilSyncStep(() => isTerminalSyncPhase(tidalPlaylistStates[playlistId]?.phase));
            return;
        }

        if (phase === 'syncing') {
            await waitUntilSyncStep(() => isTerminalSyncPhase(tidalPlaylistStates[playlistId]?.phase));
            return;
        }

        return;
    }
}

async function bulkAutoAdvanceQobuz(playlistId) {
    const urlHash = `qobuz_${playlistId}`;
    for (let step = 0; step < 12; step++) {
        const state = qobuzPlaylistStates[playlistId];
        if (!state) return;
        const phase = state.phase || 'fresh';
        if (isTerminalSyncPhase(phase)) return;

        if (phase === 'fresh') {
            const response = await fetch(`/api/qobuz/discovery/start/${playlistId}`, { method: 'POST' });
            const result = await response.json();
            if (result.error) throw new Error(result.error);
            state.phase = 'discovering';
            updateQobuzCardPhase(playlistId, 'discovering');
            startQobuzDiscoveryPolling(urlHash, playlistId);
            await waitUntilSyncStep(() => {
                const p = qobuzPlaylistStates[playlistId]?.phase;
                return p && p !== 'fresh' && p !== 'discovering';
            });
            continue;
        }

        if (phase === 'discovering') {
            await waitUntilSyncStep(() => {
                const p = qobuzPlaylistStates[playlistId]?.phase;
                return p && p !== 'fresh' && p !== 'discovering';
            });
            continue;
        }

        if (phase === 'discovered') {
            await startQobuzPlaylistSync(urlHash);
            await waitUntilSyncStep(() => isTerminalSyncPhase(qobuzPlaylistStates[playlistId]?.phase));
            return;
        }

        if (phase === 'syncing') {
            await waitUntilSyncStep(() => isTerminalSyncPhase(qobuzPlaylistStates[playlistId]?.phase));
            return;
        }

        return;
    }
}

async function bulkAutoAdvanceDeezerLink(playlistId) {
    const urlHash = `deezer_${playlistId}`;
    for (let step = 0; step < 12; step++) {
        const state = deezerPlaylistStates[playlistId];
        if (!state) return;
        const phase = state.phase || 'fresh';
        if (isTerminalSyncPhase(phase)) return;

        if (phase === 'fresh') {
            const response = await fetch(`/api/deezer/discovery/start/${playlistId}`, { method: 'POST' });
            const result = await response.json();
            if (result.error) throw new Error(result.error);
            state.phase = 'discovering';
            updateDeezerCardPhase(playlistId, 'discovering');
            startDeezerDiscoveryPolling(urlHash, playlistId);
            await waitUntilSyncStep(() => {
                const p = deezerPlaylistStates[playlistId]?.phase;
                return p && p !== 'fresh' && p !== 'discovering';
            });
            continue;
        }

        if (phase === 'discovering') {
            await waitUntilSyncStep(() => {
                const p = deezerPlaylistStates[playlistId]?.phase;
                return p && p !== 'fresh' && p !== 'discovering';
            });
            continue;
        }

        if (phase === 'discovered') {
            await startDeezerPlaylistSync(urlHash);
            await waitUntilSyncStep(() => isTerminalSyncPhase(deezerPlaylistStates[playlistId]?.phase));
            return;
        }

        if (phase === 'syncing') {
            await waitUntilSyncStep(() => isTerminalSyncPhase(deezerPlaylistStates[playlistId]?.phase));
            return;
        }

        return;
    }
}

async function bulkAutoAdvanceYouTube(urlHash) {
    for (let step = 0; step < 12; step++) {
        const state = youtubePlaylistStates[urlHash];
        if (!state) return;
        const phase = state.phase || 'fresh';
        if (isTerminalSyncPhase(phase)) return;

        if (phase === 'fresh') {
            const response = await fetch(`/api/youtube/discovery/start/${urlHash}`, { method: 'POST' });
            const result = await response.json();
            if (result.error) throw new Error(result.error);
            state.phase = 'discovering';
            updateYouTubeCardPhase(urlHash, 'discovering');
            startYouTubeDiscoveryPolling(urlHash);
            await waitUntilSyncStep(() => {
                const p = youtubePlaylistStates[urlHash]?.phase;
                return p && p !== 'fresh' && p !== 'discovering';
            });
            continue;
        }

        if (phase === 'discovering') {
            await waitUntilSyncStep(() => {
                const p = youtubePlaylistStates[urlHash]?.phase;
                return p && p !== 'fresh' && p !== 'discovering';
            });
            continue;
        }

        if (phase === 'discovered') {
            await startYouTubePlaylistSync(urlHash);
            await waitUntilSyncStep(() => isTerminalSyncPhase(youtubePlaylistStates[urlHash]?.phase));
            return;
        }

        if (phase === 'syncing') {
            await waitUntilSyncStep(() => isTerminalSyncPhase(youtubePlaylistStates[urlHash]?.phase));
            return;
        }

        return;
    }
}

function mirrorYoutubeStateKey(fakeUrlHash, urlHash) {
    if (youtubePlaylistStates[fakeUrlHash] && !youtubePlaylistStates[urlHash]) {
        youtubePlaylistStates[urlHash] = { ...youtubePlaylistStates[fakeUrlHash] };
    }
}

async function bulkAutoAdvanceSpotifyPublic(urlHash) {
    const fakeUrlHash = `spotifypublic_${urlHash}`;
    for (let step = 0; step < 12; step++) {
        const state = spotifyPublicPlaylistStates[urlHash];
        if (!state) return;
        const phase = state.phase || 'fresh';
        if (isTerminalSyncPhase(phase)) return;

        if (phase === 'fresh') {
            const response = await fetch(`/api/spotify-public/discovery/start/${urlHash}`, { method: 'POST' });
            const result = await response.json();
            if (result.error) throw new Error(result.error);
            state.phase = 'discovering';
            updateSpotifyPublicCardPhase(urlHash, 'discovering');
            if (youtubePlaylistStates[fakeUrlHash]) youtubePlaylistStates[fakeUrlHash].phase = 'discovering';
            startSpotifyPublicDiscoveryPolling(fakeUrlHash, urlHash);
            await waitUntilSyncStep(() => {
                const p = spotifyPublicPlaylistStates[urlHash]?.phase;
                return p && p !== 'fresh' && p !== 'discovering';
            });
            continue;
        }

        if (phase === 'discovering') {
            await waitUntilSyncStep(() => {
                const p = spotifyPublicPlaylistStates[urlHash]?.phase;
                return p && p !== 'fresh' && p !== 'discovering';
            });
            continue;
        }

        if (phase === 'discovered') {
            mirrorYoutubeStateKey(fakeUrlHash, urlHash);
            await startSpotifyPublicPlaylistSync(urlHash);
            await waitUntilSyncStep(() => isTerminalSyncPhase(spotifyPublicPlaylistStates[urlHash]?.phase));
            return;
        }

        if (phase === 'syncing') {
            await waitUntilSyncStep(() => isTerminalSyncPhase(spotifyPublicPlaylistStates[urlHash]?.phase));
            return;
        }

        return;
    }
}

async function bulkAutoAdvanceITunesLink(urlHash) {
    const fakeUrlHash = `ituneslink_${urlHash}`;
    for (let step = 0; step < 12; step++) {
        const state = itunesLinkPlaylistStates[urlHash];
        if (!state) return;
        const phase = state.phase || 'fresh';
        if (isTerminalSyncPhase(phase)) return;

        if (phase === 'fresh') {
            const response = await fetch(`/api/itunes-link/discovery/start/${urlHash}`, { method: 'POST' });
            const result = await response.json();
            if (result.error) throw new Error(result.error);
            state.phase = 'discovering';
            updateITunesLinkCardPhase(urlHash, 'discovering');
            if (youtubePlaylistStates[fakeUrlHash]) youtubePlaylistStates[fakeUrlHash].phase = 'discovering';
            startITunesLinkDiscoveryPolling(fakeUrlHash, urlHash);
            await waitUntilSyncStep(() => {
                const p = itunesLinkPlaylistStates[urlHash]?.phase;
                return p && p !== 'fresh' && p !== 'discovering';
            });
            continue;
        }

        if (phase === 'discovering') {
            await waitUntilSyncStep(() => {
                const p = itunesLinkPlaylistStates[urlHash]?.phase;
                return p && p !== 'fresh' && p !== 'discovering';
            });
            continue;
        }

        if (phase === 'discovered') {
            mirrorYoutubeStateKey(fakeUrlHash, urlHash);
            await startITunesLinkPlaylistSync(urlHash);
            await waitUntilSyncStep(() => isTerminalSyncPhase(itunesLinkPlaylistStates[urlHash]?.phase));
            return;
        }

        if (phase === 'syncing') {
            await waitUntilSyncStep(() => isTerminalSyncPhase(itunesLinkPlaylistStates[urlHash]?.phase));
            return;
        }

        return;
    }
}

async function bulkAutoAdvanceBeatport(chartHash) {
    for (let step = 0; step < 12; step++) {
        const state = beatportChartStates[chartHash];
        if (!state) return;
        const phase = state.phase || 'fresh';
        if (isTerminalSyncPhase(phase)) return;

        if (phase === 'fresh') {
            const response = await fetch(`/api/beatport/discovery/start/${chartHash}`, { method: 'POST' });
            const result = await response.json();
            if (result.error) throw new Error(result.error);
            state.phase = 'discovering';
            updateBeatportCardPhase(chartHash, 'discovering');
            startBeatportDiscoveryPolling(chartHash);
            await waitUntilSyncStep(() => {
                const p = beatportChartStates[chartHash]?.phase;
                return p && p !== 'fresh' && p !== 'discovering';
            });
            continue;
        }

        if (phase === 'discovering') {
            await waitUntilSyncStep(() => {
                const p = beatportChartStates[chartHash]?.phase;
                return p && p !== 'fresh' && p !== 'discovering';
            });
            continue;
        }

        if (phase === 'discovered') {
            await startBeatportPlaylistSync(chartHash);
            await waitUntilSyncStep(() => isTerminalSyncPhase(beatportChartStates[chartHash]?.phase));
            return;
        }

        if (phase === 'syncing') {
            await waitUntilSyncStep(() => isTerminalSyncPhase(beatportChartStates[chartHash]?.phase));
            return;
        }

        return;
    }
}

async function ensureListenBrainzPlaylistState(playlistMbid, playlistTitle) {
    if (listenbrainzPlaylistStates[playlistMbid]?.playlist?.tracks?.length) {
        return listenbrainzPlaylistStates[playlistMbid];
    }

    if (typeof listenbrainzTracksCache === 'undefined') {
        window.listenbrainzTracksCache = {};
    }
    let tracks = listenbrainzTracksCache[playlistMbid];
    if (!tracks?.length) {
        const resp = await fetch(`/api/discover/listenbrainz/playlist/${encodeURIComponent(playlistMbid)}`);
        if (!resp.ok) throw new Error(`Failed to load playlist tracks (${resp.status})`);
        const data = await resp.json();
        tracks = (data.tracks || []).map(t => ({
            track_name: t.track_name || '',
            artist_name: t.artist_name || '',
            album_name: t.album_name || '',
            duration_ms: t.duration_ms || 0,
            mbid: t.recording_mbid || t.mbid || '',
            release_mbid: t.release_mbid || '',
            album_cover_url: t.album_cover_url || '',
        }));
        listenbrainzTracksCache[playlistMbid] = tracks;
    }
    if (!tracks.length) throw new Error('Playlist has no tracks');

    const title = playlistTitle || 'ListenBrainz playlist';
    listenbrainzPlaylistStates[playlistMbid] = {
        phase: 'fresh',
        playlist: {
            name: title,
            tracks: tracks.map(track => ({ ...track })),
            description: `${tracks.length} tracks from ${title}`,
            source: 'listenbrainz',
        },
        is_listenbrainz_playlist: true,
        playlist_mbid: playlistMbid,
        discovery_results: [],
        discoveryResults: [],
        discovery_progress: 0,
        discoveryProgress: 0,
        spotify_matches: 0,
        spotifyMatches: 0,
        spotify_total: tracks.length,
        spotifyTotal: tracks.length,
    };
    return listenbrainzPlaylistStates[playlistMbid];
}

async function bulkAutoAdvanceListenBrainz(playlistMbid) {
    const card = document.querySelector(`#listenbrainz-sync-card-${CSS.escape(playlistMbid)}, #lastfm-sync-card-${CSS.escape(playlistMbid)}`);
    const title = card?.dataset.lbTitle || 'Playlist';
    await ensureListenBrainzPlaylistState(playlistMbid, title);

    const phase = listenbrainzPlaylistStates[playlistMbid]?.phase || 'fresh';
    if (isTerminalSyncPhase(phase)) return;

    if (phase === 'fresh' || phase === 'discovering') {
        if (typeof startListenBrainzDiscovery === 'function') {
            await startListenBrainzDiscovery(playlistMbid);
        }
        await waitUntilSyncStep(() => {
            const p = listenbrainzPlaylistStates[playlistMbid]?.phase;
            return p && p !== 'fresh' && p !== 'discovering';
        });
    }

    const afterDiscover = listenbrainzPlaylistStates[playlistMbid]?.phase;
    if (afterDiscover === 'discovered' && typeof startListenBrainzPlaylistSync === 'function') {
        await startListenBrainzPlaylistSync(playlistMbid);
        await waitUntilSyncStep(() => isTerminalSyncPhase(listenbrainzPlaylistStates[playlistMbid]?.phase));
    }
}

async function bulkAutoAdvanceSoulsyncDiscovery(syntheticId) {
    const card = document.getElementById(`soulsync-discovery-sync-card-${syntheticId}`);
    if (!card || typeof handleSoulsyncDiscoverySyncCardClick !== 'function') return;
    const kind = card.dataset.ssdKind;
    const variant = card.dataset.ssdVariant;
    const name = card.dataset.ssdName;
    await handleSoulsyncDiscoverySyncCardClick(kind, variant, name, card);
}

async function bulkSyncDeezerArlPlaylist(arlPlaylistId) {
    const rawId = arlPlaylistId.replace(/^deezer_arl_/, '');
    const playlistMeta = deezerArlPlaylists.find(p => String(p.id) === String(rawId));
    const cacheStale = typeof playlistTrackCacheIsStale === 'function'
        && playlistTrackCacheIsStale(arlPlaylistId, playlistMeta);
    if (!playlistTrackCache[arlPlaylistId] || cacheStale) {
        if (typeof fetchAndCacheDeezerArlPlaylistTracks === 'function') {
            await fetchAndCacheDeezerArlPlaylistTracks(arlPlaylistId, rawId);
        } else {
            const response = await fetch(`/api/deezer/arl-playlist/${rawId}`);
            const data = await response.json();
            if (data.error) throw new Error(data.error);
            playlistTrackCache[arlPlaylistId] = data.tracks;
        }
    }
    await startPlaylistSync(arlPlaylistId);
    await waitForSyncPollerCompletion(arlPlaylistId);
}

async function waitForSyncPollerCompletion(playlistId) {
    return new Promise((resolve) => {
        const checkCompletion = () => {
            if (!activeSyncPollers[playlistId]) {
                resolve();
                return;
            }
            if (sequentialSyncManager && !sequentialSyncManager.isRunning) {
                resolve();
                return;
            }
            setTimeout(checkCompletion, 1000);
        };
        checkCompletion();
    });
}

function wirePhaseSyncCards(sourceKey, containerSelector, cardSelector, getIdFromCard, onActionClick) {
    const container = document.querySelector(containerSelector);
    if (!container) return;

    container.querySelectorAll(cardSelector).forEach(card => {
        const id = getIdFromCard(card);
        if (!id) return;

        card.dataset.syncSelectId = id;

        const btn = card.querySelector('.playlist-card-action-btn');
        if (btn && !btn.dataset.syncBulkWired) {
            btn.dataset.syncBulkWired = '1';
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                if (typeof onActionClick === 'function') onActionClick(id);
            });
        }

        if (!card.dataset.syncBulkWired) {
            card.dataset.syncBulkWired = '1';
            card.addEventListener('click', (e) => {
                if (e.target.closest('.playlist-card-action-btn')) return;
                toggleSyncPlaylistSelection(e, sourceKey, id, card);
            });
        }
    });

    applySyncPlaylistSelectionToCards(sourceKey);
}

function wirePlaylistCardSelection(sourceKey, containerId) {
    const config = SYNC_BULK_SOURCES[sourceKey];
    if (!config) return;
    const container = document.getElementById(containerId);
    if (!container) return;

    container.querySelectorAll('.playlist-card[data-playlist-id]').forEach(card => {
        const id = card.dataset.playlistId;
        if (!id) return;

        if (!card.dataset.syncBulkWired) {
            card.dataset.syncBulkWired = '1';
            card.addEventListener('click', (e) => {
                if (e.target.tagName === 'BUTTON') return;
                toggleSyncPlaylistSelection(e, sourceKey, id, card);
            });
        }
    });

    applySyncPlaylistSelectionToCards(sourceKey);
}

const SYNC_BULK_SOURCES = {
    spotify: {
        tabContentId: 'spotify-tab-content',
        containerId: 'spotify-playlist-container',
        cardQuery: '#spotify-playlist-container .playlist-card[data-playlist-id]',
        idAttr: 'playlistId',
        getIds: () => spotifyPlaylists.map(p => p.id),
        getName: (id) => spotifyPlaylists.find(p => p.id === id)?.name || id,
        process: async (id) => {
            await startPlaylistSync(id);
            await waitForSyncPollerCompletion(id);
        },
    },
    deezer: {
        tabContentId: 'deezer-tab-content',
        containerId: 'deezer-arl-playlist-container',
        cardQuery: '#deezer-arl-playlist-container .playlist-card[data-playlist-id]',
        idAttr: 'playlistId',
        getIds: () => deezerArlPlaylists.map(p => `deezer_arl_${p.id}`),
        getName: (id) => {
            const raw = id.replace(/^deezer_arl_/, '');
            return deezerArlPlaylists.find(p => String(p.id) === String(raw))?.name || id;
        },
        process: (id) => bulkSyncDeezerArlPlaylist(id),
    },
    tidal: {
        tabContentId: 'tidal-tab-content',
        containerId: 'tidal-playlist-container',
        cardQuery: '#tidal-playlist-container .tidal-playlist-card',
        idFromCard: (card) => card.id.replace(/^tidal-card-/, ''),
        getIds: () => tidalPlaylists.map(p => p.id),
        getName: (id) => tidalPlaylistStates[id]?.playlist?.name || id,
        process: (id) => bulkAutoAdvancePhaseSource('tidal', id),
    },
    qobuz: {
        tabContentId: 'qobuz-tab-content',
        containerId: 'qobuz-playlist-container',
        cardQuery: '#qobuz-playlist-container .qobuz-playlist-card',
        idFromCard: (card) => card.id.replace(/^qobuz-card-/, ''),
        getIds: () => qobuzPlaylists.map(p => p.id),
        getName: (id) => qobuzPlaylistStates[id]?.playlist?.name || id,
        process: (id) => bulkAutoAdvancePhaseSource('qobuz', id),
    },
    'deezer-link': {
        tabContentId: 'deezer-link-tab-content',
        containerId: 'deezer-playlist-container',
        cardQuery: '#deezer-playlist-container .deezer-playlist-card',
        idFromCard: (card) => card.id.replace(/^deezer-card-/, ''),
        getIds: () => deezerPlaylists.map(p => p.id),
        getName: (id) => deezerPlaylistStates[id]?.playlist?.name || id,
        process: (id) => bulkAutoAdvancePhaseSource('deezer-link', id),
    },
    youtube: {
        tabContentId: 'youtube-tab-content',
        containerId: 'youtube-playlist-container',
        cardQuery: '#youtube-playlist-container .youtube-playlist-card[id^="youtube-card-"]',
        idFromCard: (card) => card.id.replace(/^youtube-card-/, ''),
        getIds: () => Object.keys(youtubePlaylistStates),
        getName: (id) => youtubePlaylistStates[id]?.playlist?.name || id,
        process: (id) => bulkAutoAdvancePhaseSource('youtube', id),
    },
    'spotify-public': {
        tabContentId: 'spotify-public-tab-content',
        containerId: 'spotify-public-playlist-container',
        cardQuery: '#spotify-public-playlist-container .spotify-public-card',
        idFromCard: (card) => card.id.replace(/^spotify-public-card-/, ''),
        getIds: () => spotifyPublicPlaylists.map(p => p.url_hash),
        getName: (id) => spotifyPublicPlaylistStates[id]?.playlist?.name || id,
        process: (id) => bulkAutoAdvancePhaseSource('spotify-public', id),
    },
    'itunes-link': {
        tabContentId: 'itunes-link-tab-content',
        containerId: 'itunes-link-playlist-container',
        cardQuery: '#itunes-link-playlist-container .itunes-link-card',
        idFromCard: (card) => card.id.replace(/^itunes-link-card-/, ''),
        getIds: () => itunesLinkPlaylists.map(p => p.url_hash),
        getName: (id) => itunesLinkPlaylistStates[id]?.playlist?.name || id,
        process: (id) => bulkAutoAdvancePhaseSource('itunes-link', id),
    },
    beatport: {
        tabContentId: 'beatport-tab-content',
        containerId: 'beatport-playlist-container',
        cardQuery: '#youtube-playlist-container [id^="beatport-card-"], #beatport-playlist-container [id^="beatport-card-"]',
        idFromCard: (card) => card.id.replace(/^beatport-card-/, ''),
        getIds: () => Object.keys(beatportChartStates),
        getName: (id) => beatportChartStates[id]?.chart?.name || beatportChartStates[id]?.name || id,
        process: (id) => bulkAutoAdvancePhaseSource('beatport', id),
    },
    'listenbrainz-sync': {
        tabContentId: 'listenbrainz-sync-tab-content',
        containerId: 'listenbrainz-sync-playlist-container',
        cardQuery: '#listenbrainz-sync-playlist-container .listenbrainz-playlist-card',
        idAttr: 'lbMbid',
        getIds: () => {
            const ids = [];
            document.querySelectorAll('#listenbrainz-sync-playlist-container .listenbrainz-playlist-card').forEach(card => {
                if (card.dataset.lbMbid) ids.push(card.dataset.lbMbid);
            });
            return ids;
        },
        getName: (id) => {
            const card = document.querySelector(`#listenbrainz-sync-card-${CSS.escape(id)}`);
            return card?.dataset.lbTitle || id;
        },
        process: (id) => bulkAutoAdvancePhaseSource('listenbrainz-sync', id),
    },
    'lastfm-sync': {
        tabContentId: 'lastfm-sync-tab-content',
        containerId: 'lastfm-sync-playlist-container',
        cardQuery: '#lastfm-sync-playlist-container .lastfm-playlist-card',
        idAttr: 'lbMbid',
        getIds: () => {
            const ids = [];
            document.querySelectorAll('#lastfm-sync-playlist-container .lastfm-playlist-card').forEach(card => {
                if (card.dataset.lbMbid) ids.push(card.dataset.lbMbid);
            });
            return ids;
        },
        getName: (id) => {
            const card = document.querySelector(`#lastfm-sync-card-${CSS.escape(id)}`);
            return card?.dataset.lbTitle || id;
        },
        process: (id) => bulkAutoAdvancePhaseSource('lastfm-sync', id),
    },
    'soulsync-discovery-sync': {
        tabContentId: 'soulsync-discovery-sync-tab-content',
        containerId: 'soulsync-discovery-sync-playlist-container',
        cardQuery: '#soulsync-discovery-sync-playlist-container .soulsync-discovery-playlist-card',
        idAttr: 'ssdId',
        getIds: () => {
            const ids = [];
            document.querySelectorAll('#soulsync-discovery-sync-playlist-container .soulsync-discovery-playlist-card').forEach(card => {
                if (card.dataset.ssdId) ids.push(card.dataset.ssdId);
            });
            return ids;
        },
        getName: (id) => {
            const card = document.getElementById(`soulsync-discovery-sync-card-${id}`);
            return card?.dataset.ssdName || id;
        },
        process: (id) => bulkAutoAdvancePhaseSource('soulsync-discovery-sync', id),
    },
};

function updateSyncActionsUI() {
    const count = selectedPlaylists.size;
    const isRunning = !!(sequentialSyncManager && sequentialSyncManager.isRunning);
    const bulkTabActive = isSyncBulkTabActive();
    const config = getActiveSyncBulkConfig();

    const selectionInfo = document.getElementById('selection-info');
    const startSyncBtn = document.getElementById('start-sync-btn');
    const bulkBar = document.getElementById('sync-playlist-bulk-bar');
    const bulkCount = document.getElementById('sync-playlist-bulk-count');
    const bulkLabel = document.getElementById('sync-playlist-bulk-label');
    let statusText;
    let syncBtnLabel;
    let syncEnabled = count > 0 && !!config;

    if (isRunning && sequentialSyncManager.bulkConfig) {
        const current = sequentialSyncManager.currentIndex + 1;
        const total = sequentialSyncManager.queue.length;
        const currentId = sequentialSyncManager.queue[sequentialSyncManager.currentIndex];
        const name = sequentialSyncManager.bulkConfig.getName(currentId);
        statusText = `Syncing ${current}/${total}: ${name || 'Unknown'}`;
        syncBtnLabel = 'Cancel sync';
        syncEnabled = true;
    } else if (!config) {
        statusText = 'Select playlists to sync';
        syncBtnLabel = 'Sync selected';
        syncEnabled = false;
    } else {
        statusText = count === 0
            ? 'Select playlists to sync'
            : `${count} playlist${count > 1 ? 's' : ''} selected`;
        syncBtnLabel = 'Sync selected';
    }

    if (selectionInfo) selectionInfo.textContent = statusText;
    if (startSyncBtn) {
        startSyncBtn.textContent = isRunning ? 'Cancel Sequential Sync' : 'Start Sync';
        startSyncBtn.disabled = !syncEnabled;
    }
    document.querySelectorAll('.sync-playlist-start-btn').forEach(btn => {
        const inActiveTab = btn.closest('.sync-tab-content.active');
        btn.textContent = isRunning && inActiveTab ? syncBtnLabel : 'Sync selected';
        btn.disabled = !(inActiveTab && syncEnabled);
        btn.classList.toggle('sync-playlist-start-btn--ready', !!(inActiveTab && count > 0 && !isRunning));
    });
    if (bulkCount) bulkCount.textContent = isRunning ? '' : String(count);
    if (bulkLabel) {
        bulkLabel.textContent = isRunning ? statusText : `playlist${count === 1 ? '' : 's'} selected`;
    }
    if (bulkBar) {
        const showBar = bulkTabActive && !!config && (count > 0 || isRunning);
        bulkBar.hidden = !showBar;
        bulkBar.classList.toggle('visible', showBar);
    }

    document.querySelectorAll('.sync-playlist-select-all-btn').forEach(btn => {
        const sourceKey = btn.dataset.syncSource;
        const sourceConfig = SYNC_BULK_SOURCES[sourceKey];
        const tab = sourceConfig && document.getElementById(sourceConfig.tabContentId);
        const tabActive = tab && tab.classList.contains('active');
        btn.disabled = !tabActive || isRunning || !sourceConfig?.getIds()?.length;
    });
    document.querySelectorAll('.sync-playlist-clear-btn').forEach(btn => {
        btn.disabled = count === 0 || isRunning;
    });
}

function getOrderedSelectedPlaylistIds() {
    const config = getActiveSyncBulkConfig();
    if (!config) return [];

    const ordered = [];
    document.querySelectorAll(config.cardQuery).forEach(card => {
        const id = getCardSelectId(card, config);
        if (id && selectedPlaylists.has(id)) ordered.push(id);
    });
    return ordered;
}

function disablePlaylistSelection(disabled) {
    const containerIds = new Set(Object.values(SYNC_BULK_SOURCES).map(c => c.containerId));
    containerIds.add('youtube-playlist-container');
    containerIds.forEach(id => {
        const container = document.getElementById(id);
        if (container) container.classList.toggle('selection-disabled', disabled);
    });

    document.querySelectorAll('.playlist-checkbox').forEach(checkbox => {
        checkbox.disabled = disabled;
    });

    if (disabled) {
        document.querySelectorAll('.sync-playlist-select-all-btn, .sync-playlist-clear-btn').forEach(btn => {
            btn.disabled = true;
        });
    } else {
        updateSyncActionsUI();
    }
}
