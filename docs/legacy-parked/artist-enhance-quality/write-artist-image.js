async function writeArtistImageToDisk(overwrite) {
    // Issue #572: writes `artist.jpg` to the artist's folder so
    // Navidrome (which has no API for artist images) picks up a
    // real photo on its next scan. Plex/Jellyfin also read this
    // file as a fallback, so it's safe to run regardless of which
    // server is active.
    const artistId = artistDetailPageState.currentArtistId;
    if (!artistId) {
        showToast('No artist selected', 'error');
        return;
    }
    const btn = document.getElementById('library-artist-write-image-btn');
    if (btn) {
        btn.disabled = true;
        const txt = btn.querySelector('.write-image-text');
        if (txt) txt.textContent = 'Writing…';
    }
    try {
        let resp = await fetch(`/api/artist/${artistId}/write-image-to-disk`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ overwrite: !!overwrite }),
        });
        let data = await resp.json();

        // Default protect-existing path: surface a confirm so the user
        // explicitly opts in to clobbering a hand-picked artist.jpg.
        if (!data.success && /already exists/i.test(data.error || '')) {
            if (confirm('artist.jpg already exists in the folder. Overwrite with the new image?')) {
                resp = await fetch(`/api/artist/${artistId}/write-image-to-disk`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ overwrite: true }),
                });
                data = await resp.json();
            } else {
                showToast('Cancelled — existing artist.jpg kept', 'info');
                return;
            }
        }

        if (!data.success) {
            showToast(`Failed: ${data.error || 'Unknown error'}`, 'error');
            return;
        }
        const scan = data.scan_triggered ? ' (Navidrome scan triggered)' : '';
        showToast(`artist.jpg written${scan}`, 'success');
    } catch (e) {
        showToast(`Write failed: ${e.message}`, 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            const txt = btn.querySelector('.write-image-text');
            if (txt) txt.textContent = 'Write Artist Image';
        }
    }
}
window.writeArtistImageToDisk = writeArtistImageToDisk;
