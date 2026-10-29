"""Daily Mixes - taste-clustered blends of owned + discovery tracks.

the spotify feature, done with the one advantage spotify doesn't have
here: the library is local. each mix is mostly tracks the user owns
(instantly playable): the cluster's artists, a few owned artists that sit
next to them and get played less, and a handful of discovery tracks from
their similar artists (one click from download).

clustering: recency-weighted top artists from listening_history, grouped by
how they actually go together. the signals, strongest first:
- played in the same listening sessions (your own co-listening)
- one is in the other's similar list (similar_artists, resolved through
  SOURCE ids - never artists.id, see the id-smear bug)
- they share similar artists (neighbourhood overlap)
- shared genres, weighted by how rare the genre is in your top artists.
  "pop" sits on half of them, so on its own it glued katy perry to
  virtual mage and a disney soundtrack to russian rave
a candidate joins a mix only when it fits the mix as a whole (average
affinity), not just one member, so a mix can't chain-drift.

daily: a mix is built per local calendar day. the tracks are a weighted
draw (favourites likelier, deep cuts possible) seeded by the day, and
anything served in the last few days counts for less, so tomorrow's mix
is the same taste with different songs. mix numbers follow their artists
from one day to the next, so Daily Mix 1 stays the same flavour on the
server.

storage: the whole payload - full track dicts, not pool ids - goes into
discovery_curated_playlists under 'daily_mixes_v2'. pool rotation can
never shrink these (the fresh-tape hydration lesson).
"""

import json
import math
import random
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from core.discovery.explain import explanation
from utils.logging_config import get_logger

logger = get_logger("personalized.daily_mixes")

CURATED_KEY = "daily_mixes_v2"
PAYLOAD_VERSION = 5
MAX_MIXES = 6
MIX_SIZE = 40
DISCOVERY_PER_MIX = 6
NEIGHBOURS_PER_MIX = 6      # owned tracks by owned artists next to the cluster
MAX_SEEDS = 60
MAX_ARTISTS_PER_MIX = 12
MIN_ARTISTS_PER_MIX = 2
MIN_OWNED_TRACKS = 5        # a mix that can barely play isn't a mix
MIN_MIX_TRACKS = 15         # nor is one that plays the same 8 songs every day
# safety net only. a mix is rebuilt when the local day changes
TTL_HOURS = 24
SESSION_GAP_MINUTES = 30
SESSION_DAYS = 365
HISTORY_DAYS = 3            # days of served tracks that count against a repeat
REPEAT_PENALTY = 0.3
MIN_TRACK_MS = 60_000

# affinity weights and the join rule, tuned on a real 160k-play history
W_SESSION, W_DIRECT, W_NEIGHBOURS, W_GENRE = 0.40, 0.30, 0.20, 0.10
JOIN_MEAN = 0.10            # average fit to the whole mix
JOIN_MAX = 0.18             # and a clear tie to at least one member
ANCHOR_SHARE = 0.2          # a lone artist this heavy still gets a mix
MAX_PER_ARTIST = 6          # per mix, before a short mix relaxes it
DISCOVERY_FLOOR = 0.25      # of the best discovery artist's pull


def _norm(text: Any) -> str:
    return str(text or "").strip().lower()


# ── track identity (pure) ─────────────────────────────────────────────────

# version noise that doesn't make a different song. remix, live, acoustic
# and instrumental do, so they stay
_VERSION_WORDS = (
    r"remaster(?:ed)?(?: \d{4})?|\d{4} remaster(?:ed)?|deluxe(?: edition| version)?|"
    r"album version|single version|radio edit|explicit|clean|mono|stereo|"
    r"bonus track|sped up|speed up|slowed(?: \+ reverb| and reverb| & reverb)?|"
    r"reverb|nightcore|super slowed|feat\.? [^)\]]*|ft\.? [^)\]]*|with [^)\]]*"
)
_BRACKETED = re.compile(r"\s*[\(\[]\s*(?:" + _VERSION_WORDS + r")\s*[\)\]]", re.I)
_DASHED = re.compile(r"\s+-\s+(?:" + _VERSION_WORDS + r")\s*$", re.I)
_TRAILING_FEAT = re.compile(r"\s+(?:feat\.?|ft\.?)\s+.*$", re.I)
_SKIT = re.compile(r"\b(?:intro|outro|skit|interlude)\b", re.I)


def title_key(title: Any) -> str:
    """the song a title names, without version noise. 'DtMF', 'DtMF (Explicit)'
    and 'PRESTIGE (SPED UP)' vs 'PRESTIGE' are one song each, so a mix holds
    one copy."""
    t = str(title or "")
    for _ in range(3):      # "Song (feat. X) [Remastered 2011]" takes two passes
        t2 = _DASHED.sub("", _BRACKETED.sub("", t))
        if t2 == t:
            break
        t = t2
    t = _TRAILING_FEAT.sub("", t)
    return re.sub(r"\s+", " ", t).strip().casefold()


def track_key(artist: Any, title: Any) -> str:
    return f"{_norm(artist)}|{title_key(title)}"


_ANY_BRACKET = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")
_ANY_DASHED = re.compile(r"\s+-\s+.*$")


def song_key(artist: Any, title: Any) -> str:
    """the song, whatever the version: 'Deadzone' and 'Deadzone (Denis Dekay
    Remix)' are one song, so a mix carries one of them (the one you play
    most, or the most popular)."""
    family = _ANY_DASHED.sub("", _ANY_BRACKET.sub("", str(title or "")))
    family = re.sub(r"\s+", " ", _TRAILING_FEAT.sub("", family)).strip().casefold()
    return f"{_norm(artist)}|{family or title_key(title)}"


def _alnum(text: Any) -> str:
    return "".join(ch for ch in str(text or "").casefold() if ch.isalnum())


def _artist_of(track: dict) -> str:
    artists = track.get("artists") or []
    first = artists[0] if artists else {}
    return first.get("name", "") if isinstance(first, dict) else str(first or "")


