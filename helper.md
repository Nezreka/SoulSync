# Downloads.py Deep Dive - Queue System and API Cleanup Documentation

## Overview

This document provides a comprehensive analysis of the download queue system in `/ui/pages/downloads.py` (10,668 lines). The system manages music downloads from the Soulseek network with sophisticated queue management, status tracking, and API synchronization.

## Architecture Overview

### Core Components

The download system consists of several key classes working together:

1. **DownloadQueue** - Individual queue management (active/finished)
2. **TabbedDownloadManager** - Tabbed interface coordinator
3. **CompactDownloadItem** - Individual download item UI
4. **ThreadSafeQueueManager** - Thread-safe operations
5. **ApiCleanupThread** - Background API cleanup

### Data Flow

```
User Action → Download Queue → Status Updates → API Cleanup → UI Updates
```

## Download Queue System

### DownloadQueue Class (Lines 4326-4553)

The `DownloadQueue` class manages individual queues with two modes:

```python
class DownloadQueue(QFrame):
    def __init__(self, title="Download Queue", queue_type="active", parent=None):
        self.queue_type = queue_type  # "active" or "finished"
        self.download_items = []
        self.setup_ui()
```

#### Key Properties:
- **queue_type**: `"active"` or `"finished"`
- **download_items**: List of `CompactDownloadItem` objects
- **queue_count_label**: Shows item count in UI
- **empty_message**: Placeholder when queue is empty

#### Critical Methods:

**add_download_item()** (Line 4436):
```python
def add_download_item(self, title: str, artist: str, status: str = "queued", 
                     progress: int = 0, file_size: int = 0, download_speed: int = 0, 
                     file_path: str = "", download_id: str = "", username: str = "", 
                     soulseek_client=None, album: str = None, track_number: int = None):
    # Hide empty message if first item
    if len(self.download_items) == 0:
        self.empty_message.hide()
    
    # Create new CompactDownloadItem
    item = CompactDownloadItem(title, artist, status, progress, file_size, download_speed, 
                             file_path, download_id, username, soulseek_client, self.queue_type,
                             album, track_number)
    self.download_items.append(item)
    
    # Insert before stretch
    insert_index = self.queue_layout.count() - 1
    self.queue_layout.insertWidget(insert_index, item)
    
    self.update_queue_count()
    return item
```

**remove_download_item()** (Line 4470):
```python
def remove_download_item(self, item):
    if item in self.download_items:
        self.download_items.remove(item)
        self.queue_layout.removeWidget(item)
        self._schedule_widget_deletion(item)  # Batched deletion for performance
        self.update_queue_count()
        
        # Notify parent to update tab counts
        parent_widget = self.parent()
        while parent_widget and not hasattr(parent_widget, 'update_tab_counts'):
            parent_widget = parent_widget.parent()
        if parent_widget:
            parent_widget.update_tab_counts()
```

**clear_completed_downloads()** (Line 4519):
```python
def clear_completed_downloads(self):
    items_to_remove = []
    
    for item in self.download_items:
        status_lower = item.status.lower()
        should_remove = False
        
        # Check for exact matches
        if status_lower in ["completed", "finished", "cancelled", "canceled", "failed"]:
            should_remove = True
        
        # Check for partial matches (handles compound statuses)
        elif any(keyword in status_lower for keyword in 
                ["completed", "finished", "cancelled", "canceled", "failed", "succeeded"]):
            should_remove = True
        
        if should_remove:
            items_to_remove.append(item)
    
    for item in items_to_remove:
        self.remove_download_item(item)
```

### TabbedDownloadManager Class (Lines 4554-4801)

Manages both active and finished queues in a tabbed interface:

```python
class TabbedDownloadManager(QTabWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.active_queue = DownloadQueue("Active Downloads", "active")
        self.finished_queue = DownloadQueue("Finished Downloads", "finished")
        
        self.addTab(self.active_queue, "Download Queue")
        self.addTab(self.finished_queue, "Finished Downloads")
```

#### Key Methods:

