"""Quality-profile endpoints, lifted out of web_server.py.

CRUD over the download quality profiles. Only the DB accessor and the
activity feed are injected."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime

from flask import Blueprint, jsonify, request

from utils.logging_config import get_logger

logger = get_logger("api.quality_profiles")

bp = Blueprint("quality_profiles", __name__)

# Injected by configure() at boot.
get_database = None
add_activity_item = None



def configure(*, get_database, add_activity_item):
    globals()['get_database'] = get_database
    globals()['add_activity_item'] = add_activity_item


def create_blueprint():
    return bp


@bp.route('/api/quality-profile', methods=['GET'])
def get_quality_profile():
    """Get current quality profile configuration"""
    try:
        from database.music_database import MusicDatabase
        db = MusicDatabase()
        profile = db.get_quality_profile()

        return jsonify({
            "success": True,
            "profile": profile
        })
    except Exception as e:
        logger.error(f"Error getting quality profile: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/quality-profile', methods=['POST'])
def save_quality_profile():
    """Save quality profile configuration"""
    try:
        from database.music_database import MusicDatabase
        db = MusicDatabase()

        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No profile data provided"}), 400

        success = db.set_quality_profile(data)

        if success:
            from core.library2.wishlist_mirror import refresh_quality_profile_wishlist
            refresh_quality_profile_wishlist(db, db.get_quality_profile()["id"])
            add_activity_item("", "Quality Profile Updated", f"Preset: {data.get('preset', 'custom')}", "Now")
            return jsonify({"success": True, "message": "Quality profile saved successfully"})
        else:
            return jsonify({"success": False, "error": "Failed to save quality profile"}), 500

    except Exception as e:
        logger.error(f"Error saving quality profile: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/quality-profile/presets', methods=['GET'])
def get_quality_presets():
    """Get all available quality presets"""
    try:
        from database.music_database import MusicDatabase
        db = MusicDatabase()

        presets = {
            "audiophile": db.get_quality_preset("audiophile"),
            "balanced": db.get_quality_preset("balanced"),
            "space_saver": db.get_quality_preset("space_saver")
        }

        return jsonify({
            "success": True,
            "presets": presets
        })
    except Exception as e:
        logger.error(f"Error getting quality presets: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/quality-profile/preset/<preset_name>', methods=['POST'])
def apply_quality_preset(preset_name):
    """Switch to a quality preset, restoring its saved edits if it has any."""
    try:
        from database.music_database import MusicDatabase
        db = MusicDatabase()

        current = db.get_quality_profile()
        preset = dict(db.get_quality_preset(preset_name))
        # search_mode + rank_candidates_by_quality are global search/ordering
        # strategies, not per-preset audio settings — carry the user's current
        # choices across preset switches.
        preset['search_mode'] = current.get('search_mode', preset.get('search_mode', 'priority'))
        preset['rank_candidates_by_quality'] = current.get(
            'rank_candidates_by_quality', preset.get('rank_candidates_by_quality', False))
        preset['upgrade_policy'] = current.get('upgrade_policy', 'none')
        preset['upgrade_cutoff_index'] = current.get('upgrade_cutoff_index', 0)
        success = db.set_quality_profile(preset)

        if success:
            from core.library2.wishlist_mirror import refresh_quality_profile_wishlist
            refresh_quality_profile_wishlist(db, db.get_quality_profile()["id"])
            add_activity_item("", "Quality Preset Applied", f"Switched to '{preset_name}' preset", "Now")
            return jsonify({
                "success": True,
                "message": f"Switched to '{preset_name}' preset",
                "profile": preset
            })
        else:
            return jsonify({"success": False, "error": "Failed to apply preset"}), 500

    except Exception as e:
        logger.error(f"Error applying quality preset: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/quality-profile/preset/<preset_name>/reset', methods=['POST'])
def reset_quality_preset(preset_name):
    """Discard a preset's saved edits and restore its factory defaults."""
    try:
        from database.music_database import MusicDatabase
        db = MusicDatabase()

        current = db.get_quality_profile()
        preset = dict(db.reset_quality_preset(preset_name))
        preset['search_mode'] = current.get('search_mode', preset.get('search_mode', 'priority'))
        preset['rank_candidates_by_quality'] = current.get(
            'rank_candidates_by_quality', preset.get('rank_candidates_by_quality', False))
        preset['upgrade_policy'] = current.get('upgrade_policy', 'none')
        preset['upgrade_cutoff_index'] = current.get('upgrade_cutoff_index', 0)
        success = db.set_quality_profile(preset)

        if success:
            from core.library2.wishlist_mirror import refresh_quality_profile_wishlist
            refresh_quality_profile_wishlist(db, db.get_quality_profile()["id"])
            add_activity_item("", "Quality Preset Reset", f"Reset '{preset_name}' to defaults", "Now")
            return jsonify({
                "success": True,
                "message": f"Reset '{preset_name}' to defaults",
                "profile": preset
            })
        else:
            return jsonify({"success": False, "error": "Failed to reset preset"}), 500

    except Exception as e:
        logger.error(f"Error resetting quality preset: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

# ── Named global quality profiles (assignable to Wishlist items / per-context
# overrides like Auto-Import, not just the single active default) — CRUD over
# `quality_profiles`. ──────────

@bp.route('/api/quality-profile/custom', methods=['GET'])
def list_custom_quality_profiles():
    """List every quality profile (built-ins + user-created), default first.

    ``upgrade_policy`` values are ``none``, ``acceptable``, ``until_cutoff`` and the
    persisted compatibility alias ``until_top`` (implicit cutoff index 0).
    """
    try:
        from database.music_database import MusicDatabase
        db = MusicDatabase()
        return jsonify({"success": True, "profiles": db.list_quality_profiles()})
    except Exception as e:
        logger.error(f"Error listing quality profiles: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/quality-profile/custom', methods=['POST'])
def create_custom_quality_profile():
    """Save the current Quality-page settings as a new named profile.

    New UI writes use ``none``, ``acceptable`` or ``until_cutoff``; ``until_top`` stays
    accepted on existing/imported profiles as the top-target compatibility
    alias.
    """
    try:
        from database.music_database import MusicDatabase
        db = MusicDatabase()

        data = request.get_json() or {}
        name = str(data.get('name') or '').strip()
        if not name:
            return jsonify({"success": False, "error": "Name is required"}), 400

        profile_id = db.create_quality_profile(name, data)
        if profile_id is None:
            return jsonify({"success": False, "error": "A profile with that name may already exist"}), 400

        add_activity_item("", "Quality Profile Created", f"Saved '{name}'", "Now")
        return jsonify({"success": True, "id": profile_id, "profiles": db.list_quality_profiles()})
    except Exception as e:
        logger.error(f"Error creating quality profile: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/quality-profile/custom/<int:profile_id>', methods=['GET'])
def get_custom_quality_profile(profile_id):
    """Read-only: a single profile in the same full v3 shape `/apply` and
    `/api/quality-profile` return (parsed ranked_targets, real booleans,
    `preset`), so the Settings UI can load a profile's settings into the page
    for viewing/editing WITHOUT the side effects `/apply` has (making it the
    default, pushing into live config) — purely a SELECT."""
    try:
        from core.quality.selection import load_profile_by_id
        from database.music_database import MusicDatabase

        db = MusicDatabase()
        if not any(p['id'] == profile_id for p in db.list_quality_profiles()):
            return jsonify({"success": False, "error": "Profile not found"}), 404

        return jsonify({"success": True, "profile": load_profile_by_id(profile_id)})
    except Exception as e:
        logger.error(f"Error loading quality profile {profile_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/quality-profile/custom/<int:profile_id>/apply', methods=['POST'])
def apply_custom_quality_profile(profile_id):
    """Make a named profile the app-wide default AND push every setting it
    captures (AcoustID strictness, downsample, deep verify, import
    quality-filter/replace-lower-quality, lossy-copy) into the live global
    settings — not just the ranked-target ladder."""
    try:
        from database.music_database import MusicDatabase
        db = MusicDatabase()

        profile = db.apply_quality_profile_to_settings(profile_id)
        if profile is None:
            return jsonify({"success": False, "error": "Profile not found"}), 404

        from core.library2.wishlist_mirror import refresh_quality_profile_wishlist
        refresh_quality_profile_wishlist(db, profile_id)
        add_activity_item("", "Quality Profile Applied", f"Now using '{profile.get('preset', 'custom')}'", "Now")
        return jsonify({"success": True, "profile": profile})
    except Exception as e:
        logger.error(f"Error applying quality profile: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/quality-profile/custom/<int:profile_id>/update', methods=['POST'])
def update_custom_quality_profile(profile_id):
    """Overwrite a named profile's captured settings with whatever is
    currently on the Quality page (edit-in-place, keeps the name)."""
    try:
        from database.music_database import MusicDatabase
        db = MusicDatabase()

        data = request.get_json() or {}
        if not db.update_quality_profile(profile_id, data):
            return jsonify({"success": False, "error": "Profile not found"}), 404

        profiles = db.list_quality_profiles()
        # Editing the ACTIVE DEFAULT profile must also push the new values
        # into config.json — every profile-owned key the rest of the app
        # reads directly (AcoustID, lossy-copy, deep-verify, replace-lower-
        # quality). Without this, the row and config.json go out of sync,
        # and the next unrelated Settings save (which mirrors config -> the
        # default row via sync_default_quality_profile_from_config) silently
        # reverts this edit back to the stale config values.
        if any(p['id'] == profile_id and p.get('is_default') for p in profiles):
            db.apply_quality_profile_to_settings(profile_id)
            profiles = db.list_quality_profiles()

        from core.library2.wishlist_mirror import refresh_quality_profile_wishlist
        refresh_quality_profile_wishlist(db, profile_id)
        add_activity_item("", "Quality Profile Updated", f"Updated saved profile {profile_id}", "Now")
        return jsonify({"success": True, "profiles": profiles})
    except Exception as e:
        logger.error(f"Error updating quality profile: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/quality-profile/custom/<int:profile_id>', methods=['PUT'])
def rename_custom_quality_profile(profile_id):
    try:
        from database.music_database import MusicDatabase
        db = MusicDatabase()

        data = request.get_json() or {}
        name = str(data.get('name') or '').strip()
        if not name:
            return jsonify({"success": False, "error": "Name is required"}), 400

        ok, reason = db.rename_quality_profile(profile_id, name)
        if not ok:
            status = 404 if reason == "Profile not found" else 400
            return jsonify({"success": False, "error": reason}), status
        from core.library2.wishlist_mirror import refresh_quality_profile_wishlist
        refresh_quality_profile_wishlist(db, profile_id)
        return jsonify({"success": True, "profiles": db.list_quality_profiles()})
    except Exception as e:
        logger.error(f"Error renaming quality profile: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/quality-profile/custom/<int:profile_id>', methods=['DELETE'])
def delete_custom_quality_profile(profile_id):
    """Delete any profile, including the built-ins. Only refuses when it
    would leave zero profiles — deleting the current default auto-promotes
    another remaining profile first (see `MusicDatabase.delete_quality_profile`)."""
    try:
        from database.music_database import MusicDatabase
        db = MusicDatabase()

        ok, reason = db.delete_quality_profile(profile_id)
        if not ok:
            return jsonify({"success": False, "error": reason}), 400
        profiles = db.list_quality_profiles()
        from core.library2.wishlist_mirror import refresh_quality_profile_wishlist
        refresh_quality_profile_wishlist(
            db, next(p["id"] for p in profiles if p.get("is_default")))
        return jsonify({"success": True, "profiles": profiles})
    except Exception as e:
        logger.error(f"Error deleting quality profile: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
