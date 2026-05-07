"""Duplicate cleaner — lifted from web_server.py.

The function body is byte-identical to the original. Module-level
state and helpers are injected via init() because the duplicate
cleaner state dict, lock, automation engine, and docker_resolve_path
helper all live in web_server.py.
"""
import logging

from config.settings import config_manager
from core.runtime_state import add_activity_item

logger = logging.getLogger(__name__)

# Injected at runtime via init().
duplicate_cleaner_state = None
duplicate_cleaner_lock = None
docker_resolve_path = None
automation_engine = None


def init(state, lock, resolve_path_fn, engine):
    """Bind shared state/helpers from web_server."""
    global duplicate_cleaner_state, duplicate_cleaner_lock
    global docker_resolve_path, automation_engine
    duplicate_cleaner_state = state
    duplicate_cleaner_lock = lock
    docker_resolve_path = resolve_path_fn
    automation_engine = engine


def _run_duplicate_cleaner():
    """Main duplicate cleaner worker function - scans Transfer folder for duplicate files"""
    import os
    import shutil
    from collections import defaultdict
    from pathlib import Path

    try:
        with duplicate_cleaner_lock:
            duplicate_cleaner_state["status"] = "running"
            duplicate_cleaner_state["phase"] = "Initializing scan..."
            duplicate_cleaner_state["progress"] = 0
            duplicate_cleaner_state["files_scanned"] = 0
            duplicate_cleaner_state["total_files"] = 0
            duplicate_cleaner_state["duplicates_found"] = 0
            duplicate_cleaner_state["deleted"] = 0
            duplicate_cleaner_state["space_freed"] = 0
            duplicate_cleaner_state["error_message"] = ""

        logger.warning("[Duplicate Cleaner] Starting duplicate scan...")

        # Get Transfer folder path from config
        transfer_folder = docker_resolve_path(config_manager.get('soulseek.transfer_path', './Transfer'))
        if not transfer_folder or not os.path.exists(transfer_folder):
            with duplicate_cleaner_lock:
                duplicate_cleaner_state["status"] = "error"
                duplicate_cleaner_state["phase"] = "Output folder not configured or does not exist"
                duplicate_cleaner_state["error_message"] = "Please configure output folder in settings"
            logger.warning(f"[Duplicate Cleaner] Transfer folder not found: {transfer_folder}")
            return

        # Create deleted folder if it doesn't exist
        deleted_folder = os.path.join(transfer_folder, 'deleted')
        os.makedirs(deleted_folder, exist_ok=True)
        logger.warning(f"[Duplicate Cleaner] Deleted folder: {deleted_folder}")

        # Phase 1: Count total files for progress tracking
        with duplicate_cleaner_lock:
            duplicate_cleaner_state["phase"] = "Counting files..."

        total_files = 0
        for _root, dirs, files in os.walk(transfer_folder):
            # Skip the deleted folder itself
            if 'deleted' in dirs:
                dirs.remove('deleted')
            total_files += len(files)

        logger.warning(f"[Duplicate Cleaner] Found {total_files} total files to scan")

        with duplicate_cleaner_lock:
            duplicate_cleaner_state["total_files"] = total_files
            duplicate_cleaner_state["phase"] = f"Scanning {total_files} files..."

        # Phase 2: Scan and group files by directory and filename
        # Structure: {directory_path: {filename_without_ext: [full_file_paths]}}
        files_by_dir_and_name = defaultdict(lambda: defaultdict(list))
        files_scanned = 0

        # Audio file extensions to consider
        audio_extensions = {'.flac', '.mp3', '.m4a', '.aac', '.opus', '.ogg', '.wav', '.ape', '.wma', '.alac', '.aiff', '.aif', '.dsf', '.dff'}

        for root, dirs, files in os.walk(transfer_folder):
            # Skip the deleted folder
            if 'deleted' in dirs:
                dirs.remove('deleted')

            for file in files:
                files_scanned += 1

                # Update progress
                with duplicate_cleaner_lock:
                    duplicate_cleaner_state["files_scanned"] = files_scanned
                    duplicate_cleaner_state["progress"] = (files_scanned / total_files) * 100 if total_files > 0 else 0
                    duplicate_cleaner_state["phase"] = f"Scanning: {file}"

                # Get file extension
                file_path = os.path.join(root, file)
                file_name, file_ext = os.path.splitext(file)
                file_ext_lower = file_ext.lower()

                # Only process audio files
                if file_ext_lower not in audio_extensions:
                    continue

                # Group by directory and filename (without extension)
                files_by_dir_and_name[root][file_name].append({
                    'full_path': file_path,
                    'extension': file_ext_lower,
                    'size': os.path.getsize(file_path)
                })

        # Phase 3: Process duplicates
        with duplicate_cleaner_lock:
            duplicate_cleaner_state["phase"] = "Processing duplicates..."

        # Quality priority: FLAC > OPUS/OGG > M4A/AAC > MP3/WMA
        format_priority = {
            '.flac': 1, '.ape': 1, '.wav': 1, '.alac': 1, '.aiff': 1, '.aif': 1, '.dsf': 1, '.dff': 1,  # Lossless
            '.opus': 2, '.ogg': 2,  # High quality lossy
            '.m4a': 3, '.aac': 3,   # Standard lossy
            '.mp3': 4, '.wma': 4    # Lower quality lossy
        }

        duplicates_found = 0
        deleted_count = 0
        space_freed = 0

        for directory, files_by_name in files_by_dir_and_name.items():
            for filename, file_versions in files_by_name.items():
                # Only process if we have duplicates (more than one version)
                if len(file_versions) <= 1:
                    continue

                duplicates_found += len(file_versions) - 1  # Count all but the one we keep
                logger.warning(f"[Duplicate Cleaner] Found {len(file_versions)} versions of '{filename}' in {directory}")

                # Sort by priority: best format first, then largest size
                def sort_key(f):
                    priority = format_priority.get(f['extension'], 999)
                    size = f['size']
                    return (priority, -size)  # Negative size for descending order

                sorted_versions = sorted(file_versions, key=sort_key)

                # Keep the first one (best quality), delete the rest
                best_version = sorted_versions[0]
                logger.warning(f"[Duplicate Cleaner] Keeping: {os.path.basename(best_version['full_path'])} "
                      f"({best_version['extension']}, {best_version['size']} bytes)")

                for duplicate_file in sorted_versions[1:]:
                    try:
                        # Move to deleted folder with relative path preserved
                        relative_path = os.path.relpath(duplicate_file['full_path'], transfer_folder)
                        deleted_path = os.path.join(deleted_folder, relative_path)

                        # Create subdirectories in deleted folder if needed
                        os.makedirs(os.path.dirname(deleted_path), exist_ok=True)

                        # Move the file
                        shutil.move(duplicate_file['full_path'], deleted_path)

                        # Track stats
                        deleted_count += 1
                        space_freed += duplicate_file['size']

                        logger.warning(f"[Duplicate Cleaner] Moved to deleted: {os.path.basename(duplicate_file['full_path'])} "
                              f"({duplicate_file['extension']}, {duplicate_file['size']} bytes)")

                        # Update stats
                        with duplicate_cleaner_lock:
                            duplicate_cleaner_state["deleted"] = deleted_count
                            duplicate_cleaner_state["space_freed"] = space_freed
                            duplicate_cleaner_state["duplicates_found"] = duplicates_found

                    except Exception as e:
                        logger.error(f"[Duplicate Cleaner] Error moving file {duplicate_file['full_path']}: {e}")
                        continue

        # Scan complete
        with duplicate_cleaner_lock:
            duplicate_cleaner_state["status"] = "finished"
            duplicate_cleaner_state["progress"] = 100
            duplicate_cleaner_state["phase"] = "Cleaning complete"

        space_mb = space_freed / (1024 * 1024)
        logger.warning(f"[Duplicate Cleaner] Scan complete: {files_scanned} files scanned, "
              f"{duplicates_found} duplicates found, {deleted_count} files moved to deleted folder, "
              f"{space_mb:.2f} MB freed")

        # Add activity
        add_activity_item("", "Duplicate Cleaner Complete",
                         f"{deleted_count} files removed, {space_mb:.1f} MB freed", "Now")

        try:
            if automation_engine:
                automation_engine.emit('duplicate_scan_completed', {
                    'files_scanned': str(files_scanned),
                    'duplicates_found': str(duplicates_found),
                    'space_freed': f"{space_mb:.1f} MB",
                })
        except Exception as e:
            logger.debug("emit duplicate_scan_completed failed: %s", e)

    except Exception as e:
        logger.error(f"[Duplicate Cleaner] Critical error: {e}")
        import traceback
        traceback.print_exc()

        with duplicate_cleaner_lock:
            duplicate_cleaner_state["status"] = "error"
            duplicate_cleaner_state["error_message"] = str(e)
            duplicate_cleaner_state["phase"] = f"Error: {str(e)}"
