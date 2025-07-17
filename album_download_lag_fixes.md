# Album Download Lag Fixes - Implementation Guide

## Problem Summary

The newMusic application experiences significant UI lag in two scenarios:
1. **Album Download Button Lag**: Clicking album download buttons causes 1+ second UI freeze
2. **Instant Error Cascade**: Albums that fail immediately (network errors, unavailable tracks) cause 1+ second lag due to 15+ errors processing individually

## Root Causes Identified

### 1. Album Download Processing
- **Location**: `_handle_matched_album_download()` (line ~7200)
- **Issue**: Sequential processing with `time.sleep(0.1)` per track = 1.5s+ for 15-track album
- **Problem**: All processing happens on main UI thread

### 2. UI Queue Updates
- **Location**: `DownloadQueue.add_download_item()` (line ~4436)
- **Issue**: Each track creates individual UI widget with immediate layout updates
- **Problem**: 15 tracks = 15 individual UI operations

### 3. Completion Cascade
- **Location**: Status update functions (line ~9880, ~9925, ~9979)
- **Issue**: When tracks complete simultaneously, each calls `move_to_finished()` individually
- **Problem**: 15 simultaneous completions = 15 individual UI transitions

### 4. Instant Error Cascade
- **Location**: `on_download_failed()` (line ~9096)
- **Issue**: Album failures trigger immediate individual error processing
- **Problem**: 15 instant errors = 15 individual `move_to_finished()` calls

## Solution Architecture

### 1. UI Batch Mode System
**Purpose**: Defer UI updates until all items are ready, then apply in single operation

**Implementation**:
```python
class DownloadQueue:
    def start_batch_mode(self):
        """Start collecting items without UI updates"""
        self._batch_mode = True
        self._batch_items = []
    
    def _add_download_item_batch(self, ...):
        """Add item to batch without immediate UI rendering"""
        item = CompactDownloadItem(...)
        self.download_items.append(item)
        self._batch_items.append(item)
        return item
    
    def end_batch_mode(self):
        """Apply all UI updates at once"""
        for item in self._batch_items:
            self.queue_layout.insertWidget(insert_index, item)
        self.update_queue_count()  # Single count update
        self._batch_items = []
```

**Integration**: Modify `add_download_item()` to check batch mode and route accordingly.

### 2. Enhanced Album Download Processing
**Purpose**: Move album processing to background with batch UI updates

**Implementation**:
```python
def _handle_matched_album_download_v2(self, album_result, artist):
    """Optimized non-blocking album download"""
    # Prepare tracks (no blocking operations)
    prepared_tracks = []
    for track in album_result.tracks:
        # Set metadata without sleep delays
        track.matched_artist = artist
        track.album = clean_album_title
        prepared_tracks.append(track)
    
    def batch_download_tracks():
        # Start UI batch mode
        self.download_queue.start_batch_mode()
        
        try:
            # Process in smaller batches (3 tracks at a time)
            for i in range(0, len(prepared_tracks), 3):
                batch = prepared_tracks[i:i + 3]
                for track in batch:
                    self._start_download_with_artist(track, artist)
                # Reduced sleep: 0.05s between batches
                if i + 3 < len(prepared_tracks):
                    time.sleep(0.05)
        finally:
            # Apply all UI updates at once
            self.download_queue.end_batch_mode()
    
    # Submit to background thread pool
    self._optimized_api_pool.submit(batch_download_tracks)
    
    # Return immediately (no blocking)
    print(f"🚀 Album download queued in background")
```

### 3. Completion Batching System
**Purpose**: Collect completions and process them in batches rather than individually

**Implementation**:
```python
class DownloadsPage:
    def __init__(self):
        # Completion batching state
        self._completion_batch_mode = False
        self._completion_batch_items = []
        self._completion_batch_timer = QTimer()
        self._completion_batch_timer.setSingleShot(True)
        self._completion_batch_timer.timeout.connect(self._process_completion_batch)
    
    def _start_completion_batch_mode(self):
        """Activate completion batching for album downloads"""
        self._completion_batch_mode = True
        self._completion_batch_items = []
        self._completion_batch_timer.start(2000)  # 2-second collection window
    
    def _add_to_completion_batch(self, download_item):
        """Add item to completion batch instead of immediate processing"""
        if self._completion_batch_mode:
            self._completion_batch_items.append(download_item)
            return True  # Item batched
        return False     # Process immediately
    
    def _process_completion_batch(self):
        """Process all batched completions at once"""
        self.download_queue.start_batch_mode()  # Enable UI batching
        
        try:
            for download_item in self._completion_batch_items:
                self.download_queue.move_to_finished(download_item)
        finally:
            self.download_queue.end_batch_mode()  # Apply UI updates
            self._completion_batch_mode = False
            self._completion_batch_items = []
```