**move_to_finished()** (Line 4631) - **CRITICAL TRANSITION METHOD**:
```python
def move_to_finished(self, download_item):
    if download_item in self.active_queue.download_items:
        # Remove from active queue
        self.active_queue.remove_download_item(download_item)
        
        # Ensure completed downloads have 100% progress
        final_progress = download_item.progress
        if download_item.status == 'completed':
            final_progress = 100
        
        # Add to finished queue
        finished_item = self.finished_queue.add_download_item(
            title=download_item.title,
            artist=download_item.artist,
            status=download_item.status,
            progress=final_progress,
            file_size=download_item.file_size,
            download_speed=download_item.download_speed,
            file_path=download_item.file_path,
            download_id=download_item.download_id,
            username=download_item.username,
            soulseek_client=download_item.soulseek_client
        )
        
        # API Cleanup for completed downloads only
        if (download_item.status == 'completed' and 
            download_item.download_id and download_item.username and download_item.soulseek_client):
            
            # Create API cleanup thread
            cleanup_thread = ApiCleanupThread(
                download_item.soulseek_client,
                download_item.download_id,
                download_item.username
            )
            cleanup_thread.cleanup_completed.connect(parent_page.api_cleanup_finished)
            cleanup_thread.start()
        
        self.update_tab_counts()
        return finished_item
```

## CompactDownloadItem Class (Lines 3882-4323)

Individual download items with queue-specific behavior:

```python
class CompactDownloadItem(QFrame):
    def __init__(self, title: str, artist: str, status: str = "queued", 
                 progress: int = 0, file_size: int = 0, download_speed: int = 0, 
                 file_path: str = "", download_id: str = "", username: str = "", 
                 soulseek_client=None, queue_type: str = "active", 
                 album: str = None, track_number: int = None):
        self.queue_type = queue_type  # "active" or "finished"
        # ... other properties
```

### Status Handling and UI Representation

**update_status()** (Line 3708) - **CRITICAL STATUS UPDATE METHOD**:
```python
def update_status(self, status: str, progress: int = None, download_speed: int = None, file_path: str = None):
    self.status = status
    if progress is not None:
        self.progress = progress
    if download_speed is not None:
        self.download_speed = download_speed
    if file_path:
        self.file_path = file_path
    
    # Status mapping for clean display
    status_mapping = {
        "completed, succeeded": "Finished",
        "completed, cancelled": "Cancelled",
        "completed": "Finished",
        "cancelled": "Cancelled",
        "downloading": "Downloading",
        "failed": "Failed",
        "queued": "Queued"
    }
    
    clean_status = status_mapping.get(self.status.lower(), self.status.title())
    status_text = clean_status
    
    if self.status.lower() in ["downloading", "queued"]:
        status_text += f" - {self.progress}%"
    
    self.status_label.setText(status_text)
    
    # Update action button based on status
    if self.status == "downloading":
        self.action_btn.setText("Cancel")
        self.action_btn.clicked.connect(self.cancel_download)
        # Red cancel button styling
    elif self.status == "failed":
        self.action_btn.setText("Retry")
        self.action_btn.clicked.connect(self.retry_download)
        # Yellow retry button styling
    else:
        self.action_btn.setText("📂 Open")
        self.action_btn.clicked.connect(self.open_download_location)
        # Green open button styling
```

### Visual Differences by Status

#### Completed Downloads:
- **Status Text**: "Finished"
- **Progress**: 100%
- **Button**: "📂 Open" (Green)
- **Action**: Opens download location

#### Cancelled Downloads:
- **Status Text**: "Cancelled"
- **Progress**: Maintains last known progress
- **Button**: "📂 Open" (Green)
- **Action**: Opens download location (if file exists)

#### Failed Downloads:
- **Status Text**: "Failed"
- **Progress**: Maintains last known progress
- **Button**: "Retry" (Yellow)
- **Action**: Retries download

## API Cleanup System

### Three Cleanup Methods

#### 1. signal_download_completion() - For Completed Downloads
**Location**: `/core/soulseek_client.py:875`
**Usage**: Called when download completes successfully

```python
async def signal_download_completion(self, download_id: str, username: str, remove: bool = True) -> bool:
    """Signal the Soulseek API that a download has completed"""
    # Signals completion to slskd backend
    # Used by ApiCleanupThread for completed downloads
```

#### 2. cancel_download() - For Cancelled Downloads
**Location**: `/core/soulseek_client.py:839`
**Usage**: Called when user cancels download or download fails

```python
async def cancel_download(self, download_id: str, username: str = None, remove: bool = False) -> bool:
    """Cancel a download and optionally remove it from the queue"""
    # Used for both user cancellations and automatic cleanup of failed downloads
```

