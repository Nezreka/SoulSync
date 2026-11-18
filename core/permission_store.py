"""people and permissions: the glue between the rules (core.permissions) and
the music database (policy in metadata, permission_templates, the profile's
template_id and perm_overrides, and the old columns as a written-through
cache).

everything that reads raises PermissionsUnavailable when it can't build the
answer, so the caller fails closed instead of guessing. everything that
writes recomputes the effective map of whoever it reaches and writes their
old columns, so every check written before the overhaul keeps telling the
truth.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from core import permissions as P

logger = logging.getLogger(__name__)


class PermissionsUnavailable(Exception):
    """the effective map can't be built (a read failed, stored data is
    unreadable, a template is gone). the caller fails closed."""


class PermissionRequestError(ValueError):
    """a request body that can't be applied; ``status`` is the http status."""

    def __init__(self, message: str, status: int = 400, code: str = "invalid"):
        super().__init__(message)
        self.status = status
        self.code = code


def _loads(raw, what: str):
    if raw is None or raw == "":
        return {}
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        raise PermissionsUnavailable("%s is unreadable" % what) from None


# ── reads ────────────────────────────────────────────────────────────────

def load_policy(db) -> Dict[str, Any]:
    """the full, clean policy (open where nothing is saved)."""
    try:
        raw = db.get_permission_policy_raw()
    except Exception as exc:  # noqa: BLE001
        raise PermissionsUnavailable("policy read failed: %s" % exc) from exc
    try:
        return P.clean_layer(_loads(raw, "the global policy"), fill=P.OPEN_POLICY)
    except P.PermissionDataError as exc:
        raise PermissionsUnavailable("the global policy is unreadable: %s" % exc) from exc


def layers_for(db, profile: Mapping[str, Any]) -> Dict[str, Any]:
    """{policy, template, overrides, template_id, source} for a profile row
    (as get_profile returns it). source: admin | template | legacy."""
    pid = profile.get("id")
    try:
        inputs = db.get_permission_inputs(int(pid))
    except Exception as exc:  # noqa: BLE001
        raise PermissionsUnavailable("permission read failed for %s: %s" % (pid, exc)) from exc
    if inputs.get("missing"):
        raise PermissionsUnavailable("profile %s is gone" % pid)
    try:
        policy = P.clean_layer(_loads(inputs.get("policy"), "the global policy"), fill=P.OPEN_POLICY)
        overrides = P.clean_layer(_loads(inputs.get("overrides"), "personal changes"))
    except P.PermissionDataError as exc:
        raise PermissionsUnavailable(str(exc)) from exc
    tid = inputs.get("template_id")
    if P.is_admin_row(profile):
        source, template = "admin", None
    elif tid is None:
        # not on a template (not mapped yet, or kept on its own settings):
        # the old columns are the truth, read as a template
        source, template = "legacy", P.template_from_legacy(profile)
    else:
        if not inputs.get("template_found"):
            raise PermissionsUnavailable("template %s for profile %s is gone" % (tid, pid))
        source = "template"
        try:
            template = P.clean_layer(_loads(inputs.get("template_perms"), "a template"),
                                     fill=P.TEMPLATE_DEFAULTS)
        except P.PermissionDataError as exc:
            raise PermissionsUnavailable(str(exc)) from exc
    return {"policy": policy, "template": template, "overrides": overrides, "template_id": tid,
            "source": source}


def effective_for(db, profile: Mapping[str, Any]) -> Tuple[Dict[str, Any], str]:
    """(effective map, source) for a profile row. raises PermissionsUnavailable."""
    if P.is_admin_row(profile):
        return P.admin_map(), "admin"
    lay = layers_for(db, profile)
    try:
        eff = P.effective(lay["policy"], lay["template"],
                          lay["overrides"] if lay["source"] == "template" else {}, profile)
    except P.PermissionDataError as exc:
        raise PermissionsUnavailable(str(exc)) from exc
    return eff, lay["source"]


def context_for(db, profile_id) -> Dict[str, Any]:
    """a can() context for any profile id (background work: automations, the
    watchlist scanner). fails closed: an unreadable profile gets no rights."""
    try:
        pid = int(profile_id)
    except (TypeError, ValueError):
        return P.make_context(None, perms=None, source="none")
    if pid == 1:
        return P.make_context(1, is_admin=True, perms=P.admin_map(), source="admin")
    try:
        profile = db.get_profile(pid, raise_errors=True)
    except TypeError:
        profile = db.get_profile(pid)
    except Exception:  # noqa: BLE001
        return P.make_context(pid, perms=None, source="none")
    if not profile:
        return P.make_context(pid, perms=None, source="none")
    try:
        eff, source = effective_for(db, profile)
    except PermissionsUnavailable as exc:
        logger.warning("permissions: no map for profile %s (%s), refusing", pid, exc)
        return P.make_context(pid, perms=None, source="none")
    return P.make_context(pid, is_admin=P.is_admin_row(profile), perms=eff, source=source)


