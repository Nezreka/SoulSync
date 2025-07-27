# Download Tracking Analysis - newMusic Application

## Overview

This document provides a comprehensive analysis of how the newMusic application accurately tracks downloads through the slskd API. The system implements sophisticated polling, status resolution, and queue management to provide real-time download status updates while maintaining UI responsiveness.

## Architecture Overview

The download tracking system follows a multi-layered architecture:

1. **API Layer**: `SoulseekClient` interfaces with slskd daemon
2. **Worker Layer**: Background threads handle expensive status processing
3. **UI Layer**: `DownloadsPage` and `SyncPlaylistModal` provide user interfaces
4. **Queue Management**: Active and finished download queue management

## Core Data Models

### DownloadStatus (`/core/soulseek_client.py:189`)

```python
@dataclass
class DownloadStatus:
    id: str                    # Unique download identifier from slskd
    filename: str              # Full path of the downloading file
    username: str              # Soulseek user providing the file
    state: str                 # Current slskd state (e.g., "InProgress", "Completed")
    progress: float            # Download progress percentage (0.0-100.0)
    size: int                  # Total file size in bytes
    transferred: int           # Bytes transferred so far
    speed: int                 # Average download speed
    time_remaining: Optional[int] = None  # Estimated time remaining
```

## Primary API Functions

### SoulseekClient.get_all_downloads() (`/core/soulseek_client.py:789`)

**Purpose**: Retrieves all active downloads from slskd API

**Input**: None

**Output**: `List[DownloadStatus]`

**Process**:
1. Makes GET request to `/api/v0/transfers/downloads`
2. Parses nested response structure: `[{"username": "user", "directories": [{"files": [...]}]}]`
3. Extracts progress from state strings or `progress` field
4. Creates `DownloadStatus` objects for each file

**Key Implementation**:
```python
async def get_all_downloads(self) -> List[DownloadStatus]:
    response = await self._make_request('GET', 'transfers/downloads')
    downloads = []
    
    for user_data in response:
        username = user_data.get('username', '')
        directories = user_data.get('directories', [])
        
        for directory in directories:
            files = directory.get('files', [])
            
            for file_data in files:
                # Parse progress
                progress = 0.0
                if file_data.get('state', '').lower().startswith('completed'):
                    progress = 100.0
                elif 'progress' in file_data:
                    progress = float(file_data.get('progress', 0.0))
                
                status = DownloadStatus(
                    id=file_data.get('id', ''),
                    filename=file_data.get('filename', ''),
                    username=username,
                    state=file_data.get('state', ''),
                    progress=progress,
                    size=file_data.get('size', 0),
                    transferred=file_data.get('bytesTransferred', 0),
                    speed=file_data.get('averageSpeed', 0),
                    time_remaining=file_data.get('timeRemaining')
                )
                downloads.append(status)
```

### SoulseekClient.get_download_status() (`/core/soulseek_client.py:764`)

**Purpose**: Retrieves status for a specific download by ID

**Input**: `download_id: str`

**Output**: `Optional[DownloadStatus]`

**Process**:
1. Makes GET request to `/api/v0/transfers/downloads/{download_id}`
2. Creates single `DownloadStatus` object
3. Returns `None` if download not found

### SoulseekClient.clear_all_completed_downloads() (`/core/soulseek_client.py:910`)

**Purpose**: Removes all completed downloads from slskd backend

**Input**: None

**Output**: `bool` (success/failure)

**Process**:
1. Makes DELETE request to `/api/v0/transfers/downloads/all/completed`
2. Clears downloads with "Completed", "Cancelled", or "Failed" status
3. Used for backend cleanup to prevent API response bloat

## Downloads Page Status Tracking

### Main Status Update Function (`/ui/pages/downloads.py:9504`)

**Function**: `update_download_status()`

**Purpose**: Primary status update coordinator for the downloads page

**Input**: None (uses instance state)

**Output**: None (triggers UI updates via signals)

