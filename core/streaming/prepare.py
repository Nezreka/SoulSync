"""Streaming preparation worker.

`prepare_stream_task(track_data, deps)` is the function the stream
executor submits to fetch a track from Soulseek/YouTube/etc and stage
it in the local Stream/ folder for the browser audio player.

1. Reset stream state to 'loading' with the new track info.
2. Clear any prior file from the Stream/ folder (only one stream lives
   there at a time).
3. Spin up a fresh asyncio event loop and `soulseek_client.download()`
   the track.
4. Poll `soulseek_client.get_all_downloads()` every 1.5 s to track
   progress, with separate handling for queued vs actively downloading
   states. Queue timeout = 15 s; overall timeout = 60 s.
5. On completion (state ~ 'succeeded' or progress >= 100% AND bytes
   transferred match expected size), find the downloaded file with retry
   logic, move it into Stream/, signal completion to the slskd API, and
   mark stream_state as 'ready' with the file path.
6. On any error/timeout/cancel: stream_state goes to 'error' or
   'stopped' with an explanatory message.
7. Finally: tear down the event loop cleanly.

The original mutated `stream_state` as a module global. Here it's
exposed through the `PrepareStreamDeps` proxy as a Python property so
the lifted body keeps the same `name[key] = value` syntax. The setter
fires only if the function reassigns (currently it only mutates in
place via .update() and key assignment).
"""

from __future__ import annotations

import asyncio
import glob
import logging
import os
import shutil
import time
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class PrepareStreamDeps:
    """Bundle of cross-cutting deps the stream-prep worker needs."""
    config_manager: Any
    soulseek_client: Any
    stream_lock: Any  # threading.Lock
    project_root: str  # absolute path to web_server.py's directory
    docker_resolve_path: Callable[[str], str]
    find_streaming_download_in_all_downloads: Callable
    find_downloaded_file: Callable
    extract_filename: Callable[[str], str]
    cleanup_empty_directories: Callable
    _get_stream_state: Callable[[], dict]
    _set_stream_state: Callable[[dict], None]

    @property
    def stream_state(self) -> dict:
        return self._get_stream_state()

    @stream_state.setter
    def stream_state(self, value: dict) -> None:
        self._set_stream_state(value)