def needs_look(db) -> Dict[str, List[str]]:
    try:
        raw = db.get_metadata("perm_needs_look")
        data = json.loads(raw) if raw else {}
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 - a missing note is no note
        return {}


def signup_template_id(db) -> Optional[int]:
    try:
        raw = db.get_metadata("perm_signup_template")
        return int(raw) if raw else None
    except Exception:  # noqa: BLE001
        return None


# ── the cache ────────────────────────────────────────────────────────────

def write_cache(db, profile: Mapping[str, Any], eff: Mapping[str, Any]) -> bool:
    """write the old columns from ``eff`` when they'd behave differently.
    True when something was written."""
    if P.is_admin_row(profile):
        return False
    if not P.cache_matches(profile, eff):
        return False
    db.write_profile_permission_cache(int(profile["id"]), P.legacy_from_perms(eff))
    return True


def _members(db) -> List[Dict[str, Any]]:
    return [p for p in db.get_all_profiles() if not P.is_admin_row(p)]


def effective_all(db, *, policy=None, templates=None, overrides_for=None) -> Dict[int, Dict[str, Any]]:
    """effective maps for every member, optionally with a proposed policy,
    template set ({id: perms}) or per-profile overrides ({pid: map}), for
    impact previews. profiles on their own settings (legacy) are left out."""
    pol = load_policy(db) if policy is None else policy
    out: Dict[int, Dict[str, Any]] = {}
    for prof in _members(db):
        lay = layers_for(db, prof)
        if lay["source"] != "template":
            continue
        tpl = lay["template"]
        if templates is not None and lay["template_id"] in templates:
            tpl = templates[lay["template_id"]]
        ov = lay["overrides"]
        if overrides_for is not None and prof["id"] in overrides_for:
            ov = overrides_for[prof["id"]]
        out[int(prof["id"])] = P.effective(pol, tpl, ov, prof)
    return out


def recompute(db, profile_ids: Optional[Iterable[int]] = None) -> List[int]:
    """recompute and write the cache for these members (all when None).
    returns the ids whose columns were written."""
    wanted = None if profile_ids is None else {int(x) for x in profile_ids}
    written = []
    for prof in _members(db):
        if wanted is not None and int(prof["id"]) not in wanted:
            continue
        try:
            eff, source = effective_for(db, prof)
        except PermissionsUnavailable as exc:
            logger.error("permissions: cache for profile %s not written: %s", prof.get("id"), exc)
            continue
        if source != "template":
            continue
        if write_cache(db, prof, eff):
            written.append(int(prof["id"]))
    return written


def resync_after_legacy_update(db, profile_id: int) -> str:
    """an access field changed through the old profile editor. bring the
    layers in line with the columns: the column groups that now behave
    differently become personal changes (rows the old editor can't express,
    like a movies/tv split, are kept when their column didn't really move).
    if that can't reproduce the columns, the profile goes back on its own
    settings (template_id NULL) and the columns stay the truth.
    returns what happened: admin | legacy | unchanged | updated | unmapped."""
    prof = db.get_profile(int(profile_id))
    if not prof:
        return "missing"
    if P.is_admin_row(prof):
        return "admin"
    lay = layers_for(db, prof)
    if lay["source"] != "template":
        db.remap_legacy_profiles()
        return "legacy"
    eff = P.effective(lay["policy"], lay["template"], lay["overrides"], prof)
    if not P.cache_matches(prof, eff):
        return "unchanged"
    legacy, reasons = P.perms_from_legacy(prof)
    proj = P.legacy_behaviour(dict(prof, **P.legacy_from_perms(eff)))
    now = P.legacy_behaviour(prof)
    field_of = {"allowed_sides": "allowed_sides", "pages": "allowed_pages", "can_download": "can_download",
                "hide_explicit": "hide_explicit", "cap": "max_rating", "quota": "request_limit"}
    ov = dict(lay["overrides"])
    for key, col in field_of.items():
        if proj[key] != now[key]:
            for pid in P.LEGACY_GROUPS[col]:
                ov[pid] = legacy[pid]
    ov = P.diff_overrides(lay["template"], dict(lay["template"], **ov))
    eff2 = P.effective(lay["policy"], lay["template"], ov, prof)
    if reasons or P.cache_matches(prof, eff2):
        db.set_profile_permission_layers(int(profile_id), None, json.dumps(lay["overrides"], sort_keys=True))
        logger.warning("permissions: profile %s edited the old way into something its template can't "
                       "express; it runs on its own settings now", profile_id)
        return "unmapped"
    db.set_profile_permission_layers(int(profile_id), lay["template_id"], json.dumps(ov, sort_keys=True))
    return "updated"


