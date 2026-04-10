#!/usr/bin/env python3
"""Reports basic system info — useful for debugging Docker setups."""
import os
import platform
import shutil

print(f"Platform: {platform.system()} {platform.release()}")
print(f"Python: {platform.python_version()}")
print(f"Working Dir: {os.getcwd()}")

# Disk usage for common SoulSync paths
for path in ['/app/downloads', '/app/Transfer', '/app/data', './downloads', './Transfer']:
    if os.path.exists(path):
        usage = shutil.disk_usage(path)
        free_gb = usage.free / (1024**3)
        total_gb = usage.total / (1024**3)
        print(f"Disk {path}: {free_gb:.1f} GB free / {total_gb:.1f} GB total")