#### 3. clear_all_completed_downloads() - Bulk Cleanup
**Location**: `/core/soulseek_client.py:910`
**Usage**: Called by "Clear Completed Downloads" button

```python
async def clear_all_completed_downloads(self) -> bool:
    """Clear all completed/finished downloads from slskd backend"""
    # Uses endpoint: DELETE /api/v0/transfers/downloads/all/completed
    # Removes all downloads with completed, cancelled, or failed status
```

### ApiCleanupThread Class (Lines 1700-1750)

Background thread for API cleanup without blocking UI:

```python
class ApiCleanupThread(QThread):
    cleanup_completed = pyqtSignal(bool, str, str)  # success, download_id, username
    
    def __init__(self, soulseek_client, download_id, username):
        super().__init__()
        self.soulseek_client = soulseek_client
        self.download_id = download_id
        self.username = username
        
    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            # Signal download completion
            success = loop.run_until_complete(
                self.soulseek_client.signal_download_completion(
                    self.download_id, 
                    self.username, 
                    remove=True
                )
            )
            self.cleanup_completed.emit(success, self.download_id, self.username)
        except Exception as e:
            self.cleanup_completed.emit(False, self.download_id, self.username)
        finally:
            loop.close()
```

## Status Polling and Transitions

### update_download_status() (Line 9547)

Main status polling method that keeps UI synchronized with backend:

```python
def update_download_status(self):
    """Poll slskd API for download status updates"""
    if not self.soulseek_client or not self.download_queue.download_items:
        return
    
    def handle_status_update(transfers_data):
        # Flatten transfers data structure
        all_transfers = []
        for user_data in transfers_data:
            if 'directories' in user_data:
                for directory in user_data['directories']:
                    if 'files' in directory:
                        all_transfers.extend(directory['files'])
        
        # Update each download item
        for download_item in self.download_queue.download_items.copy():
            matching_transfer = None
            
            # Find matching transfer by various criteria
            for transfer in all_transfers:
                if self._is_matching_transfer(download_item, transfer):
                    matching_transfer = transfer
                    break
            
            if matching_transfer:
                state = matching_transfer.get('state', '')
                
                # Handle different states
                if 'Completed' in state:
                    # Process completed download
                    download_item.update_status('completed', 100, 0)
                    self.download_queue.move_to_finished(download_item)
                    
                elif 'Cancelled' in state or 'Canceled' in state:
                    # Process cancelled download
                    download_item.update_status('cancelled', download_item.progress, 0)
                    
                    # IMMEDIATE CLEANUP for cancelled downloads
                    if download_item.download_id and download_item.username:
                        self._cleanup_cancelled_download(download_item)
                    
                    self.download_queue.move_to_finished(download_item)
                    
                elif 'Failed' in state or 'Errored' in state:
                    # Process failed download
                    download_item.update_status('failed', download_item.progress, 0)
                    
                    # RETRY CLEANUP for failed downloads
                    if download_item.download_id and download_item.username:
                        self._cleanup_errored_download_with_retry(download_item)
                    
                    self.download_queue.move_to_finished(download_item)
                    
                elif 'Downloading' in state:
                    # Update progress for active downloads
                    progress = int(matching_transfer.get('percentComplete', 0))
                    speed = int(matching_transfer.get('averageSpeed', 0))
                    download_item.update_status('downloading', progress, speed)
                    
                elif 'Queued' in state:
                    download_item.update_status('queued', download_item.progress, 0)
```

### Cleanup Strategies by Status

#### Completed Downloads (Lines 4665-4698):
- **When**: After successful download completion
- **Method**: `signal_download_completion()` via `ApiCleanupThread`
- **Purpose**: Notify slskd that download is complete and can be removed
- **Threading**: Background thread to prevent UI blocking

#### Cancelled Downloads (Lines 9794-9823):
- **When**: User clicks cancel or download is cancelled by backend
- **Method**: `cancel_download()` with `remove=True`
- **Purpose**: Remove from slskd queue immediately
- **Threading**: Background thread with immediate execution

```python
def cleanup_cancelled_download():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    success = loop.run_until_complete(
        download_item.soulseek_client.cancel_download(
            download_item.download_id, 
            download_item.username, 
            remove=True
        )
    )
    loop.close()
```

#### Failed Downloads (Lines 9837-9872):
- **When**: Download fails due to network/peer issues
- **Method**: `cancel_download()` with `remove=True` and retry mechanism
- **Purpose**: Clean up failed downloads with multiple retry attempts
- **Threading**: Background thread with progressive retry delays