# ── payloads and changes for the api ─────────────────────────────────────

def _template_public(t: Mapping[str, Any]) -> Dict[str, Any]:
    try:
        perms = P.clean_layer(_loads(t.get("perms"), "a template"), fill=P.TEMPLATE_DEFAULTS)
        ok = True
    except (P.PermissionDataError, PermissionsUnavailable):
        perms, ok = None, False
    try:
        sidebar = json.loads(t["sidebar"]) if t.get("sidebar") else None
    except (TypeError, ValueError):
        sidebar = None
    return {"id": t["id"], "slug": t.get("slug"), "name": t.get("name"), "description": t.get("description"),
            "builtin": bool(t.get("builtin")), "sort": t.get("sort"), "perms": perms, "readable": ok,
            "sidebar": sidebar, "followers": t.get("followers", []), "updated_at": t.get("updated_at")}


def templates_payload(db) -> Dict[str, Any]:
    return {"success": True, "templates": [_template_public(t) for t in db.list_permission_templates()],
            "signup_template_id": signup_template_id(db)}


def _followers(db, template_id) -> List[Dict[str, Any]]:
    return next((x.get("followers", []) for x in db.list_permission_templates()
                 if int(x["id"]) == int(template_id)), [])


def profile_payload(db, profile: Mapping[str, Any]) -> Dict[str, Any]:
    """GET /api/profiles/<id>/permissions."""
    lay = layers_for(db, profile)
    tpl_row = db.get_permission_template(lay["template_id"]) if lay["template_id"] is not None else None
    rows = P.explain(lay["policy"], lay["template"], lay["overrides"] if lay["source"] == "template" else {},
                     profile, base_source=lay["source"])
    eff = {r["id"]: r["value"] for r in rows}
    changed_categories = sorted({P.CATALOGUE[r["id"]].category for r in rows if r["override"]})
    nl = needs_look(db).get(str(profile.get("id")))
    return {
        "success": True,
        "profile": {"id": profile.get("id"), "name": profile.get("name"),
                    "is_admin": P.is_admin_row(profile)},
        "mode": lay["source"],                       # admin | template | legacy
        "template": ({"id": tpl_row["id"], "name": tpl_row["name"], "slug": tpl_row.get("slug")}
                     if tpl_row else None),
        "overrides": lay["overrides"] if lay["source"] == "template" else {},
        "effective": eff,
        "rows": rows,
        "changed_categories": changed_categories,
        "capped": [r["id"] for r in rows if r["capped"]],
        "needs_look": nl or [],
        "summary": P.summarize(eff, is_admin=P.is_admin_row(profile), name=profile.get("name") or "They"),
    }


def me_payload(db, profile: Mapping[str, Any]) -> Dict[str, Any]:
    """GET /api/profiles/me/permissions: the member's own effective map and
    summary, never the layers or anyone else's."""
    eff, source = effective_for(db, profile)
    is_admin = P.is_admin_row(profile)
    return {"success": True, "profile_id": profile.get("id"), "is_admin": is_admin, "source": source,
            "effective": eff, "summary": P.summarize(eff, is_admin=is_admin, name="You")}


def _resolve_reset(reset, categories) -> List[str]:
    ids: List[str] = []
    if reset == "all":
        return list(P.STORED_IDS)
    if reset is None:
        reset = []
    if not isinstance(reset, list):
        raise PermissionRequestError("reset is a list of ids or \"all\"")
    for pid in reset:
        if pid not in P.CATALOGUE or not P.CATALOGUE[pid].stored:
            raise PermissionRequestError("unknown permission %r" % pid)
        ids.append(pid)
    for cat in categories or []:
        if cat not in P.CATEGORY_IDS:
            raise PermissionRequestError("unknown category %r" % cat)
        ids.extend(r.id for r in P.CATALOGUE_ROWS if r.stored and (r.category == cat or cat in r.also_in))
    return ids