**Process**:
1. Checks if status update is already running (prevents concurrent updates)
2. Filters for active downloads only
3. Creates `StatusProcessingWorker` with current download items
4. Connects worker completion signal to `_handle_processed_status_updates()`
5. Starts worker in thread pool

**Key Implementation**:
```python
def update_download_status(self):
    if self._is_status_update_running or not self.soulseek_client:
        return
    
    active_items = [item for item in self.download_queue.active_queue.download_items]
    if not active_items:
        self._is_status_update_running = False
        return
    
    self._is_status_update_running = True
    
    worker = StatusProcessingWorker(
        soulseek_client=self.soulseek_client,
        download_items=active_items
    )
    
    worker.signals.completed.connect(self._handle_processed_status_updates)
    worker.signals.error.connect(lambda e: print(f"Status Worker Error: {e}"))
    
    self.status_processing_pool.start(worker)
```

### Enhanced Status Update Function (`/ui/pages/downloads.py:9206`)

**Function**: `update_download_status_v2()`

**Purpose**: Optimized version with adaptive polling and thread safety

**Input**: None

**Output**: None

**Key Improvements**:
- Thread-safe access to download items
- Adaptive polling frequency based on download activity
- Enhanced transfer matching with duplicate prevention
- Improved error handling and state consistency

### Background Status Processing Worker (`/ui/pages/downloads.py:119`)

**Class**: `StatusProcessingWorker`

**Purpose**: Performs expensive status processing in background thread

**Input**: 
- `soulseek_client`: API client instance
- `download_items`: List of download items to check

**Output**: Emits `completed` signal with list of status update results

**Process**:
1. Creates async event loop in background thread
2. Calls `soulseek_client._make_request('GET', 'transfers/downloads')`
3. Flattens nested transfer data structure
4. Matches downloads by ID, falls back to filename matching
5. Determines new status based on slskd state
6. Returns structured results for main thread processing

**Key Status Mapping**:
```python
# Terminal states checked first (critical for correct status determination)
if 'Cancelled' in state or 'Canceled' in state:
    new_status = 'cancelled'
elif 'Failed' in state or 'Errored' in state:
    new_status = 'failed'
elif 'Completed' in state or 'Succeeded' in state:
    new_status = 'completed'
elif 'InProgress' in state:
    new_status = 'downloading'
else:
    new_status = 'queued'
```

### Status Update Result Processing (`/ui/pages/downloads.py:9327`)

**Function**: `_handle_processed_status_updates()`

**Purpose**: Applies background worker results to UI on main thread

**Input**: `results: List[dict]` - Status update results from worker

**Output**: None (updates UI state)

**Process**:
1. Iterates through results from background worker
2. Finds corresponding download items by widget ID
3. Updates download item status and progress
4. Handles queue transitions for completed downloads
5. Updates tab counts and progress indicators

## Sync Page Status Tracking

### Sync Status Polling (`/ui/pages/sync.py:4099`)

**Function**: `poll_all_download_statuses()`

**Purpose**: Status update coordinator for sync playlist modal

**Input**: None (uses `self.active_downloads`)

**Output**: None

**Process**:
1. Creates snapshot of active download data for thread safety
2. Filters downloads that have valid slskd results
3. Creates `SyncStatusProcessingWorker` with download data
4. Starts worker in dedicated thread pool

**Key Implementation**:
```python
def poll_all_download_statuses(self):
    if self._is_status_update_running or not self.active_downloads:
        return
    self._is_status_update_running = True
    
    items_to_check = []
    for d in self.active_downloads:
        if d.get('slskd_result') and hasattr(d['slskd_result'], 'filename'):
            items_to_check.append({
                'widget_id': d['download_index'], 
                'download_id': d.get('download_id'),
                'file_path': d['slskd_result'].filename,
                'api_missing_count': d.get('api_missing_count', 0)
            })
    
    worker = SyncStatusProcessingWorker(
        self.parent_page.soulseek_client, 
        items_to_check
    )
    
    worker.signals.completed.connect(self._handle_processed_status_updates)
    self.download_status_pool.start(worker)
```

