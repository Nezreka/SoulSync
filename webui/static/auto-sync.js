// Compatibility helpers for classic mirrored-playlist dialogs and cards.
// AutoSyncHost owns the manager and delegates legacy run/resume actions to
// the native pipeline controller. The script-scoped discovery registry still
// needs this card-state adapter until its remaining consumers are migrated.

function getMirroredSourceRef(p) {
    if (p && p.source_ref) return String(p.source_ref);
    const desc = (p && p.description) ? String(p.description).trim() : '';
    if ((p.source === 'spotify_public' || p.source === 'youtube') && /^https?:\/\//i.test(desc)) {
        return desc;
    }
    return (p && p.source_playlist_id) ? String(p.source_playlist_id) : '';
}

async function editMirroredCustomName(playlistId, originalName, currentCustom) {
    const nextName = window.prompt(
        `Rename "${originalName}"\n\nThis name is shown here and used when syncing. ` +
        `Leave blank to use the original name.`,
        currentCustom || ''
    );
    if (nextName === null) return;
    const trimmed = nextName.trim();
    try {
        const res = await fetch(`/api/mirrored-playlists/${playlistId}/custom-name`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ custom_name: trimmed })
        });
        const data = await res.json();
        if (!res.ok || data.error) {
            throw new Error(data.error || 'Failed to update name');
        }
        showToast(trimmed ? `Renamed to "${trimmed}"` : `Reverted to "${originalName}"`, 'success');
        loadMirroredPlaylists();
        const openModal = document.getElementById('mirrored-track-modal');
        if (openModal) {
            closeMirroredModal();
            openMirroredPlaylistModal(playlistId);
        }
    } catch (err) {
        showToast(`Error: ${err.message}`, 'error');
    }
}

async function editMirroredSourceRef(playlistId, name, source, currentRef) {
    const label = (source === 'spotify_public' || source === 'youtube')
        ? 'original playlist URL'
        : 'original playlist ID or URL';
    const nextRef = window.prompt(`Update ${label} for "${name}"`, currentRef || '');
    if (nextRef === null) return;
    const trimmed = nextRef.trim();
    if (!trimmed) {
        showToast('Source link or ID is required', 'error');
        return;
    }

    try {
        const res = await fetch(`/api/mirrored-playlists/${playlistId}/source-ref`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ source_ref: trimmed })
        });
        const data = await res.json();
        if (!res.ok || data.error) {
            throw new Error(data.error || 'Failed to update source reference');
        }
        showToast(`Updated source for ${name}`, 'success');
        loadMirroredPlaylists();
        const openModal = document.getElementById('mirrored-track-modal');
        if (openModal) {
            closeMirroredModal();
            openMirroredPlaylistModal(playlistId);
        }
    } catch (err) {
        showToast(`Error: ${err.message}`, 'error');
    }
}

function applyMirroredPipelineState(playlistId, state) {
    const hash = `mirrored_${playlistId}`;
    const existing = youtubePlaylistStates[hash] || {};
    const status = state.status || 'idle';
    let phase = existing.phase;
    if (status === 'running') phase = 'pipeline_running';
    else if (status === 'finished') phase = 'pipeline_complete';
    else if (status === 'error' || status === 'skipped') phase = 'pipeline_error';

    youtubePlaylistStates[hash] = {
        ...existing,
        phase,
        pipeline_status: status,
        pipeline_progress: state.progress || 0,
        pipeline_phase: state.phase || '',
        pipeline_error: state.error || '',
        pipeline_log: state.log || [],
        pipeline_result: state.result || null,
    };

    updateMirroredCardPhase(hash, phase);
}