def _clean_set(body_set, layer: str = "O") -> Dict[str, Any]:
    """checked values for a layer: P policy, T template, O personal change.
    a row whose scope leaves the layer out is refused (own library is set
    per person, never by a template)."""
    if body_set is None:
        return {}
    if not isinstance(body_set, Mapping):
        raise PermissionRequestError("set is an object of permission: value")
    out = {}
    for pid, value in body_set.items():
        row = P.CATALOGUE.get(pid)
        if row is not None and row.stored and layer not in row.scope:
            raise PermissionRequestError("%s can't be set on a %s" % (
                pid, {"P": "global policy", "T": "template", "O": "person"}[layer]))
        try:
            out[pid] = P.coerce(pid, value)
        except P.PermissionDataError as exc:
            raise PermissionRequestError(str(exc)) from exc
    return out


def _admin_count(db) -> int:
    return sum(1 for p in db.get_all_profiles() if p.get("is_admin"))


def _set_admin_flag(db, profile_id: int, on: bool) -> None:
    if not db.update_profile(int(profile_id), is_admin=1 if on else 0):
        raise PermissionsUnavailable("couldn't save the admin switch for profile %s" % profile_id)


def change_profile(db, profile: Mapping[str, Any], body: Mapping[str, Any]) -> Dict[str, Any]:
    """PUT /api/profiles/<id>/permissions: {is_admin?, template_id?, set?,
    reset?, reset_categories?}. resets run before sets. a profile on its own
    settings (legacy) moves onto the template named, or the one its
    columns match best, with its columns as personal changes.

    is_admin (#2166 follow-up 4) goes in the same call, so the editor never
    leaves someone half changed: true makes them an admin (nothing else in
    the body applies to an admin; their template and changes stay stored
    for if it's ever turned off); false on an admin makes them a member and
    the rest of the body lands in the same call, and if that part fails the
    admin switch goes back. profile 1 stays an admin, and so does the last
    admin."""
    if "is_admin" in body and body["is_admin"] is not None:
        want = body["is_admin"]
        if not isinstance(want, bool):
            raise PermissionRequestError("is_admin is true or false")
        pid = int(profile["id"])
        if want and not P.is_admin_row(profile):
            _set_admin_flag(db, pid, True)
            out = profile_payload(db, db.get_profile(pid) or dict(profile, is_admin=True))
            out["changes"] = []
            out["admin_changed"] = True
            return out
        if not want and P.is_admin_row(profile):
            if pid == 1:
                raise PermissionRequestError("The main admin profile stays an admin.", 409, "owner_admin")
            if _admin_count(db) <= 1:
                raise PermissionRequestError("SoulSync needs at least one admin.", 409, "last_admin")
            _set_admin_flag(db, pid, False)
            rest = {k: v for k, v in body.items() if k != "is_admin"}
            try:
                out = change_profile(db, db.get_profile(pid) or dict(profile, is_admin=False), rest)
            except Exception:
                _set_admin_flag(db, pid, True)
                raise
            out["admin_changed"] = True
            return out
    if P.is_admin_row(profile):
        raise PermissionRequestError("Admins can use everything; there's nothing to set.", 409, "admin_profile")
    lay = layers_for(db, profile)
    tid = lay["template_id"]
    template = lay["template"]
    overrides = dict(lay["overrides"]) if lay["source"] == "template" else {}
    if lay["source"] == "legacy":
        # start from what the columns say, as changes on the closest template
        templates = [{"id": t["id"], "slug": t.get("slug"), "perms": _loads(t.get("perms"), "a template")}
                     for t in db.list_permission_templates()]
        legacy, _reasons = P.perms_from_legacy(profile)
        best = None
        for order, t in enumerate(templates):
            try:
                base = P.clean_layer(t["perms"], fill=P.TEMPLATE_DEFAULTS)
            except P.PermissionDataError:
                continue
            ov = P.diff_overrides(base, legacy)
            if best is None or (len(ov), order) < best[0]:
                best = ((len(ov), order), t["id"], base, ov)
        if best is None:
            raise PermissionRequestError("No template to start from.", 409, "no_template")
        _r, tid, template, overrides = best
    if "template_id" in body and body["template_id"] is not None:
        try:
            new_tid = int(body["template_id"])
        except (TypeError, ValueError):
            raise PermissionRequestError("template_id is a template's id") from None
        row = db.get_permission_template(new_tid)
        if not row:
            raise PermissionRequestError("No such template.", 404, "no_template")
        if new_tid != tid:
            tid = new_tid
            template = P.clean_layer(_loads(row["perms"], "a template"), fill=P.TEMPLATE_DEFAULTS)
            # "Follows" changed: personal changes stay, only the ones that now
            # equal the new template drop away
            overrides = P.diff_overrides(template, dict(template, **overrides))
    for pid in _resolve_reset(body.get("reset"), body.get("reset_categories")):
        overrides.pop(pid, None)
    for pid, value in _clean_set(body.get("set")).items():
        overrides[pid] = value
    overrides = P.diff_overrides(template, dict(template, **overrides))
    before, _src = effective_for(db, profile)
    after = P.effective(lay["policy"], template, overrides, profile)
    db.set_profile_permission_layers(int(profile["id"]), tid, json.dumps(overrides, sort_keys=True))
    fresh = db.get_profile(int(profile["id"])) or profile
    write_cache(db, fresh, after)
    nl = needs_look(db)
    if nl.pop(str(profile["id"]), None) is not None:
        db.set_metadata("perm_needs_look", json.dumps(nl, sort_keys=True))
    out = profile_payload(db, db.get_profile(int(profile["id"])) or fresh)
    out["changes"] = P.impact({int(profile["id"]): before}, {int(profile["id"]): after}).get(int(profile["id"]), [])
    return out