```python
def cleanup_errored_download_with_retry():
    max_retries = 3
    for attempt in range(max_retries):
        try:
            if attempt == 0:
                time.sleep(2)  # Initial delay
            else:
                time.sleep(5 * attempt)  # Progressive delay
            
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            success = loop.run_until_complete(
                download_item.soulseek_client.cancel_download(
                    download_item.download_id, 
                    download_item.username, 
                    remove=True
                )
            )
            loop.close()
            
            if success:
                return  # Success, exit retry loop
                
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"Failed to clean up after {max_retries} attempts")
```

## Queue Transitions Flow

### 1. New Download Added
```
User clicks download → add_download_item() → Active Queue → UI Update
```

### 2. Download Progresses
```
Status Polling → update_download_status() → UI Update → Progress Bar Update
```

### 3. Download Completes
```
Status Polling → 'Completed' State → update_status() → move_to_finished() → API Cleanup Thread → Finished Queue
```

### 4. Download Cancelled
```
User clicks Cancel → cancel_download() → API Cleanup → 'Cancelled' State → move_to_finished() → Finished Queue
```

### 5. Download Fails
```
Status Polling → 'Failed' State → update_status() → API Cleanup with Retry → move_to_finished() → Finished Queue
```

### 6. Clear Completed Downloads
```
User clicks Clear → clear_completed_downloads() → Backend API Call → clear_local_queues_only() → UI Update
```

## Critical Integration Points

### 1. Status Synchronization (Lines 9547-9979)
- **Polling Frequency**: 1-second intervals with adaptive optimization
- **Thread Safety**: Uses `ThreadSafeQueueManager` for concurrent access
- **Error Handling**: Grace periods for API delays and missing transfers

### 2. UI Thread Safety (Lines 4561-4567)
- **Batched Updates**: Timer-based batching to prevent excessive UI updates
- **Widget Deletion**: Scheduled deletion to prevent memory leaks
- **Signal-based Communication**: Thread-safe communication between components

### 3. Performance Optimizations
- **Adaptive Polling**: Adjusts polling frequency based on active downloads
- **Thread Pooling**: Reuses threads for API operations
- **Memory Management**: Proper cleanup of Qt objects and threads

## Error Handling and Edge Cases

### API Missing Downloads (Lines 9916-9944)
When downloads disappear from API (backend cleanup/restart):
- **Grace Period**: 2-3 polling cycles before marking as failed
- **State Transition**: Moves to finished queue with 'failed' status
- **User Feedback**: Visible in finished downloads for user awareness

### Network Issues
- **Retry Mechanisms**: Progressive delays for API calls
- **Fallback Cleanup**: Secondary cleanup attempts for persistent errors
- **UI Resilience**: Continues to function even with API failures

### Concurrent Operations
- **Thread Safety**: All queue operations are thread-safe
- **Lock Management**: Prevents race conditions in status updates
- **Atomic Transitions**: Ensures consistent state during transitions

## Key File Locations for Modifications

### For Visual Changes to Finished Downloads:
- **CompactDownloadItem.setup_ui()** (Lines 3927-4110): UI styling and layout
- **CompactDownloadItem.update_status()** (Lines 3708-3891): Status-specific styling
- **Status mapping** (Lines 3728-3738): Status text display

### For Queue Behavior Changes:
- **TabbedDownloadManager.move_to_finished()** (Lines 4631-4712): Transition logic
- **DownloadQueue.clear_completed_downloads()** (Lines 4519-4552): Clearing logic
- **update_download_status()** (Lines 9547-9979): Status polling and transitions

### For API Cleanup Changes:
- **ApiCleanupThread** (Lines 1700-1750): Background cleanup
- **Cleanup strategies** (Lines 9794-9872): Status-specific cleanup
- **SoulseekClient methods** in `/core/soulseek_client.py`: API endpoints

## Conclusion

The download queue system is a sophisticated, thread-safe implementation that manages the complete lifecycle of music downloads. Understanding these components and their interactions is crucial for safely modifying the appearance and behavior of finished downloads without breaking the underlying synchronization with the Soulseek backend.

The system's strength lies in its separation of concerns: UI presentation, queue management, status tracking, and API synchronization are all handled by separate components that communicate through well-defined interfaces. This architecture enables safe modifications to the visual representation while maintaining the integrity of the download management system.