def _album_of(track: dict) -> str:
    album = track.get("album")
    return album.get("name", "") if isinstance(album, dict) else str(album or "")


def is_filler(title: Any, duration_ms: Any) -> bool:
    """too short to be a song, or a short skit/intro"""
    try:
        ms = int(duration_ms or 0)
    except (TypeError, ValueError):
        ms = 0
    if 0 < ms < MIN_TRACK_MS:
        return True
    return bool(_SKIT.search(str(title or ""))) and 0 < ms < 120_000


_HOLIDAY = ("christmas", "xmas", "x-mas", "navidad", "noel", "jingle", "santa", "holiday")


def in_season(track: dict, today: date) -> bool:
    """a christmas song is for november and december"""
    if today.month in (11, 12):
        return True
    text = f"{track.get('name', '')} {_album_of(track)}".lower()
    return not any(word in text for word in _HOLIDAY)


# ── seeds (pure) ──────────────────────────────────────────────────────────

_CREDIT_SPLIT = re.compile(r"\s*(?:,|&| x | feat\.? | ft\.? | with | and )\s*", re.I)


def fold_credit_seeds(seeds: Sequence[dict], owned: Set[str]) -> List[dict]:
    """history credits are often joined strings ("Bad Bunny, Jhayco"). a credit
    that isn't an owned artist hands its weight to its first artist that is,
    so those plays count instead of founding a seed that can never play."""
    merged: Dict[str, dict] = {}
    order: List[str] = []
    for s in seeds:
        name = str(s.get("name") or "").strip()
        if not name:
            continue
        key = _norm(name)
        if key not in owned:
            for part in _CREDIT_SPLIT.split(name):
                if _norm(part) in owned:
                    name, key = part.strip(), _norm(part)
                    break
        if key not in merged:
            merged[key] = {"name": name, "weight": 0.0}
            order.append(key)
        merged[key]["weight"] += float(s.get("weight") or 0)
    return [merged[k] for k in order]


# ── clustering (pure) ─────────────────────────────────────────────────────


def session_cooccurrence(plays: Iterable[Tuple[str, datetime]],
                         gap_minutes: int = SESSION_GAP_MINUTES,
                         min_shared: int = 3) -> Dict[Tuple[str, str], float]:
    """how much more often two artists share a sitting than chance says, as
    normalized pmi clipped to 0..1. raw counts would glue everything together
    for someone who shuffles the whole library; pmi only rewards pairs you
    put together on purpose. plays must be in time order. a session ends
    after gap_minutes of silence. pairs sharing fewer than min_shared
    sessions say nothing."""
    gap = timedelta(minutes=gap_minutes)
    per_artist: Dict[str, int] = {}
    pairs: Dict[Tuple[str, str], int] = {}
    current: Set[str] = set()
    last: Optional[datetime] = None
    sessions = 0

    def close():
        nonlocal sessions
        if current:
            sessions += 1
        for a in current:
            per_artist[a] = per_artist.get(a, 0) + 1
        ordered = sorted(current)
        for i, a in enumerate(ordered):
            for b in ordered[i + 1:]:
                pairs[(a, b)] = pairs.get((a, b), 0) + 1

    for artist, at in plays:
        if last is not None and at - last > gap:
            close()
            current = set()
        current.add(artist)
        last = at
    close()
    out: Dict[Tuple[str, str], float] = {}
    for (a, b), n in pairs.items():
        if n < min_shared or n >= sessions:
            continue
        p_ab = n / sessions
        pmi = math.log(p_ab / ((per_artist[a] / sessions) * (per_artist[b] / sessions)))
        npmi = pmi / -math.log(p_ab)
        if npmi > 0:
            out[(a, b)] = min(1.0, npmi)
    return out


def affinity_matrix(names: Sequence[str],
                    similars_by_seed: Dict[str, List[dict]],
                    genres_by_name: Dict[str, List[str]],
                    cooccurrence: Optional[Dict[Tuple[str, str], float]] = None,
                    ) -> Dict[Tuple[str, str], float]:
    """0..1 affinity for every pair of (lowercased) names that has any."""
    sims: Dict[str, Dict[str, float]] = {}
    for seed, rows in (similars_by_seed or {}).items():
        d = sims.setdefault(_norm(seed), {})
        for r in rows or []:
            nm = _norm(r.get("name"))
            if nm:
                d[nm] = max(d.get(nm, 0.0), float(r.get("score", 1.0) or 0.0))
    genre_sets = {n: {_norm(g) for g in (genres_by_name.get(n) or []) if _norm(g)}
                  for n in names}
    df: Dict[str, int] = {}
    for gs in genre_sets.values():
        for g in gs:
            df[g] = df.get(g, 0) + 1
    total = max(1, len(names))
    idf = {g: math.log((1 + total) / (1 + c)) + 1.0 for g, c in df.items()}
    cooc = cooccurrence or {}
    # no co-listening and no similarity data (a fresh install): genre is all
    # there is, so it carries the whole score instead of a tiebreaker's share
    w_genre = W_GENRE if (cooc or any(sims.values())) else 1.0

    out: Dict[Tuple[str, str], float] = {}
    for i, a in enumerate(names):
        sa = sims.get(a, {})
        for b in names[i + 1:]:
            sb = sims.get(b, {})
            direct = max(sa.get(b, 0.0), sb.get(a, 0.0))
            neighbours = 0.0
            if len(sa) >= 3 and len(sb) >= 3:
                neighbours = min(1.0, 2.0 * len(sa.keys() & sb.keys()) / min(len(sa), len(sb)))
            genre = 0.0
            ga, gb = genre_sets.get(a, set()), genre_sets.get(b, set())
            if ga and gb:
                union = sum(idf[g] for g in ga | gb)
                genre = sum(idf[g] for g in ga & gb) / union if union else 0.0
            session = cooc.get((a, b), cooc.get((b, a), 0.0))
            score = (W_SESSION * session + W_DIRECT * direct
                     + W_NEIGHBOURS * neighbours + w_genre * genre)
            if score > 0:
                out[(a, b)] = out[(b, a)] = score
    return out


