# Album Download Tracking Implementation - newMusic Application

## Overview

This document details the complete implementation journey of live album download tracking in the newMusic application. The goal was to provide real-time progress updates for album downloads in the artists page, showing users exactly how many tracks have completed downloading as each individual file finishes.

## The Challenge

The artists.py page had a partial implementation that displayed basic progress but lacked the sophisticated live status tracking that worked perfectly on the downloads.py and sync.py pages. Users would see albums stuck on "preparing" status without any indication of actual download progress.

## Initial Analysis

### Working Reference Implementation

The foundation came from analyzing `download-tracking-analysis.md`, which documented how the downloads and sync pages achieved reliable live status tracking through:

1. **Background Worker Threads**: `StatusProcessingWorker` and `SyncStatusProcessingWorker`
2. **API Polling**: Regular status checks via `soulseek_client.get_all_downloads()`
3. **ID-based Matching**: Primary matching by slskd download IDs with filename fallback
4. **Grace Period Logic**: 3-poll grace period for missing downloads before marking as failed
5. **Cleanup Worker Management**: Handling the cleanup worker that removes completed downloads from the API

### Artists Page Current State

The artists.py file had:
- Basic album download initiation (`start_album_download()`)
- Simple progress display logic
- Incomplete integration with the proven tracking system
- Wrong worker usage (trying to use `SyncStatusProcessingWorker` incorrectly)

## Problem Identification

### Core Issues Discovered

1. **Incorrect Worker Usage**: Artists page was trying to repurpose `SyncStatusProcessingWorker` which had different data structures
2. **Missing Data Structure Mapping**: No proper mapping between album downloads and slskd API responses
3. **Inadequate Status Processing**: Missing the sophisticated status resolution logic from working pages
4. **Incomplete Integration**: No connection to the proven download tracking infrastructure

## Implementation Phase 1: Foundation

### Created Dedicated AlbumStatusProcessingWorker

```python
class AlbumStatusProcessingWorker(QRunnable):
    """Background worker for processing album download status updates"""
    
    def __init__(self, soulseek_client, album_downloads):
        super().__init__()
        self.soulseek_client = soulseek_client
        self.album_downloads = album_downloads
        self.signals = WorkerSignals()
    
    def run(self):
        try:
            # Create async event loop for API calls
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            # Get all current downloads from slskd API
            all_downloads = loop.run_until_complete(
                self.soulseek_client.get_all_downloads()
            )
            
            # Process each album's download status
            results = []
            for album_id, album_info in self.album_downloads.items():
                # Match downloads and determine status
                # [Status processing logic...]
```

**Key Features:**
- Dedicated to album download tracking
- Proper async handling for API calls
- Status resolution matching the working pages
- Results structured for album-specific updates

### Enhanced poll_album_download_statuses()

```python
def poll_album_download_statuses(self):
    """Poll download statuses for all active albums using dedicated worker"""
    if self._is_album_status_update_running:
        return
    
    if not self.album_downloads:
        return
    
    self._is_album_status_update_running = True
    
    # Create worker with current album download data
    worker = AlbumStatusProcessingWorker(
        soulseek_client=self.soulseek_client,
        album_downloads=dict(self.album_downloads)  # Snapshot for thread safety
    )
    
    # Connect signals
    worker.signals.completed.connect(self._handle_album_status_updates)
    worker.signals.error.connect(lambda e: print(f"Album Status Worker Error: {e}"))
    
    # Start in thread pool
    self.album_status_processing_pool.start(worker)
```

### Fixed _handle_album_status_updates()

```python
def _handle_album_status_updates(self, results):
    """Process album status results from background worker"""
    try:
        albums_to_update = set()
        albums_completed = set()
        
        for result in results:
            album_id = result['album_id']
            download_id = result['download_id'] 
            status = result['status']
            progress = result['progress']
            
            # [Robust status processing logic...]
```

## First Test: Download ID Mismatch

### Problem Encountered

User feedback: "nope that did not resolve the issue. im watching downloads finish in the the slskd webapi but the album i chose just says 'preparing'"

### Root Cause Analysis

The issue was **composite vs real download IDs**:
- **Artists page tracked**: Composite IDs like `"recovery8655_Taylor Swift-Fearless..."`
- **slskd API returned**: Real UUIDs like `"6bff31cd-07eb-4757-aae7-86fe6d4e847f"`

These IDs never matched, so status updates never found the corresponding downloads.

## Implementation Phase 2: ID Resolution

### Added Real Download ID Resolution

```python
def _get_real_download_id(self, composite_id, all_downloads):
    """Convert composite download ID to real slskd download ID"""
    # Extract components from composite ID
    parts = composite_id.split('_')
    if len(parts) >= 3:
        username = parts[0]
        filename_part = '_'.join(parts[1:-2])  # Rejoin middle parts
        
        # Find matching download in API response
        for download in all_downloads:
            if (download.username == username and 
                filename_part.lower() in download.filename.lower()):
                return download.id
    
    return None
```

### Enhanced Download ID Tracking Integration