def _impact_payload(db, before: Mapping[int, Any], after: Mapping[int, Any]) -> Dict[str, Any]:
    names = {int(p["id"]): p.get("name") for p in db.get_all_profiles()}
    moved = P.impact(before, after)
    legacy = [{"id": int(p["id"]), "name": p.get("name")} for p in _members(db)
              if int(p["id"]) not in before]
    return {"reaches": len(before), "changes": [{"profile_id": pid, "name": names.get(pid), "rows": rows}
                                               for pid, rows in sorted(moved.items())],
            "not_following": legacy}


def change_policy(db, body: Mapping[str, Any], *, dry_run: bool = False) -> Dict[str, Any]:
    """PUT /api/permissions/policy: {set?, reset?}. reset ids go back to open."""
    current = load_policy(db)
    proposed = dict(current)
    for pid in _resolve_reset(body.get("reset"), body.get("reset_categories")):
        proposed[pid] = P._copy(P.OPEN_POLICY[pid])
    for pid, value in _clean_set(body.get("set"), "P").items():
        proposed[pid] = value
    before = effective_all(db)
    after = effective_all(db, policy=proposed)
    impact = _impact_payload(db, before, after)
    if not dry_run:
        stored = {k: v for k, v in proposed.items() if P._canon(v) != P._canon(P.OPEN_POLICY[k])}
        db.set_permission_policy_raw(json.dumps(stored, sort_keys=True))
        recompute(db)
    return {"success": True, "dry_run": dry_run, "policy": proposed if dry_run else load_policy(db),
            "impact": impact}


def policy_payload(db) -> Dict[str, Any]:
    pol = load_policy(db)
    return {"success": True, "policy": pol,
            "changed": sorted(k for k in pol if P._canon(pol[k]) != P._canon(P.OPEN_POLICY[k]))}


def _clean_sidebar(sidebar) -> Optional[str]:
    if sidebar is None:
        return None
    if not isinstance(sidebar, Mapping):
        raise PermissionRequestError("sidebar is {off: [...], locked: bool}")
    from core.ui_features import normalize_off
    return json.dumps({"off": normalize_off(sidebar.get("off")), "locked": bool(sidebar.get("locked"))})


def create_template(db, body: Mapping[str, Any]) -> Dict[str, Any]:
    """POST /api/permissions/templates: {name, description?, from_template_id?,
    perms?, sidebar?}. starts as a copy of from_template_id (else Adult's
    values), then perms on top."""
    name = str(body.get("name") or "").strip()
    if not name or len(name) > 60:
        raise PermissionRequestError("A template needs a name (60 characters at most).")
    if any((t.get("name") or "").lower() == name.lower() for t in db.list_permission_templates()):
        raise PermissionRequestError("There's already a template called that.", 409, "duplicate_name")
    base = dict(P.TEMPLATE_DEFAULTS)
    sidebar = None
    src_id = body.get("from_template_id")
    if src_id is not None:
        src = db.get_permission_template(int(src_id))
        if not src:
            raise PermissionRequestError("No such template to copy.", 404, "no_template")
        base = P.clean_layer(_loads(src["perms"], "a template"), fill=P.TEMPLATE_DEFAULTS)
        sidebar = src.get("sidebar")
    base.update(_clean_set(body.get("perms"), "T"))
    if "sidebar" in body:
        sidebar = _clean_sidebar(body.get("sidebar"))
    tid = db.create_permission_template(name=name, description=str(body.get("description") or "")[:200],
                                        perms=json.dumps(P.clean_layer(base, fill=P.TEMPLATE_DEFAULTS),
                                                         sort_keys=True),
                                        sidebar=sidebar)
    return {"success": True, "template": _template_public(dict(db.get_permission_template(tid), followers=[]))}