def prepare_stream_task(track_data, deps: PrepareStreamDeps):
    """
    Background streaming task that downloads track to Stream folder and updates global state.
    Enhanced version with robust error handling matching the GUI StreamingThread.
    """
    loop = None
    queue_start_time = None
    actively_downloading = False
    last_progress_sent = 0.0
    
    try:
        logger.info(f"Starting stream preparation for: {track_data.get('filename')}")
        
        # Update state to loading
        with deps.stream_lock:
            deps.stream_state.update({
                "status": "loading",
                "progress": 0,
                "track_info": track_data,
                "file_path": None,
                "error_message": None
            })
        
        # Get paths
        download_path = deps.docker_resolve_path(deps.config_manager.get('soulseek.download_path', './downloads'))
        project_root = deps.project_root
        stream_folder = os.path.join(project_root, 'Stream')
        
        # Ensure Stream directory exists
        os.makedirs(stream_folder, exist_ok=True)
        
        # Clear any existing files in Stream folder (only one file at a time)
        for existing_file in glob.glob(os.path.join(stream_folder, '*')):
            try:
                if os.path.isfile(existing_file):
                    os.remove(existing_file)
                elif os.path.isdir(existing_file):
                    shutil.rmtree(existing_file)
                logger.info(f"Cleared old stream file: {existing_file}")
            except Exception as e:
                logger.error(f"Could not remove existing stream file: {e}")
        
        # Start the download using the same mechanism as regular downloads
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            download_result = loop.run_until_complete(deps.soulseek_client.download(
                track_data.get('username'),
                track_data.get('filename'),
                track_data.get('size', 0)
            ))
            
            if not download_result:
                with deps.stream_lock:
                    deps.stream_state.update({
                        "status": "error",
                        "error_message": "Failed to initiate download - uploader may be offline"
                    })
                return
            
            logger.info("Download initiated for streaming")
            
            # Enhanced monitoring with queue timeout detection (matching GUI)
            max_wait_time = 60  # Increased timeout
            poll_interval = 1.5  # More frequent polling
            queue_timeout = 15   # Queue timeout like GUI
            wait_count = 0
            
            while wait_count * poll_interval < max_wait_time:
                wait_count += 1
                
                # Check download progress via orchestrator (works for Soulseek and YouTube)
                api_progress = None
                download_state = None
                download_status = None

                try:
                    # Use orchestrator's get_all_downloads() which works for both sources
                    all_downloads = loop.run_until_complete(deps.soulseek_client.get_all_downloads())
                    download_status = deps.find_streaming_download_in_all_downloads(all_downloads, track_data)
                    
                    if download_status:
                        api_progress = download_status.get('percentComplete', 0)
                        download_state = download_status.get('state', '').lower()
                        original_state = download_status.get('state', '')
                        
                        logger.info(f"API Download - State: {original_state}, Progress: {api_progress:.1f}%")
                        
                        # Track queue state timing (matching GUI logic)
                        is_queued = ('queued' in download_state or 'initializing' in download_state)
                        is_downloading = ('inprogress' in download_state or 'transferring' in download_state)
                        # Verify bytes match before trusting state/progress
                        _stream_expected = download_status.get('size', 0)
                        _stream_transferred = download_status.get('bytesTransferred', 0)
                        _bytes_ok = _stream_expected <= 0 or _stream_transferred >= _stream_expected
                        is_completed = ('succeeded' in download_state or api_progress >= 100) and _bytes_ok
                        
                        # Handle queue state timing
                        if is_queued and queue_start_time is None:
                            queue_start_time = time.time()
                            logger.info(f"Download entered queue state: {original_state}")
                            with deps.stream_lock:
                                deps.stream_state["status"] = "queued"
                        elif is_downloading and not actively_downloading:
                            actively_downloading = True
                            queue_start_time = None  # Reset queue timer
                            logger.info(f"Download started actively downloading: {original_state}")
                            with deps.stream_lock:
                                deps.stream_state["status"] = "loading"
                        
                        # Check for queue timeout (matching GUI)
                        if is_queued and queue_start_time:
                            queue_elapsed = time.time() - queue_start_time
                            if queue_elapsed > queue_timeout:
                                logger.error(f"⏰ Queue timeout after {queue_elapsed:.1f}s - download stuck in queue")
                                with deps.stream_lock:
                                    deps.stream_state.update({
                                        "status": "error",
                                        "error_message": "Queue timeout - uploader not responding. Try another source."
                                    })
                                return
                        
                        # Update progress
                        with deps.stream_lock:
                            if api_progress != last_progress_sent:
                                deps.stream_state["progress"] = api_progress
                                last_progress_sent = api_progress
                        
                        # Check if download is complete
                        if is_completed:
                            logger.info(f"Download completed via API status: {original_state}")

                            # Wait for file to stabilise on disk before moving
                            found_file = deps.find_downloaded_file(download_path, track_data)
                            if found_file:
                                _prev_sz = -1
                                for _sc in range(4):
                                    try:
                                        _cur_sz = os.path.getsize(found_file)
                                    except OSError:
                                        _cur_sz = -1
                                    if _cur_sz == _prev_sz and _cur_sz > 0:
                                        break
                                    _prev_sz = _cur_sz
                                    time.sleep(1.5)

                            # Re-find in case it wasn't found on first try
                            if not found_file:
                                found_file = deps.find_downloaded_file(download_path, track_data)
                            
                            # Retry file search a few times (matching GUI logic)
                            retry_attempts = 5
                            for attempt in range(retry_attempts):
                                if found_file:
                                    break
                                logger.warning(f"File not found yet, attempt {attempt + 1}/{retry_attempts}")
                                time.sleep(1)
                                found_file = deps.find_downloaded_file(download_path, track_data)
                            
                            if found_file:
                                logger.debug(f"Found downloaded file: {found_file}")
                                
                                # Move file to Stream folder
                                original_filename = deps.extract_filename(found_file)
                                stream_path = os.path.join(stream_folder, original_filename)
                                
                                try:
                                    shutil.move(found_file, stream_path)
                                    logger.debug(f"Moved file to stream folder: {stream_path}")
                                    
                                    # Clean up empty directories (matching GUI)
                                    deps.cleanup_empty_directories(download_path, found_file)
                                    
                                    # Update state to ready
                                    with deps.stream_lock:
                                        deps.stream_state.update({
                                            "status": "ready",
                                            "progress": 100,
                                            "file_path": stream_path
                                        })
                                    
                                    # Clean up download from slskd API
                                    try:
                                        download_id = download_status.get('id', '')
                                        if download_id and track_data.get('username'):
                                            success = loop.run_until_complete(
                                                deps.soulseek_client.signal_download_completion(
                                                    download_id, track_data.get('username'), remove=True)
                                            )
                                            if success:
                                                logger.debug(f"Cleaned up download {download_id} from API")
                                    except Exception as e:
                                        logger.error(f"Error cleaning up download: {e}")
                                    
                                    logger.info(f"Stream file ready for playback: {stream_path}")
                                    return  # Success!
                                    
                                except Exception as e:
                                    logger.error(f"Error moving file to stream folder: {e}")
                                    with deps.stream_lock:
                                        deps.stream_state.update({
                                            "status": "error",
                                            "error_message": f"Failed to prepare stream file: {e}"
                                        })
                                    return
                            else:
                                logger.error("Could not find downloaded file after completion")
                                with deps.stream_lock:
                                    deps.stream_state.update({
                                        "status": "error",
                                        "error_message": "Download completed but file not found"
                                    })
                                return
                    else:
                        # No transfer found in API - may still be initializing
                        logger.debug(f"No transfer found in API yet... (elapsed: {wait_count * poll_interval}s)")
                        
                except Exception as e:
                    logger.error(f"Error checking download progress: {e}")
                    # Continue to next iteration if API call fails
                
                # Wait before next poll
                time.sleep(poll_interval)
            
            # If we get here, download timed out
            logger.warning(f"Download timed out after {max_wait_time}s")
            with deps.stream_lock:
                deps.stream_state.update({
                    "status": "error", 
                    "error_message": "Download timed out - try a different source"
                })
                
        except asyncio.CancelledError:
            logger.warning("Stream task cancelled")
            with deps.stream_lock:
                deps.stream_state.update({
                    "status": "stopped",
                    "error_message": None
                })
        finally:
            if loop:
                try:
                    # Clean up any pending tasks
                    pending = asyncio.all_tasks(loop)
                    if pending:
                        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                    loop.close()
                except Exception as e:
                    logger.error(f"Error cleaning up streaming event loop: {e}")
            
    except Exception as e:
        logger.error(f"Stream preparation failed: {e}")
        with deps.stream_lock:
            deps.stream_state.update({
                "status": "error",
                "error_message": f"Streaming error: {str(e)}"
            })

