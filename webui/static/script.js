function updateStatus() {
    fetch('/status')
        .then(response => response.json())
        .then(data => {
            document.getElementById('spotify-status').textContent = data.spotify ? 'Connected' : 'Disconnected';
            document.getElementById('spotify-status').className = data.spotify ? 'connected' : 'disconnected';
            
            document.getElementById('media-status').textContent = data.media_server ? 'Connected' : 'Disconnected';
            document.getElementById('media-status').className = data.media_server ? 'connected' : 'disconnected';
            
            document.getElementById('soulseek-status').textContent = data.soulseek ? 'Connected' : 'Disconnected';
            document.getElementById('soulseek-status').className = data.soulseek ? 'connected' : 'disconnected';
        })
        .catch(error => console.error('Error fetching status:', error));
}

// Initialize when page loads
document.addEventListener('DOMContentLoaded', function() {
    // Update status immediately
    updateStatus();
    
    // Update status every 5 seconds
    setInterval(updateStatus, 5000);
    
    console.log('SoulSync Web UI loaded successfully!');
});