def change_template(db, template_id: int, body: Mapping[str, Any], *, dry_run: bool = False) -> Dict[str, Any]:
    """PUT /api/permissions/templates/<id>: {name?, description?, perms?,
    reset?, sidebar?, sort?}. perms merge onto the template; reset ids go
    back to the catalogue default. returns the impact on its followers."""
    row = db.get_permission_template(int(template_id))
    if not row:
        raise PermissionRequestError("No such template.", 404, "no_template")
    try:
        perms = P.clean_layer(_loads(row["perms"], "a template"), fill=P.TEMPLATE_DEFAULTS)
    except (P.PermissionDataError, PermissionsUnavailable) as exc:
        raise PermissionRequestError("This template is unreadable: %s" % exc, 409, "unreadable") from exc
    for pid in _resolve_reset(body.get("reset"), body.get("reset_categories")):
        perms[pid] = P._copy(P.TEMPLATE_DEFAULTS[pid])
    perms.update(_clean_set(body.get("perms"), "T"))
    fields: Dict[str, Any] = {}
    if "name" in body:
        name = str(body.get("name") or "").strip()
        if not name or len(name) > 60:
            raise PermissionRequestError("A template needs a name (60 characters at most).")
        if any((t.get("name") or "").lower() == name.lower() and int(t["id"]) != int(template_id)
               for t in db.list_permission_templates()):
            raise PermissionRequestError("There's already a template called that.", 409, "duplicate_name")
        fields["name"] = name
    if "description" in body:
        fields["description"] = str(body.get("description") or "")[:200]
    if "sidebar" in body:
        fields["sidebar"] = _clean_sidebar(body.get("sidebar"))
    if "sort" in body:
        try:
            fields["sort"] = int(body["sort"])
        except (TypeError, ValueError):
            raise PermissionRequestError("sort is a number") from None
    before = effective_all(db)
    after = effective_all(db, templates={int(template_id): perms})
    impact = _impact_payload(db, before, after)
    followers = _followers(db, template_id)
    if not dry_run:
        fields["perms"] = json.dumps(perms, sort_keys=True)
        db.update_permission_template(int(template_id), **fields)
        if followers:
            recompute(db, [f["id"] for f in followers])
    t = db.get_permission_template(int(template_id))
    out_t = _template_public(dict(t, followers=followers))
    if dry_run:
        out_t["perms"] = perms
    return {"success": True, "dry_run": dry_run, "template": out_t, "impact": impact}


def delete_template(db, template_id: int, reassign_to=None) -> Dict[str, Any]:
    row = db.get_permission_template(int(template_id))
    if not row:
        raise PermissionRequestError("No such template.", 404, "no_template")
    if signup_template_id(db) == int(template_id):
        raise PermissionRequestError("New people start on this template. Pick another one for them first.",
                                     409, "signup_template")
    target = None
    if reassign_to is not None:
        try:
            target = int(reassign_to)
        except (TypeError, ValueError):
            raise PermissionRequestError("reassign_to is a template's id") from None
        if target == int(template_id) or not db.get_permission_template(target):
            raise PermissionRequestError("Pick another template to move them to.", 400, "no_template")
    followers = _followers(db, template_id)
    if followers and target is None:
        raise PermissionRequestError("%d people follow this template. Move them to another one first."
                                     % len(followers), 409, "has_followers")
    if not db.delete_permission_template(int(template_id), reassign_to=target):
        raise PermissionRequestError("Couldn't delete it.", 409, "not_deleted")
    if followers:
        recompute(db, [f["id"] for f in followers])
    return {"success": True, "deleted": int(template_id), "moved": followers}


def set_signup_template(db, template_id) -> Dict[str, Any]:
    try:
        tid = int(template_id)
    except (TypeError, ValueError):
        raise PermissionRequestError("template_id is a template's id") from None
    if not db.get_permission_template(tid):
        raise PermissionRequestError("No such template.", 404, "no_template")
    db.set_metadata("perm_signup_template", str(tid))
    return {"success": True, "signup_template_id": tid}