```python
# In album download initiation
download_id = self.soulseek_client.download(...)
if download_id:
    # Store real ID instead of composite
    album_info['active_downloads'].append(download_id)
```

## Second Test: Still Not Working

### Problem Encountered

User feedback: "idk i think its worse? now it only says preparing again" with detailed logs showing all downloads "missing from API"

### Enhanced Debugging

The logs revealed:
```
🔔 Downloads page notified completion of: 6bff31cd-07eb-4757-aae7-86fe6d4e847f
🎵 Album 'taylor_swift_fearless_12345': Checking download a1165c82-dfba-492c-b584-dca104fb3f81
❌ Download a1165c82-dfba-492c-b584-dca104fb3f81 not found in API transfers
```

The notification system was working but the IDs still didn't match between tracking and API.

## Implementation Phase 3: The Breakthrough

### Core Problem Identification

The **cleanup worker** was removing completed downloads from the API before the artists page could detect completion. The downloads page was correctly updating its items with real IDs, but the artists page was still tracking the original composite IDs.

### Two-Pronged Solution

#### 1. Enhanced Queue Scanning with Real ID Resolution

```python
def update_active_downloads_from_queue(self):
    """Scan download queue items to get real download IDs"""
    if not hasattr(self, 'downloads_page') or not self.downloads_page:
        return
    
    # Get current download items from both active and finished queues
    active_items = getattr(self.downloads_page.download_queue.active_queue, 'download_items', [])
    finished_items = getattr(self.downloads_page.download_queue.finished_queue, 'download_items', [])
    all_items = list(active_items) + list(finished_items)
    
    for album_id, album_info in self.album_downloads.items():
        matching_downloads = []
        completed_count = 0
        
        for item in all_items:
            # Check if this download item belongs to this album
            if self._is_download_item_for_album(item, album_info):
                current_id = getattr(item, 'download_id', None)
                
                # Use the download ID directly from the item (should be the real one)
                if current_id and current_id != 'NO_ID':
                    # Check if this item is in finished items (completed)
                    if item in finished_items:
                        completed_count += 1
                    else:
                        # It's an active download - use the current ID
                        matching_downloads.append(current_id)
        
        # Update album tracking with real IDs and completion count
        album_info['active_downloads'] = matching_downloads
        album_info['completed_tracks'] = completed_count
```

#### 2. Direct Notification System

Modified `downloads.py` to notify the artists page directly when downloads complete **before** the cleanup worker runs:

```python
def move_to_finished(self, download_item):
    """Move a download item from active to finished queue"""
    if download_item in self.active_queue.download_items:
        # Notify artists page of completion BEFORE moving to finished (before cleanup)
        if (download_item.status == 'completed' and 
            hasattr(download_item, 'download_id') and 
            download_item.download_id):
            # Navigate to artists page and notify
            main_window = self.find_main_window()
            if main_window and hasattr(main_window, 'artists_page'):
                main_window.artists_page.notify_download_completed(
                    download_item.download_id, download_item
                )
```

### Smart Notification Handler

```python
def notify_download_completed(self, download_id, download_item=None):
    """Called by downloads page when a download completes (before cleanup)"""
    # Find which album this belongs to - try multiple approaches
    target_album_id = None
    
    # Approach 1: Direct ID match
    for album_id, album_info in self.album_downloads.items():
        if download_id in album_info.get('active_downloads', []):
            target_album_id = album_id
            break
    
    # Approach 2: Match by download item attributes
    if not target_album_id and download_item:
        for album_id, album_info in self.album_downloads.items():
            if self._is_download_from_album(download_item, album_info):
                target_album_id = album_id
                break
    
    # Approach 3: Replace composite ID with real ID
    if not target_album_id and download_item:
        item_title = getattr(download_item, 'title', '')
        for album_id, album_info in self.album_downloads.items():
            active_downloads = album_info.get('active_downloads', [])
            for active_id in active_downloads[:]:
                if item_title and item_title.lower() in active_id.lower():
                    # Replace composite with real ID
                    album_info['active_downloads'].remove(active_id)
                    album_info['active_downloads'].append(download_id)
                    target_album_id = album_id
                    break
    
    if target_album_id:
        # Update album progress immediately
        album_info = self.album_downloads[target_album_id]
        album_info['completed_tracks'] += 1
        if download_id in album_info['active_downloads']:
            album_info['active_downloads'].remove(download_id)
        
        # Update UI
        self.update_album_card_progress(target_album_id)
```

## Final Issue: Double Counting

### Problem Encountered

User feedback: "every time a download finishes it seems to be marked twice so when the first track finished downloading it jumped from 0/19 to 2/19"

### Root Cause

Both the notification system AND the regular polling were incrementing the completed count:

1. **Notification system** (line 2403): `album_info['completed_tracks'] += 1`
2. **Regular polling** (line 2274): `album_info['completed_tracks'] += 1`

### Solution: Duplicate Detection

Added completion tracking to prevent double counting:

```python
def _mark_download_as_completed(self, download_id):
    """Mark a download as completed to handle cleanup detection"""
    if download_id:
        self.completed_downloads.add(download_id)

def _was_download_previously_completed(self, download_id):
    """Check if a download was previously marked as completed"""
    return download_id in self.completed_downloads

def notify_download_completed(self, download_id, download_item=None):
    """Called by downloads page when a download completes (before cleanup)"""
    # Check if already processed to prevent double counting
    if self._was_download_previously_completed(download_id):
        print(f"⏭️ Download {download_id} already processed, skipping")
        return
    
    # Mark as completed and process...
    self._mark_download_as_completed(download_id)
    # [Rest of processing...]

# Also in regular polling:
if status == 'completed':
    # Only process if not already handled by notification system
    if not self._was_download_previously_completed(download_id):
        # [Process completion...]
```

## Final Architecture

### Complete System Flow

1. **Album Download Initiated**
   - User clicks download button for album
   - Real download IDs stored in `album_info['active_downloads']`
   - Album card shows "Downloading 0/X tracks"

2. **Live Tracking via Dual System**
   - **Primary: Direct Notification**
     - Downloads page calls `notify_download_completed()` immediately when track finishes
     - Immediate UI update with completion count increment
     - Track marked as completed to prevent duplicate counting
   
   - **Fallback: Polling System**
     - Background worker polls slskd API every 2 seconds
     - Checks for completions not caught by notification
     - Duplicate detection prevents double counting

3. **Progress Display**
   - Album cards show real-time updates: "Downloading 1/19 tracks (5%)"
   - Progress bar fills incrementally
   - Final state: "Downloaded 19/19 tracks (100%)"

### Key Components

#### Data Structures
```python
# Album tracking
self.album_downloads = {
    'album_id': {
        'spotify_album': SpotifyAlbum,
        'album_result': SearchResult, 
        'active_downloads': [real_download_ids],
        'completed_tracks': int,
        'total_tracks': int
    }
}

# Completion tracking
self.completed_downloads = set()  # Set of completed download IDs
```

#### Worker Classes
- **`AlbumStatusProcessingWorker`**: Background API polling
- **Notification System**: Direct completion callbacks
- **Duplicate Detection**: Prevents double counting

#### Integration Points
- **`downloads.py:move_to_finished()`**: Triggers notifications
- **`artists.py:notify_download_completed()`**: Handles completions
- **`artists.py:poll_album_download_statuses()`**: Fallback polling
- **Timer System**: 2-second polling interval

## Key Insights

### Critical Success Factors

1. **Understanding the Cleanup Worker Problem**
   - The slskd cleanup worker removes completed downloads from the API
   - This breaks traditional polling-only approaches
   - Solution: Catch completions BEFORE cleanup via direct notification

2. **ID Lifecycle Management**
   - Downloads start with composite IDs from search results
   - slskd assigns real UUIDs when downloads begin
   - Must track this transition and update references

3. **Duplicate Prevention**
   - Multiple systems can detect the same completion
   - Completion tracking set prevents double counting
   - Prioritize fast notification over slower polling

4. **Thread Safety**
   - Background workers need data snapshots
   - UI updates must happen on main thread
   - Signal/slot system bridges thread boundaries safely

### Performance Optimizations

- **Background Processing**: All API calls in worker threads
- **Adaptive Updates**: Only update UI when status actually changes
- **Efficient Matching**: Direct ID lookups with fallback strategies
- **Minimal API Calls**: Leverage notification system to reduce polling

## Testing Results

### Before Implementation
- Albums stuck on "preparing" status
- No progress indication during downloads
- User confusion about download state

### After Implementation
- Real-time progress: "Downloading 1/19 tracks (5%)"
- Immediate updates as each track completes
- Accurate completion detection and final state
- Smooth increments (1/19 → 2/19 → 3/19) without double counting

## Code Files Modified

### `/ui/pages/artists.py`
- Added `AlbumStatusProcessingWorker` class (lines 243-418)
- Enhanced `poll_album_download_statuses()` method
- Fixed `_handle_album_status_updates()` for robust result processing
- Added completion tracking with `notify_download_completed()` method
- Enhanced `update_active_downloads_from_queue()` with real ID resolution

### `/ui/pages/downloads.py`
- Modified `move_to_finished()` method to notify artists page before cleanup
- Removed redundant notification code after enhanced system worked

### Key Integration Functions
- `notify_download_completed()`: Direct completion notification
- `_mark_download_as_completed()`: Completion tracking
- `_was_download_previously_completed()`: Duplicate prevention
- `update_album_card_progress()`: UI progress updates

## Conclusion

The album download tracking implementation required solving a complex interaction between multiple systems:

1. **API Lifecycle**: Understanding when downloads appear/disappear from slskd API
2. **ID Management**: Tracking the transition from composite to real download IDs  
3. **Cleanup Timing**: Working around the cleanup worker that removes completed downloads
4. **Duplicate Detection**: Preventing multiple systems from double-counting completions
5. **Thread Safety**: Coordinating between background workers and UI updates

The final solution combines the reliability of the proven download tracking architecture with album-specific enhancements, providing users with the live progress tracking they needed while maintaining system performance and accuracy.

**Result**: Users now see real-time album download progress that updates immediately as each track completes, matching the quality and reliability of the existing downloads page tracking system.