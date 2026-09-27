"""requests: a profile that can't download asks, an admin decides.

video keeps its own table (video db isolation); music rides the wishlist, see
core.requests.music. both notify the requester through core.profile_notify.
"""
