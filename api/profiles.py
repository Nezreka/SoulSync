"""
Profile management endpoints — list, create, update, delete profiles.
"""

from flask import request
from database.music_database import get_database
from .auth import require_api_key
from .helpers import api_success, api_error


def register_routes(bp):

    @bp.route("/profiles", methods=["GET"])
    @require_api_key
    def list_profiles():
        """List all profiles."""
        try:
            db = get_database()
            profiles = db.get_all_profiles()
            return api_success({"profiles": profiles})
        except Exception as e:
            return api_error("PROFILE_ERROR", str(e), 500)

    @bp.route("/profiles/<int:profile_id>", methods=["GET"])
    @require_api_key
    def get_profile(profile_id):
        """Get a single profile by ID."""
        try:
            db = get_database()
            profile = db.get_profile(profile_id)
            if not profile:
                return api_error("NOT_FOUND", f"Profile {profile_id} not found.", 404)
            return api_success({"profile": profile})
        except Exception as e:
            return api_error("PROFILE_ERROR", str(e), 500)

    @bp.route("/profiles", methods=["POST"])
    @require_api_key
    def create_profile():
        """Create a new profile.

        Body: {"name": "...", "avatar_color": "#hex", "avatar_url": "...", "is_admin": false}
        """
        body = request.get_json(silent=True) or {}
        name = body.get("name", "").strip()

        if not name:
            return api_error("BAD_REQUEST", "Missing 'name' in body.", 400)

        avatar_color = body.get("avatar_color", "#6366f1")
        avatar_url = body.get("avatar_url")
        is_admin = bool(body.get("is_admin", False))

        # Handle optional PIN
        pin_hash = None
        pin = body.get("pin")
        if pin:
            from werkzeug.security import generate_password_hash
            pin_hash = generate_password_hash(pin, method="pbkdf2:sha256")

        try:
            db = get_database()
            profile_id = db.create_profile(
                name=name,
                avatar_color=avatar_color,
                pin_hash=pin_hash,
                is_admin=is_admin,
                avatar_url=avatar_url,
            )
            if profile_id:
                profile = db.get_profile(profile_id)
                return api_success({"profile": profile}, status=201)
            return api_error("CONFLICT", "Profile name already exists.", 409)
        except Exception as e:
            return api_error("PROFILE_ERROR", str(e), 500)

    @bp.route("/profiles/<int:profile_id>", methods=["PUT"])
    @require_api_key
    def update_profile(profile_id):
        """Update a profile.

        Body: {"name": "...", "avatar_color": "#hex", "avatar_url": "...", "is_admin": false}
        """
        body = request.get_json(silent=True) or {}

        kwargs = {}
        if "name" in body:
            kwargs["name"] = body["name"].strip()
        if "avatar_color" in body:
            kwargs["avatar_color"] = body["avatar_color"]
        if "avatar_url" in body:
            kwargs["avatar_url"] = body["avatar_url"]
        if "is_admin" in body:
            kwargs["is_admin"] = int(bool(body["is_admin"]))
        if "pin" in body:
            pin = body["pin"]
            if pin:
                from werkzeug.security import generate_password_hash
                kwargs["pin_hash"] = generate_password_hash(pin, method="pbkdf2:sha256")
            else:
                kwargs["pin_hash"] = None  # Clear PIN

        if not kwargs:
            return api_error("BAD_REQUEST", "No valid fields to update.", 400)

        try:
            db = get_database()
            ok = db.update_profile(profile_id, **kwargs)
            if ok:
                profile = db.get_profile(profile_id)
                return api_success({"profile": profile})
            return api_error("NOT_FOUND", f"Profile {profile_id} not found.", 404)
        except Exception as e:
            return api_error("PROFILE_ERROR", str(e), 500)

    @bp.route("/profiles/<int:profile_id>", methods=["DELETE"])
    @require_api_key
    def delete_profile(profile_id):
        """Delete a profile and all its data. Cannot delete profile 1 (admin)."""
        if profile_id == 1:
            return api_error("FORBIDDEN", "Cannot delete the default admin profile.", 403)

        try:
            db = get_database()
            ok = db.delete_profile(profile_id)
            if ok:
                return api_success({"message": f"Profile {profile_id} deleted."})
            return api_error("NOT_FOUND", f"Profile {profile_id} not found.", 404)
        except Exception as e:
            return api_error("PROFILE_ERROR", str(e), 500)