# ── sign-ups (#2166 follow-up 7) ─────────────────────────────────────────
#
# a profile made by sign-in (plex, oidc) follows the sign-up template
# ("New people start as" under People > Templates), with no personal
# changes. the old System > Security settings (the side it starts on, a
# request limit, and "new profiles can download" per sign-in method) moved
# there once: when they gave something other than Guest, they became a
# "New sign-ups" template that new people start as.

SIGNUP_MOVED_KEY = "perm_signup_settings_moved"
SIGNUP_TEMPLATE_NAME = "New sign-ups"
_GET_ROWS = ("music.get", "movies.get", "tv.get", "books.get", "podcasts.get")


def signup_settings_perms(get, guest: Mapping[str, Any]) -> Dict[str, Any]:
    """what the old security.signup_* and *_default_can_download settings
    gave a new profile, as a full template map on top of Guest. downloads:
    the old one switch covered every kind, so it maps to every get row, and
    only when every sign-in method that's turned on said yes (a method that
    said no keeps asking; the stricter one wins)."""
    from core.security.signup_defaults import signup_access
    perms = dict(guest, **P.signup_overrides(signup_access(get), guest))
    methods = [m for m in ("plex", "oidc") if bool(get("security.%s_signin" % m, False))]
    if methods and all(bool(get("security.%s_signin_default_can_download" % m, False)) for m in methods):
        for row in _GET_ROWS:
            perms[row] = "download"
    return P.clean_layer(perms, fill=P.TEMPLATE_DEFAULTS)


_SIGNUP_KEYS = ("security.signup_sides", "security.signup_request_limit", "security.signup_request_limit_days",
                "security.plex_signin_default_can_download", "security.oidc_signin_default_can_download")


def _signup_settings_matter(get) -> bool:
    """did the old settings ever shape a sign-up? only when one was saved,
    or a sign-in that makes profiles is on (then the shipped defaults were
    what new people got). an install that never did either just starts new
    people as Guest, with no extra template."""
    if any(get(key, None) is not None for key in _SIGNUP_KEYS):
        return True
    return any(bool(get("security.%s_signin" % m, False)) and bool(get("security.%s_signin_auto_create" % m, True))
               for m in ("plex", "oidc"))


def move_signup_settings(db, get) -> Optional[int]:
    """once per install: the old sign-up settings become the sign-up
    template. only while the sign-up template is still the seeded Guest (an
    admin who already picked one keeps it). returns the sign-up template id.
    a failure logs and tries again next time; sign-up keeps the old columns
    meanwhile (place_new_signup does nothing until this has run)."""
    try:
        if db.get_metadata(SIGNUP_MOVED_KEY) == "1":
            return signup_template_id(db)
        tid = signup_template_id(db)
        row = db.get_permission_template(tid) if tid is not None else None
        if row and row.get("slug") == "guest" and get is not None and _signup_settings_matter(get):
            guest = P.clean_layer(_loads(row.get("perms"), "a template"), fill=P.TEMPLATE_DEFAULTS)
            wanted = signup_settings_perms(get, guest)
            if P.diff_overrides(guest, wanted):
                existing = next((t for t in db.list_permission_templates()
                                 if (t.get("name") or "").lower() == SIGNUP_TEMPLATE_NAME.lower()), None)
                if existing:
                    new_id = int(existing["id"])
                else:
                    new_id = db.create_permission_template(
                        name=SIGNUP_TEMPLATE_NAME,
                        description="What people who sign in with Plex or OIDC start with.",
                        perms=json.dumps(wanted, sort_keys=True), sidebar=row.get("sidebar"))
                set_signup_template(db, new_id)
                tid = new_id
                logger.info("permissions: the old sign-up settings are the %r template now (%s)",
                            SIGNUP_TEMPLATE_NAME, new_id)
        db.set_metadata(SIGNUP_MOVED_KEY, "1")
        return tid
    except Exception as exc:  # noqa: BLE001 - try again next time
        logger.warning("permissions: moving the sign-up settings failed, will retry: %s", exc)
        return None


def place_on_template(db, profile_id, template_id) -> bool:
    """a brand new member follows ``template_id`` with no personal changes,
    and its cache columns follow it. False (and the profile keeps the
    columns it was made with) when the template is gone or unreadable."""
    try:
        tid = int(template_id)
        if not db.get_permission_template(tid):
            return False
        db.set_profile_permission_layers(int(profile_id), tid, "{}")
        prof = db.get_profile(int(profile_id))
        if not prof:
            return False
        eff, source = effective_for(db, prof)
        if source != "template":
            return False
        write_cache(db, prof, eff)
        return True
    except Exception as exc:  # noqa: BLE001 - the columns it was made with still hold
        logger.warning("permissions: profile %s not placed on template %s: %s", profile_id, template_id, exc)
        return False