### Sync Background Status Worker (`/ui/pages/sync.py:348`)

**Class**: `SyncStatusProcessingWorker`

**Purpose**: Background status processing specific to sync modal

**Input**: 
- `soulseek_client`: API client
- `download_items_data`: List of download data snapshots

**Output**: Emits results with status updates

**Key Features**:
- Enhanced transfer data parsing (handles both nested and flat structures)
- Grace period for missing downloads (3 polls before marking as failed)
- Automatic download ID correction via filename matching
- Comprehensive error state detection

**Enhanced Transfer Parsing**:
```python
# Handles multiple response formats from slskd API
all_transfers = []
for user_data in transfers_data:
    # Check for files directly under user object
    if 'files' in user_data and isinstance(user_data['files'], list):
        all_transfers.extend(user_data['files'])
    # Also check for files nested inside directories
    if 'directories' in user_data and isinstance(user_data['directories'], list):
        for directory in user_data['directories']:
            if 'files' in directory and isinstance(directory['files'], list):
                all_transfers.extend(directory['files'])
```

### Sync Status Result Processing (`/ui/pages/sync.py:4138`)

**Function**: `_handle_processed_status_updates()`

**Purpose**: Processes sync worker results and triggers appropriate actions

**Input**: `results: List[dict]` - Worker results

**Output**: None

**Process**:
1. Creates lookup map for active downloads
2. Updates download IDs when corrected by filename matching
3. Handles terminal states (completed, failed, cancelled)
4. Manages retry logic for failed downloads
5. Updates missing count tracking for grace period logic

## Status Polling and Timers

### Downloads Page Timer Setup (`/ui/pages/downloads.py:4863`)

```python
self.download_status_timer = QTimer()
self.download_status_timer.timeout.connect(self.update_download_status)
self.download_status_timer.start(1000)  # Poll every 1 second
```

### Sync Page Timer Setup (`/ui/pages/sync.py:3529`)

```python
self.download_status_timer = QTimer(self)
self.download_status_timer.timeout.connect(self.poll_all_download_statuses)
self.download_status_timer.start(2000)  # Poll every 2 seconds
```

### Adaptive Polling (`/ui/pages/downloads.py:9183`)

**Function**: `_update_adaptive_polling()`

**Purpose**: Adjusts polling frequency based on download activity

**Logic**:
- **Active downloads present**: 500ms intervals
- **No active downloads**: 5000ms intervals
- Optimizes performance by reducing unnecessary API calls

## Download Matching Strategies

### Primary: ID-Based Matching

The system primarily matches downloads using the unique ID assigned by slskd:

```python
# Direct ID lookup
matching_transfer = transfers_by_id.get(item_data['download_id'])
```

### Fallback: Filename-Based Matching

When ID matching fails, the system falls back to filename comparison:

```python
if not matching_transfer:
    expected_basename = os.path.basename(item_data['file_path']).lower()
    for t in all_transfers:
        api_basename = os.path.basename(t.get('filename', '')).lower()
        if api_basename == expected_basename:
            matching_transfer = t
            break
```

### Enhanced Matching in V2 (`/ui/pages/downloads.py:9278`)

**Function**: `_find_matching_transfer_v2()`

**Features**:
- Prevents duplicate matches across multiple downloads
- Tracks already-matched transfer IDs
- Maintains match consistency across polling cycles

## Grace Period and Missing Download Handling

### Missing Download Grace Period

The system implements a 3-poll grace period before marking downloads as failed:

```python
# Grace period logic
item_data['api_missing_count'] = item_data.get('api_missing_count', 0) + 1
if item_data['api_missing_count'] >= 3:
    print(f"❌ Download failed (missing from API after 3 checks): {expected_filename}")
    payload = {'widget_id': item_data['widget_id'], 'status': 'failed'}
```

**Purpose**: Handles temporary API inconsistencies and network issues

## Queue Management and State Transitions

### Download Queue Structure