def cluster_seeds(
    seeds: Sequence[dict],
    similars_by_seed: Dict[str, List[dict]],
    genres_by_name: Dict[str, List[str]],
    *,
    cooccurrence: Optional[Dict[Tuple[str, str], float]] = None,
    max_clusters: int = MAX_MIXES,
    max_per: int = MAX_ARTISTS_PER_MIX,
    min_per: int = MIN_ARTISTS_PER_MIX,
    anchor_share: float = ANCHOR_SHARE,
) -> List[Dict[str, Any]]:
    """Group seed artists into taste clusters.

    The heaviest unassigned seed founds a cluster, then the candidate that
    fits the cluster best (average affinity to every member) joins, while it
    fits well enough. A founder nothing fits still anchors a mix of its own
    when it is one of your big artists (anchor_share of the heaviest seed's
    weight): you play bad bunny alone, so bad bunny gets a mix, filled out
    later by his neighbours. A light lone founder founds nothing and costs
    no slot. Deterministic - the daily variation lives in track picking.
    """
    ordered = [s for s in sorted(seeds, key=lambda s: -float(s.get("weight", 0)))
               if _norm(s.get("name"))]
    names: List[str] = []
    for s in ordered:
        if _norm(s["name"]) not in names:
            names.append(_norm(s["name"]))
    display = {}
    for s in ordered:
        display.setdefault(_norm(s["name"]), str(s["name"]))
    weight = {_norm(s["name"]): float(s.get("weight", 0)) for s in ordered}
    aff = affinity_matrix(names, similars_by_seed, genres_by_name, cooccurrence)
    heaviest = max(weight.values(), default=0.0)

    def fit(cand: str, members: List[str]) -> Tuple[float, float]:
        scores = [aff.get((cand, m), 0.0) for m in members]
        return sum(scores) / len(scores), max(scores)

    unassigned = list(names)
    clusters: List[List[str]] = []
    while unassigned:
        nucleus = unassigned.pop(0)
        members = [nucleus]
        while len(members) < max_per:
            best, best_fit = None, (0.0, 0.0)
            for cand in unassigned:
                mean, top = fit(cand, members)
                if mean >= JOIN_MEAN and top >= JOIN_MAX and mean > best_fit[0]:
                    best, best_fit = cand, (mean, top)
            if best is None:
                break
            members.append(best)
            unassigned.remove(best)
        if len(members) >= min_per or weight[nucleus] >= anchor_share * heaviest:
            clusters.append(members)
    # the mixes you'd play most win the slots: three rave artists together
    # outweigh one lighter artist on its own. stable, so ties keep founding order
    clusters = sorted(clusters, key=lambda c: -sum(weight[m] for m in c))[:max_clusters]

    out = []
    genre_df: Dict[str, int] = {}
    for n in names:
        for g in {_norm(x) for x in genres_by_name.get(n) or []}:
            genre_df[g] = genre_df.get(g, 0) + 1
    for members in clusters:
        votes: Dict[str, float] = {}
        labels: Dict[str, str] = {}
        for m in members:
            for g in genres_by_name.get(m) or []:
                key = _norm(g)
                if not key:
                    continue
                labels.setdefault(key, str(g).strip())
                # the label is what sets this mix apart: a genre on half of
                # your artists names nothing
                rarity = math.log((1 + len(names)) / (1 + genre_df.get(key, 0))) + 0.25
                votes[key] = votes.get(key, 0.0) + rarity * math.log1p(weight.get(m, 1.0))
        top_genre = max(votes, key=lambda g: (votes[g], g)) if votes else ""
        label = labels.get(top_genre, "")
        out.append({
            "artists": [display[m] for m in members],
            "genre": (label[:1].upper() + label[1:]) if label else "",
        })
    return out


# ── picking (pure) ────────────────────────────────────────────────────────


def weighted_order(rows: Sequence[dict], weights: Sequence[float],
                   rng: random.Random) -> List[dict]:
    """every row, heavier weights likelier to come first (u^(1/w) keys)."""
    keyed = [(rng.random() ** (1.0 / w), i) for i, w in enumerate(weights) if w > 0]
    keyed.sort(key=lambda kv: kv[0], reverse=True)
    return [rows[i] for _, i in keyed]


def allocate(weights: Dict[str, float], available: Dict[str, int], total: int) -> Dict[str, int]:
    """split total slots across artists by sqrt(weight), so the favourite leads
    without drowning everyone else. nobody gets more than they have, and what
    one can't use goes to the rest."""
    alloc = {a: 0 for a in weights}
    room = {a: int(available.get(a, 0)) for a in weights}
    left = min(total, sum(room.values()))
    while left > 0:
        open_ = [a for a in weights if alloc[a] < room[a]]
        if not open_:
            break
        share = {a: math.sqrt(max(weights[a], 0.0)) + 1e-9 for a in open_}
        norm = sum(share.values())
        exact = {a: left * share[a] / norm for a in open_}
        given = 0
        for a in open_:
            take = min(int(exact[a]), room[a] - alloc[a])
            alloc[a] += take
            given += take
        if given == 0:
            # remainders: the biggest fractional claims first
            for a in sorted(open_, key=lambda a: (-(exact[a] - int(exact[a])), a)):
                if left - given <= 0:
                    break
                if alloc[a] < room[a]:
                    alloc[a] += 1
                    given += 1
        left -= given
    return alloc


