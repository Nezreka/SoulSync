"""Compatibility shim for legacy wishlist service imports."""

from core.wishlist.service import WishlistService, get_wishlist_service

__all__ = ["WishlistService", "get_wishlist_service"]