The system maintains separate queues:
- **Active Queue**: Currently downloading or queued items
- **Finished Queue**: Completed, cancelled, or failed downloads

### State Transition Function (`/ui/pages/downloads.py:315`)

**Function**: `atomic_state_transition()`

**Purpose**: Thread-safe status updates with callback support

**Input**:
- `download_item`: Item to update
- `new_status`: Target status
- `callback`: Optional callback function

**Process**:
1. Captures old status
2. Updates item status atomically
3. Calls callback with old/new status if provided

### Queue Movement Logic

Downloads transition between queues based on status:

```python
# Terminal states move to finished queue
if new_status in ['completed', 'cancelled', 'failed']:
    self.download_queue.move_to_finished(download_item)
    self.download_queue.active_queue.remove_item(download_item)
```

## Backend Cleanup System

### Periodic Cleanup (`/ui/pages/downloads.py:9534`)

**Function**: `_periodic_cleanup_check()`

**Purpose**: Prevents slskd backend from accumulating completed downloads

**Process**:
1. Identifies downloads needing cleanup from previous polling cycle
2. Performs bulk cleanup for standard completed downloads
3. Handles individual cleanup for errored downloads
4. Prepares cleanup list for next cycle

### Cleanup Categories

**Bulk Cleanup States**: 
- 'Completed, Succeeded'
- 'Completed, Cancelled' 
- 'Cancelled'
- 'Canceled'

**Individual Cleanup States**:
- 'Completed, Errored'
- 'Failed'
- 'Errored'

### Backend Cleanup Execution (`/ui/pages/downloads.py:9617`)

**Function**: `_cleanup_backend_downloads()`

**Process**:
1. Runs in background thread to avoid UI blocking
2. Calls `soulseek_client.clear_all_completed_downloads()`
3. Logs cleanup results
4. Handles cleanup failures gracefully

## Error Handling and Resilience

### Connection Failure Handling

All API functions include comprehensive error handling:

```python
try:
    response = await self._make_request('GET', 'transfers/downloads')
    if not response:
        return []
    # Process response...
except Exception as e:
    logger.error(f"Error getting downloads: {e}")
    return []
```

### Thread Safety Measures

- **Status Update Locks**: Prevent concurrent status processing
- **Queue Consistency Locks**: Ensure atomic queue operations  
- **Worker Thread Pools**: Manage background thread lifecycle

### Graceful Degradation

- System continues functioning when API calls fail
- Missing downloads handled with grace period
- UI remains responsive during network issues

## Performance Optimizations

### Background Threading

All expensive operations run in background threads:
- API calls to slskd
- Status processing and matching
- Backend cleanup operations

### Efficient Data Structures

- Transfer lookup dictionaries for O(1) matching
- Set-based duplicate tracking
- Minimal data copying between threads

### Adaptive Polling

Polling frequency adjusts based on activity:
- High frequency when downloads active
- Low frequency when idle
- Immediate updates for user actions

## Integration Points

### Key Function Call Chains

1. **Timer → Status Update → Worker → Results → UI Update**
2. **Download Start → Queue Add → Status Tracking → Completion → Queue Move**
3. **Periodic Cleanup → Backend Query → Bulk Clear → UI Sync**

### Critical State Synchronization

- **UI Thread**: Handles all widget updates
- **Background Threads**: Perform API operations
- **Signal/Slot System**: Bridges thread boundaries safely

## Summary

The newMusic download tracking system provides robust, real-time status monitoring through:

1. **Layered Architecture**: Clean separation between API, processing, and UI layers
2. **Background Processing**: Non-blocking status updates via worker threads
3. **Intelligent Matching**: ID-based primary matching with filename fallback
4. **Grace Period Handling**: Tolerance for temporary API inconsistencies
5. **Adaptive Polling**: Performance optimization based on download activity
6. **Comprehensive Cleanup**: Prevents backend bloat through periodic maintenance
7. **Thread Safety**: Consistent state management across concurrent operations

This architecture ensures accurate download tracking while maintaining responsive UI performance and handling various edge cases and error conditions gracefully.