**Integration**: Modify status update completion handling:
```python
# In status update functions (lines ~9880, ~9925, ~9979)
# Replace direct move_to_finished calls with:
if not self._add_to_completion_batch(download_item):
    self.download_queue.move_to_finished(download_item)
```

### 4. Error Batching System
**Purpose**: Handle instant failure cascades by batching error processing

**Implementation**:
```python
class DownloadsPage:
    def __init__(self):
        # Error batching state
        self._error_batch_mode = False
        self._error_batch_items = []
        self._error_batch_timer = QTimer()
        self._error_batch_timer.setSingleShot(True)
        self._error_batch_timer.timeout.connect(self._process_error_batch)
    
    def _start_completion_batch_mode(self):
        """Modified to also enable error batching"""
        # ... existing completion batching code ...
        
        # Also enable error batching for instant failures
        self._error_batch_mode = True
        self._error_batch_items = []
        self._error_batch_timer.start(500)  # 500ms window for errors
    
    def _add_to_error_batch(self, download_item):
        """Add item to error batch instead of immediate processing"""
        if self._error_batch_mode:
            self._error_batch_items.append(download_item)
            return True
        return False
    
    def _process_error_batch(self):
        """Process all batched errors at once"""
        self.download_queue.start_batch_mode()
        
        try:
            for download_item in self._error_batch_items:
                self.download_queue.move_to_finished(download_item)
        finally:
            self.download_queue.end_batch_mode()
            self._error_batch_mode = False
            self._error_batch_items = []
```

**Integration**: Modify `on_download_failed()`:
```python
def on_download_failed(self, error_msg, download_item):
    download_item.status = "failed"
    download_item.progress = 0
    
    # Use error batch if active to prevent instant cascade
    if not self._add_to_error_batch(download_item):
        self.download_queue.move_to_finished(download_item)
```

## Implementation Plan

### Step 1: UI Batch Mode System
1. Add batch mode state to `DownloadQueue.__init__()`
2. Create `start_batch_mode()`, `_add_download_item_batch()`, `end_batch_mode()` methods
3. Modify `add_download_item()` to check batch mode

### Step 2: Enhanced Album Processing
1. Create `_handle_matched_album_download_v2()` method
2. Modify existing album download to route to v2 when optimizations enabled
3. Add background thread processing with batch mode integration

### Step 3: Completion Batching
1. Add completion batch state to `DownloadsPage.__init__()`
2. Create completion batching methods
3. Add `_start_completion_batch_mode()` call to album downloads
4. Modify status update functions to use completion batching

### Step 4: Error Batching
1. Add error batch state to `DownloadsPage.__init__()`
2. Create error batching methods
3. Integrate error batching with completion batch activation
4. Modify `on_download_failed()` to use error batching
5. Update status update failure handling

### Step 5: Integration Points
1. **Album Download Start**: Call `_start_completion_batch_mode()`
2. **Status Updates**: Use batching for completed/cancelled/failed states
3. **Cleanup**: Process pending batches in `cleanup_resources()`

## Key Files and Locations

### Primary File: `/ui/pages/downloads.py`

**DownloadQueue Class** (~line 4326):
- Add batch mode methods
- Modify `add_download_item()`

**DownloadsPage Class** (~line 4802):
- Add batch state variables to `__init__()` (~line 4880)
- Create completion batching methods (~line 10800+)
- Create error batching methods (~line 10850+)
- Modify `_handle_matched_album_download_v2()` (~line 7224)
- Modify `on_download_failed()` (~line 9096)
- Modify status update completion handling (~lines 9880, 9925, 9979)

## Expected Results

### Performance Improvements:
1. **Album Download**: Near-instant button response (background processing)
2. **Completion Processing**: Batch transitions instead of individual UI updates
3. **Error Handling**: 500ms batched processing instead of instant cascade
4. **UI Responsiveness**: Smooth interaction during all album operations

### Functional Preservation:
- All existing download functionality maintained
- Complete API compatibility preserved
- Error handling and cleanup operations intact
- Progress tracking and status updates continue working

## Testing Scenarios

1. **Large Album Download**: 15+ track album with successful downloads
2. **Network Failure Album**: Album that fails immediately due to network issues
3. **Mixed Results**: Album with some successes and some failures
4. **Single Track**: Ensure individual downloads still work without batching
5. **Rapid Multiple Albums**: Multiple album downloads in quick succession

## Rollback Strategy

If issues occur:
1. Set feature flags to disable optimizations
2. Comment out batch mode routing in `add_download_item()`
3. Remove batch mode checks in status update functions
4. Restore original album download method routing

The implementation preserves all original functionality while adding performance optimizations that can be easily disabled if needed.