def space_out(by_artist: Dict[str, List[dict]], artist_order: Sequence[str],
              rng: random.Random, gap: int = 2) -> List[dict]:
    """one sequence from per-artist piles: the artist with the most left goes
    next, never one used in the last `gap` slots while another can go.
    ties break by the day's rng, so the running order changes too."""
    piles = {a: list(by_artist.get(a) or []) for a in artist_order}
    tiebreak = {a: rng.random() for a in artist_order}
    out: List[dict] = []
    recent: List[str] = []
    while any(piles.values()):
        ready = [a for a in artist_order if piles[a]]
        fresh = [a for a in ready if a not in recent[-gap:]] or ready
        pick = max(fresh, key=lambda a: (len(piles[a]), tiebreak[a]))
        out.append(piles[pick].pop(0))
        recent.append(pick)
    return out


def interleave_owned(
    tracks_by_artist: Dict[str, List[dict]],
    artist_order: Sequence[str],
    size: int,
    rng: random.Random,
    *,
    artist_weights: Optional[Dict[str, float]] = None,
    recent: Optional[Set[str]] = None,
    cap: Optional[int] = None,
) -> List[dict]:
    """size tracks across the cluster's artists, spaced so no two same-artist
    tracks sit together. slots split by artist weight; within an artist the
    pick is a weighted draw on your plays, so favourites usually make it and
    deep cuts sometimes do. anything served in the last few days counts for
    less, which is what makes tomorrow's mix different."""
    recent = recent or set()
    pools: Dict[str, List[dict]] = {}
    for artist in artist_order:
        rows = list(tracks_by_artist.get(artist) or [])
        w = [(1.0 + 1.5 * math.log1p(float(r.get("play_count") or 0)))
             * (REPEAT_PENALTY if r.get("_key") in recent else 1.0) for r in rows]
        pools[artist] = weighted_order(rows, w, rng)
    weights = {a: float((artist_weights or {}).get(a, 1.0)) for a in artist_order}
    room = {a: len(pools[a]) if cap is None else min(cap, len(pools[a])) for a in artist_order}
    quota = allocate(weights, room, size)
    chosen = {a: pools[a][:quota[a]] for a in artist_order}
    return space_out(chosen, artist_order, rng)


def renumber(clusters: List[dict], previous: Sequence[dict]) -> List[int]:
    """a number for each cluster: the number of yesterday's mix it shares the
    most artists with, so a synced Daily Mix 2 keeps its flavour. the rest
    take the free numbers in order. always 1..len(clusters)."""
    k = len(clusters)
    prev = {}
    for m in previous or []:
        try:
            n = int(str(m.get("key", "")).rsplit("_", 1)[-1])
        except ValueError:
            continue
        if 1 <= n <= k:
            prev[n] = {_norm(a) for a in m.get("artists") or []}
    pairs = []
    for i, c in enumerate(clusters):
        mine = {_norm(a) for a in c["artists"]}
        for n, theirs in prev.items():
            if mine and theirs:
                overlap = len(mine & theirs) / len(mine | theirs)
                if overlap >= 0.2:
                    pairs.append((overlap, -i, n, i))
    pairs.sort(reverse=True)
    numbers: Dict[int, int] = {}
    taken: Set[int] = set()
    for _o, _ni, n, i in pairs:
        if i not in numbers and n not in taken:
            numbers[i] = n
            taken.add(n)
    free = [n for n in range(1, k + 1) if n not in taken]
    for i in range(k):
        if i not in numbers:
            numbers[i] = free.pop(0)
    return [numbers[i] for i in range(k)]


def weave(owned: List[dict], discovery: List[dict]) -> List[dict]:
    """discovery spread evenly through the owned run so it flavours rather
    than clumps, never first."""
    if not discovery:
        return list(owned)
    out = list(owned)
    step = max(3, len(owned) // (len(discovery) + 1))
    for j, d in enumerate(discovery):
        out.insert(min(len(out), (j + 1) * step + j), d)
    return out


# ── db-facing generation ──────────────────────────────────────────────────


def _seed_edges(database, seed_names: List[str], profile_id: int,
                spellings: Optional[Dict[str, str]] = None):
    """similars_by_seed + owned-name set, the smear-proof way (source ids).
    spellings, when given, is filled with lowercased name -> library name,
    so later lookups can use the name index instead of scanning artists."""
    from core.discovery.listening_recommendations import group_similars_by_seed

    owned, seed_source_ids, seed_id_to_name = set(), [], {}
    with database._get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT name, spotify_artist_id, itunes_artist_id, deezer_id, "
                    "musicbrainz_id FROM artists WHERE name IS NOT NULL AND name != ''")
        wanted = set(seed_names)
        for row in cur.fetchall():
            nm = row[0]
            lname = _norm(nm)
            owned.add(lname)
            if spellings is not None:
                spellings.setdefault(lname, nm)
            if lname in wanted:
                for sid in (row[1], row[2], row[3], row[4]):
                    if sid:
                        seed_source_ids.append(str(sid))
                        seed_id_to_name[str(sid)] = nm
        edges = []
        if seed_source_ids:
            placeholders = ",".join("?" * len(seed_source_ids))
            cur.execute(
                f"SELECT source_artist_id, similar_artist_name, similarity_rank "
                f"FROM similar_artists WHERE profile_id = ? "
                f"AND source_artist_id IN ({placeholders})",
                [profile_id, *seed_source_ids])
            edges = [dict(zip(("source_artist_id", "similar_artist_name",
                               "similarity_rank"), r, strict=False))
                     for r in cur.fetchall()]
    seeds_shaped = [{"name": n} for n in seed_names]
    similars = group_similars_by_seed(seeds_shaped, edges, seed_id_to_name,
                                      rank_attr="similarity_rank")
    return similars, owned


