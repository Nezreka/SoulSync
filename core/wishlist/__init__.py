"""Wishlist package.

This package collects the wishlist service and the shared helpers that are
being peeled out of the old controller-heavy code paths.
"""

from core.wishlist.processing import (
    WishlistAutoProcessingRuntime,
    WishlistManualDownloadRuntime,
    cleanup_wishlist_against_library,
    process_wishlist_automatically,
    start_manual_wishlist_download_batch,
)
from core.wishlist.service import WishlistService, get_wishlist_service

__all__ = [
    "WishlistService",
    "get_wishlist_service",
    "WishlistAutoProcessingRuntime",
    "WishlistManualDownloadRuntime",
    "cleanup_wishlist_against_library",
    "process_wishlist_automatically",
    "start_manual_wishlist_download_batch",
]