def place_new_signup(db, profile_id) -> bool:
    """a profile sign-in just made follows the sign-up template. only once
    the old settings have moved (until then the columns sign-up wrote from
    them are the same thing)."""
    try:
        if db.get_metadata(SIGNUP_MOVED_KEY) != "1":
            return False
    except Exception:  # noqa: BLE001
        return False
    tid = signup_template_id(db)
    return tid is not None and place_on_template(db, profile_id, tid)


# ── music, for the routes and the wishlist (piece A) ─────────────────────

_MUSIC_CLOSED = {"mode": "none", "quota": None, "hide_explicit": True}


def music_rights(db, profile: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """what a profile row may do with music, from its effective map:
    {"mode": "download" | "ask" | "none", "quota": {limit, days} | None,
    "hide_explicit": bool}. the mode is music.get under music.access (a
    video-only member gets none), the quota is requests.limit (asks only).
    admins: download, no quota, nothing hidden. a map that can't be built is
    no music and everything hidden: the caller fails closed."""
    if not profile:
        return dict(_MUSIC_CLOSED)
    if P.is_admin_row(profile):
        return {"mode": "download", "quota": None, "hide_explicit": False}
    try:
        eff, _source = effective_for(db, profile)
    except Exception as exc:  # noqa: BLE001 - PermissionsUnavailable or a db that can't answer
        logger.warning("permissions: no music rights for profile %s (%s), refusing", profile.get("id"), exc)
        return dict(_MUSIC_CLOSED)
    ctx = P.make_context(profile.get("id"), perms=eff)
    mode = "download" if P.can(ctx, "music.get", "download") else "ask" if P.can(ctx, "music.get", "ask") else "none"
    limit = eff.get("requests.limit") or {}
    try:
        n, days = int(limit.get("n") or 0), int(limit.get("days") or 7)
    except (TypeError, ValueError):
        n, days = 1, P.DAYS_MAX
    quota = {"limit": n, "days": max(1, days)} if n > 0 and mode == "ask" else None
    return {"mode": mode, "quota": quota, "hide_explicit": P.can(ctx, "music.hide_explicit")}


WISH_NOT_ALLOWED = "Getting music isn't part of your profile. Ask an admin if you'd like it."
WISH_EXPLICIT = "explicit music is hidden for this profile"


def wish_refusal(db, profile_id, track: Optional[Mapping[str, Any]] = None) -> Optional[str]:
    """why a music wishlist add for this profile is refused, or None.

    every add funnels through the database's add_to_wishlist_detailed, so this
    is the one place a member who can't request music (music.get none, or no
    music side at all) or whose explicit music is hidden is held to it,
    whichever page, job or scan made the add. a profile that isn't there is
    nobody's to refuse (the add's own checks answer for it)."""
    try:
        pid = int(profile_id)
    except (TypeError, ValueError):
        return None
    if pid == 1:
        return None
    try:
        profile = db.get_profile(pid)
    except Exception:  # noqa: BLE001 - unreadable is not "allowed"
        return WISH_NOT_ALLOWED
    if not profile or P.is_admin_row(profile):
        return None
    rights = music_rights(db, profile)
    if rights["mode"] == "none":
        return WISH_NOT_ALLOWED
    if rights["hide_explicit"] and isinstance(track, Mapping):
        from core.content_filter import is_explicit
        album = track.get("album") if isinstance(track.get("album"), Mapping) else {}
        if is_explicit(track.get("explicit")) or is_explicit(album.get("explicit")):
            return WISH_EXPLICIT
    return None


__all__ = ["PermissionsUnavailable", "PermissionRequestError", "load_policy", "layers_for", "effective_for",
           "context_for", "needs_look", "signup_template_id", "write_cache", "effective_all", "recompute",
           "resync_after_legacy_update", "templates_payload", "profile_payload", "me_payload",
           "change_profile", "change_policy", "policy_payload", "create_template", "change_template",
           "delete_template", "set_signup_template", "music_rights", "wish_refusal",
           "SIGNUP_MOVED_KEY", "SIGNUP_TEMPLATE_NAME", "signup_settings_perms", "move_signup_settings",
           "place_on_template", "place_new_signup",
           "WISH_NOT_ALLOWED", "WISH_EXPLICIT"]