def _parse_ts(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    text = str(value or "").strip().replace("T", " ")
    if not text:
        return None
    try:
        return datetime.fromisoformat(text[:19])
    except ValueError:
        return None


def _session_plays(database, owner: int, names: Set[str],
                   owned: Set[str]) -> List[Tuple[str, datetime]]:
    """(seed, played_at) for the last year of this pile's plays by these
    artists, in time order. joined credits count for their first owned
    artist, the same way the seeds were folded."""
    since = (datetime.now(timezone.utc) - timedelta(days=SESSION_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    out: List[Tuple[str, datetime]] = []
    resolved: Dict[str, Optional[str]] = {}
    conn = database._get_connection()
    try:
        rows = conn.execute(
            "SELECT artist, played_at FROM listening_history "
            "WHERE +profile_id = ? AND played_at >= ? AND artist IS NOT NULL "
            "ORDER BY played_at",
            (owner, since)).fetchall()
    finally:
        conn.close()
    for artist, played_at in rows:
        raw = _norm(artist)
        if raw not in resolved:
            hit = raw if raw in names else None
            if hit is None and raw not in owned:
                for part in _CREDIT_SPLIT.split(raw):
                    if _norm(part) in names:
                        hit = _norm(part)
                        break
            resolved[raw] = hit
        hit = resolved[raw]
        at = _parse_ts(played_at) if hit else None
        if hit and at:
            out.append((hit, at))
    return out


def _owned_tracks_for(database, artist_names: Sequence[str],
                      owner: Optional[int] = None) -> Dict[str, List[dict]]:
    """Playable library tracks per artist (lowercased key), mix-track shaped,
    one copy of each song. play_count is this pile's own plays from
    listening_history when it has any for the track, the media server's
    count otherwise."""
    from core.personalized.for_you import _artists_named, _chunks

    out: Dict[str, List[dict]] = {}
    if not artist_names:
        return out
    conn = database._get_connection()
    try:
        artist_rows = _artists_named(conn, list(artist_names))
        ids = [r[0] for r in artist_rows]
        rows = []
        for chunk in _chunks(ids, 900):
            rows += conn.execute(
                f"""
                SELECT t.id, t.title, t.duration, t.play_count,
                       ar.name AS artist, al.title AS album,
                       COALESCE(al.thumb_url, ar.thumb_url) AS cover
                FROM tracks t
                JOIN artists ar ON ar.id = t.artist_id
                LEFT JOIN albums al ON al.id = t.album_id
                WHERE t.artist_id IN ({','.join('?' * len(chunk))})
                  AND t.file_path IS NOT NULL AND t.file_path != ''
                """, list(chunk)).fetchall()
        plays: Dict[str, int] = {}
        if owner is not None and rows:
            for chunk in _chunks([r[0] for r in rows], 900):
                for tid, n in conn.execute(
                        f"SELECT db_track_id, COUNT(*) FROM listening_history "
                        f"WHERE +profile_id = ? AND db_track_id IN ({','.join('?' * len(chunk))}) "
                        f"GROUP BY db_track_id", [owner, *chunk]).fetchall():
                    plays[str(tid)] = n
    finally:
        conn.close()

    copies: Dict[str, List[dict]] = {}
    for tid, title, duration, server_plays, artist, album, cover in rows:
        if not title or not artist or is_filler(title, duration):
            continue
        # this pile's plays when it has played any of these, so a kid's
        # profile isn't steered by the admin's server counts
        count = plays.get(str(tid), 0) if plays else int(server_plays or 0)
        copies.setdefault(song_key(artist, title), []).append({
            "name": title,
            "artists": [{"name": artist}],
            # the art resolves only for tracks that make a mix (see _clean):
            # doing it here registered every owned track's thumb
            "album": {"name": album or "", "images": []},
            "duration_ms": int(duration or 0),  # tracks.duration is already milliseconds
            "play_count": count,
            "owned": True,
            "_cover": cover or None,
        })
    # one copy per song (the album and the deluxe, two servers, the remix):
    # the most played one with art, carrying the plays of every copy
    for key, group in copies.items():
        track = max(group, key=lambda t: (t["play_count"], bool(t["_cover"])))
        track = dict(track, play_count=sum(t["play_count"] for t in group), _key=key)
        out.setdefault(_norm(_artist_of(track)), []).append(track)
    return out


def _discovery_tracks_for(database, artist_names: Sequence[str], profile_id: int,
                          per_artist: int = 4,
                          owned: Optional[Set[str]] = None) -> Dict[str, List[dict]]:
    """Unowned flavor from the discovery pool, per (lowercased) artist, most
    popular first, one version of each song. rows carry the pool's artist
    genres as _genres so a same-named stranger can be told apart.

    a row counts for the artist it was asked for only when that artist leads
    the track: the pool files features under the featured artist too, which
    put an owned artist's song in a mix as 'discovery' and let a 'silo'
    nobody asked for in."""
    out: Dict[str, List[dict]] = {}
    if not artist_names:
        return out
    owned = owned or set()
    with database._get_connection() as conn:
        cur = conn.cursor()
        placeholders = ",".join("?" * len(artist_names))
        cur.execute(
            f"""
            SELECT track_name, artist_name, album_name, album_cover_url,
                   duration_ms, popularity, artist_genres, track_data_json
            FROM discovery_pool
            WHERE profile_id = ? AND LOWER(artist_name) IN ({placeholders})
            ORDER BY popularity DESC
            """,
            [profile_id, *[_norm(a) for a in artist_names]])
        seen = set()
        for row in cur.fetchall():
            r = dict(row)
            artist = _norm(r["artist_name"])
            if len(out.get(artist, [])) >= per_artist:
                continue
            if is_filler(r["track_name"], r["duration_ms"]):
                continue
            track = None
            if r.get("track_data_json"):
                try:
                    track = json.loads(r["track_data_json"])
                except Exception:  # noqa: BLE001 - blob optional
                    track = None
            if not isinstance(track, dict):
                track = {
                    "name": r["track_name"],
                    "artists": [{"name": r["artist_name"]}],
                    "album": {"name": r["album_name"] or "",
                              "images": ([{"url": r["album_cover_url"]}]
                                         if r["album_cover_url"] else [])},
                    "duration_ms": r["duration_ms"] or 0,
                }
            lead = _artist_of(track) or r["artist_name"]
            if _alnum(lead) != _alnum(r["artist_name"]) or _norm(lead) in owned:
                continue
            key = song_key(lead, track.get("name") or r["track_name"])
            if key in seen:
                continue
            seen.add(key)
            track["owned"] = False
            track["_key"] = key
            track["_popularity"] = int(r.get("popularity") or 0)
            try:
                genres = json.loads(r["artist_genres"]) if r.get("artist_genres") else []
            except (TypeError, ValueError):
                genres = []
            track["_genres"] = [str(g) for g in genres] if isinstance(genres, list) else []
            out.setdefault(artist, []).append(track)
    return out


def _families(genres: Iterable[str]) -> Set[str]:
    from core.personalized_playlists import PersonalizedPlaylistsService
    fams = {PersonalizedPlaylistsService.get_parent_genre(str(g)) for g in genres if g}
    fams.discard("Other")
    return fams


def cluster_families(members: Sequence[str], genres_by_name: Dict[str, List[str]]) -> Set[str]:
    """the genre families a mix is about: ones a real share of its members
    carry, so one member tagged with fourteen genres can't open every door"""
    counts: Dict[str, int] = {}
    tagged = 0
    for m in members:
        fams = _families(genres_by_name.get(m) or [])
        if fams:
            tagged += 1
        for f in fams:
            counts[f] = counts.get(f, 0) + 1
    need = max(1, math.ceil(0.25 * tagged))
    return {f for f, n in counts.items() if n >= need}


def pick_discovery(candidates: Dict[str, List[dict]], ranked_artists: Sequence[str],
                   cluster_families: Set[str], size: int, rng: random.Random,
                   recent: Set[str], taken: Set[str]) -> List[dict]:
    """size discovery tracks, spread across the best-ranked similar artists:
    one each first, a second only when there aren't enough artists. an
    artist whose pool genres share no family with the mix is a same-named
    stranger (an outlaw-country 'Silo' in an electro mix) and is skipped."""
    pools: List[List[dict]] = []
    for artist in ranked_artists:
        rows = [t for t in candidates.get(artist) or [] if t["_key"] not in taken]
        if not rows:
            continue
        fams = _families(rows[0].get("_genres") or [])
        if fams and cluster_families and not fams & cluster_families:
            continue
        w = [(1.0 + t.get("_popularity", 0) / 25.0)
             * (0.15 if t["_key"] in recent else 1.0) for t in rows]
        pools.append(weighted_order(rows, w, rng))
    out: List[dict] = []
    for round_ in range(2):
        for pool in pools:
            if len(out) >= size:
                return out
            if len(pool) > round_:
                out.append(pool[round_])
    return out


def _taste(database, profile_id: int):
    from core.discovery.feedback import Taste
    return Taste.load(database, profile_id)


def _blocked_artists(database, profile_id: int):
    from core.discovery.blocked import BlockedArtists
    return BlockedArtists.load(database, profile_id)


def _library_seeds(database, limit: int) -> List[dict]:
    """no listening history yet: the artists the media server says get played,
    then the ones you own most of."""
    try:
        with database._get_connection() as conn:
            rows = conn.execute(
                "SELECT ar.name, SUM(COALESCE(t.play_count, 0)) AS plays, COUNT(*) AS n "
                "FROM tracks t JOIN artists ar ON ar.id = t.artist_id "
                "WHERE t.file_path IS NOT NULL AND t.file_path != '' "
                "AND ar.name IS NOT NULL AND ar.name != '' "
                "GROUP BY ar.id ORDER BY plays DESC, n DESC LIMIT ?", (limit,)).fetchall()
    except Exception as e:  # noqa: BLE001 - no fallback is the old behaviour
        logger.debug(f"library seed fallback failed: {e}")
        return []
    return [{"name": r[0], "weight": float(r[1] or 0) + 0.1 * float(r[2] or 0)}
            for r in rows if r[2] and r[2] >= 3]


def _recent_keys(previous: Optional[dict], today: date) -> Set[str]:
    """track keys served on the last HISTORY_DAYS days before today"""
    out: Set[str] = set()
    for day, keys in ((previous or {}).get("served") or {}).items():
        try:
            d = date.fromisoformat(day)
        except ValueError:
            continue
        if 0 < (today - d).days <= HISTORY_DAYS:
            out.update(k for k in keys or [] if isinstance(k, str))
    return out


def _clean(track: dict) -> dict:
    """the track as it's stored and served: no picking fields, and library
    art made browser-safe. library thumbs are media-server-relative
    (/library/metadata/...) and render blank without the conversion"""
    out = {k: v for k, v in track.items() if not k.startswith("_") and k != "play_count"}
    if track.get("_cover"):
        from core.metadata import normalize_image_url
        url = normalize_image_url(track["_cover"])
        if url:
            out["album"] = dict(out.get("album") or {}, images=[{"url": url}])
    return out


def generate_daily_mixes(database, profile_id: int = 1, *,
                         max_mixes: int = MAX_MIXES,
                         mix_size: int = MIX_SIZE,
                         discovery_per_mix: int = DISCOVERY_PER_MIX,
                         neighbours_per_mix: int = NEIGHBOURS_PER_MIX,
                         today: Optional[date] = None,
                         previous: Optional[dict] = None) -> Dict[str, Any]:
    from core.discovery.listening_recommendations import build_recency_weighted_seeds
    from core.listening_scope import listening_owner

    today = today or date.today()
    owner = listening_owner(database, profile_id)
    top = database.get_top_artists('all', 200, profile_id=profile_id) or []
    recent_plays = database.get_top_artists('30d', 200, profile_id=profile_id) or []
    seeds = build_recency_weighted_seeds(
        top, {a['name']: a.get('play_count', 0) for a in recent_plays})
    if not seeds:
        seeds = _library_seeds(database, MAX_SEEDS)
    # a blocked artist founds no mix and names none (the subtitle is text the
    # response filter can't reach); cut before the seed cap so it costs no slot
    blocked = _blocked_artists(database, profile_id)
    seeds = [s for s in seeds if not blocked.blocks_name(s.get('name'))]
    # more / less like this reweights the seeds before the cap
    taste = _taste(database, profile_id)
    seeds = taste.adjust_seeds(seeds)

    # edges for every name a credit could fold into, so a folded seed has its
    # similar artists too
    wanted = []
    for s in seeds:
        for part in [s['name'], *_CREDIT_SPLIT.split(str(s['name']))]:
            if _norm(part) and _norm(part) not in wanted:
                wanted.append(_norm(part))
    spelling: Dict[str, str] = {}
    similars, owned = _seed_edges(database, wanted, profile_id, spelling)
    seeds = fold_credit_seeds(seeds, owned)
    # mixes are built from OWNED artists - a heard-but-unowned artist has no
    # tracks to play and belongs to the discovery side instead
    owned_seeds = sorted((s for s in seeds if _norm(s['name']) in owned
                          and not blocked.blocks_name(s['name'])),
                         key=lambda s: -s['weight'])[:MAX_SEEDS]
    seed_keys = {_norm(s['name']) for s in owned_seeds}
    weight_of = {_norm(s['name']): s['weight'] for s in owned_seeds}
    genres_by_name = {}
    try:
        genres_by_name = database.get_artist_genres_by_name(
            [s['name'] for s in owned_seeds]) or {}
    except Exception as e:
        logger.debug(f"genre lookup failed: {e}")
    cooc = {}
    try:
        cooc = session_cooccurrence(_session_plays(database, owner, seed_keys, owned))
    except Exception as e:  # noqa: BLE001 - the other signals still cluster
        logger.debug(f"session co-listening failed: {e}")

    # a few spare candidates, best first: a mix that comes out too thin hands
    # its slot to the next one
    clusters = cluster_seeds(owned_seeds, similars, genres_by_name,
                             cooccurrence=cooc, max_clusters=max_mixes + 4)
    recent = _recent_keys(previous, today)
    members_all = {_norm(a) for c in clusters[:max_mixes] for a in c['artists']}
    used_neighbours: Set[str] = set()
    used_discovery: Set[str] = set()
    built = []

    def neighbourhood(artist_keys: List[str]) -> Dict[str, float]:
        """who sits next to a mix: every member's similar artists, scored by
        how many members point at them, how close, and how much you play the
        member"""
        near: Dict[str, float] = {}
        for a in artist_keys:
            pull = math.log1p(weight_of.get(a, 1.0))
            for s in similars.get(a) or []:
                nm = _norm(s.get('name'))
                if nm and nm not in members_all and not blocked.blocks_name(nm):
                    near[nm] = near.get(nm, 0.0) + pull * float(s.get('score', 1.0) or 0.0)
        for nm in list(near):
            near[nm] *= taste.artist_factor(nm)
        return near

    scored = [neighbourhood([_norm(a) for a in c['artists']]) for c in clusters]
    nears = [sorted(near, key=lambda n: (-near[n], n)) for near in scored]
    # discovery: unowned similar artists, an artist you asked for less of just
    # isn't in it, and one only a single far member points at isn't either
    # (flamenco in the synthwave mix). one pool read for every mix, it's a
    # scan of the pool
    flavour_names = []
    for near, ranked in zip(scored, nears, strict=True):
        names = [n for n in ranked if n not in owned and taste.artist_factor(n) >= 1.0]
        floor = DISCOVERY_FLOOR * near[names[0]] if names else 0.0
        flavour_names.append([n for n in names if near[n] >= floor][:30])
    pool = _discovery_tracks_for(
        database, sorted({n for names in flavour_names for n in names}), profile_id,
        owned=owned)
    pool = {a: [t for t in rows if in_season(t, today) and not blocked.blocks_work(t)]
            for a, rows in pool.items()}

    for i, cluster in enumerate(clusters, start=1):
        if len(built) >= max_mixes:
            break
        rng = random.Random(f"{today.isoformat()}:{profile_id}:{i}")
        artist_keys = [_norm(a) for a in cluster['artists']]
        ranked_near = nears[i - 1]

        # owned neighbours: library artists next to the mix that lead no mix
        # of their own (unplayed, or a seed too light to found one), the
        # "familiar but fresh" layer
        neighbour_names = [n for n in ranked_near if n in owned
                           and n not in used_neighbours and taste.artist_factor(n) >= 1.0][:8]
        library = _owned_tracks_for(
            database, cluster['artists'] + [spelling.get(n, n) for n in neighbour_names], owner)
        library = {a: [t for t in rows if in_season(t, today) and not blocked.blocks_work(t)]
                   for a, rows in library.items()}
        neighbour_names = [n for n in neighbour_names if library.get(n)]
        weights = {a: weight_of.get(a, 1.0) for a in artist_keys}

        # picked in priority order, so a short mix can take a few more off the top
        flavour = pick_discovery(pool, flavour_names[i - 1],
                                 cluster_families(artist_keys, genres_by_name),
                                 2 * discovery_per_mix, rng, recent, used_discovery)

        # the mix fills in layers: your artists, capped so nobody runs away
        # with it, then their owned neighbours, then a little more discovery,
        # then more of the neighbours, and only then your artists past the cap
        discovery = flavour[:discovery_per_mix]
        core = interleave_owned(library, artist_keys, mix_size - len(discovery) - neighbours_per_mix,
                                rng, artist_weights=weights, recent=recent, cap=MAX_PER_ARTIST)
        short = mix_size - len(core) - len(discovery)
        neighbour_tracks = interleave_owned(library, neighbour_names, short, rng,
                                            recent=recent, cap=3)
        short -= len(neighbour_tracks)
        if short > 0:
            discovery = flavour[:len(discovery) + short]
            short = mix_size - len(core) - len(discovery) - len(neighbour_tracks)
        if short > 0 and neighbour_names:
            neighbour_tracks = interleave_owned(library, neighbour_names,
                                                len(neighbour_tracks) + short, rng,
                                                recent=recent, cap=MAX_PER_ARTIST)
            short = mix_size - len(core) - len(discovery) - len(neighbour_tracks)
        if short > 0:
            core = interleave_owned(library, artist_keys, len(core) + short, rng,
                                    artist_weights=weights, recent=recent)
        artists_in = {_norm(_artist_of(t)) for t in core + neighbour_tracks + discovery}
        if (len(core) < MIN_OWNED_TRACKS
                or len(core) + len(neighbour_tracks) + len(discovery) < min(MIN_MIX_TRACKS, mix_size)
                or len(artists_in) < 2):
            continue
        used_neighbours.update(n for n in neighbour_names
                               if any(_norm(_artist_of(t)) == n for t in neighbour_tracks))
        used_discovery.update(t['_key'] for t in discovery)
        # neighbours ride in the owned run, spaced like everyone else
        piles: Dict[str, List[dict]] = {}
        for t in core + neighbour_tracks:
            piles.setdefault(_norm(_artist_of(t)), []).append(t)
        owned_run = space_out(piles, artist_keys + neighbour_names, rng)
        tracks = weave(owned_run, discovery)
        # neighbour keys are lowercase, the library's own spelling reads better
        shown = [_artist_of(piles[n][0]) for n in neighbour_names if piles.get(n)]
        built.append((cluster, tracks, len(owned_run), shown))

    numbers = renumber([c for c, *_ in built], (previous or {}).get("mixes") or [])
    mixes = []
    served: List[str] = []
    for (cluster, tracks, owned_count, neighbour_names), n in sorted(
            zip(built, numbers, strict=True), key=lambda x: x[1]):
        served.extend(t['_key'] for t in tracks if t.get('_key'))
        total = len(tracks)
        mixes.append({
            "key": f"daily_mix_{n}",
            "name": f"Daily Mix {n}",
            "subtitle": _subtitle(cluster['artists'], neighbour_names),
            "genre": cluster.get('genre') or "",
            "artists": cluster['artists'],
            "tracks": [_clean(t) for t in tracks],
            "owned_count": owned_count,
            "total": total,
            # made from artists you play; confidence is the share of it that
            # is already yours rather than discovery flavor, and the source
            # mix says the same thing as actual owned/discovery shares
            "explanation": explanation(
                "listened", cluster['artists'],
                owned_count / total if total else None,
                source_mix=({'owned': owned_count / total,
                             'discovery': (total - owned_count) / total}
                            if total else None)),
        })

    history = {d: k for d, k in ((previous or {}).get("served") or {}).items()
               if isinstance(k, list) and d != today.isoformat()
               and _days_ago(d, today) <= HISTORY_DAYS}
    history[today.isoformat()] = served
    return {
        "mixes": mixes,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        # the local day this was built for: a new day is a new mix
        "day": today.isoformat(),
        "profile_id": profile_id,
        # the blocks and the feedback this was built under: either changing
        # rebuilds it
        "blocked": blocked.fingerprint(),
        "taste": taste.fingerprint(),
        # what each recent day served, so tomorrow can play something else
        "served": history,
        # payload version: bump to invalidate stored payloads whose SHAPE or
        # content rules changed (v3 = library durations stay in milliseconds,
        # v4 = each mix carries its explanation, v5 = co-listening clusters,
        # per-day builds and the served history)
        "v": PAYLOAD_VERSION,
    }


def _subtitle(members: Sequence[str], neighbours: Sequence[str]) -> str:
    """three names off the mix, its own artists first. a one-artist mix
    names who it's with"""
    names = list(members[:3])
    if len(names) < 3:
        shown = {_norm(n) for n in names}
        for n in neighbours:
            if len(names) >= 3:
                break
            if _norm(n) not in shown:
                names.append(n)
    more = len(members) + len(neighbours) > len(names)
    return ", ".join(names) + (" and more" if more else "")


def _days_ago(day: str, today: date) -> int:
    try:
        return (today - date.fromisoformat(day)).days
    except ValueError:
        return 10 ** 6


def is_fresh(stored: Any, today: date, blocked_fp: str, taste_fp: str,
             ttl_hours: float = TTL_HOURS, now: Optional[datetime] = None) -> bool:
    """a stored payload serves while it is today's, current, and built under
    the blocks and feedback in force now."""
    if not (isinstance(stored, dict) and stored.get("mixes")):
        return False
    if stored.get("v") != PAYLOAD_VERSION or stored.get("day") != today.isoformat():
        return False
    if stored.get("blocked") != blocked_fp or stored.get("taste") != taste_fp:
        return False
    try:
        age = (now or datetime.now(timezone.utc)) - datetime.fromisoformat(
            stored.get("generated_at", ""))
    except (TypeError, ValueError):
        return False
    return age.total_seconds() < ttl_hours * 3600


def get_or_build_daily_mixes(database, profile_id: int = 1, *,
                             ttl_hours: float = TTL_HOURS,
                             force: bool = False,
                             today: Optional[date] = None) -> Dict[str, Any]:
    """The endpoint's entry: serve today's stored payload, build a new one
    when the local day turns over (the discover warmer asks every 25
    minutes, so that happens shortly after midnight without anyone visiting).
    The previous payload feeds the new build: its numbers and what it
    served."""
    today = today or date.today()
    stored = None
    try:
        stored = database.get_curated_playlist(CURATED_KEY, profile_id)
    except Exception as e:
        logger.debug(f"stored daily mixes unreadable: {e}")
    if not force and is_fresh(stored, today,
                              _blocked_artists(database, profile_id).fingerprint(),
                              _taste(database, profile_id).fingerprint(), ttl_hours):
        return stored
    previous = stored if isinstance(stored, dict) else None
    payload = generate_daily_mixes(database, profile_id, today=today, previous=previous)
    try:
        database.save_curated_playlist(CURATED_KEY, payload, profile_id)
    except Exception as e:
        logger.warning(f"daily mixes save failed: {e}")
    return payload
