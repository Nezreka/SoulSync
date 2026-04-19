"""Genre whitelist filter for enrichment workers.

When strict mode is enabled, only genres on the whitelist pass through
during enrichment. When disabled (default), all genres pass unchanged.
"""

from utils.logging_config import get_logger

logger = get_logger("genre_filter")

# ~180 curated genres covering all major categories.
# This is the default whitelist — users can add/remove via Settings.
DEFAULT_GENRES = [
    # Rock
    "Rock", "Alternative Rock", "Indie Rock", "Classic Rock", "Punk Rock", "Post-Punk",
    "Psychedelic Rock", "Progressive Rock", "Garage Rock", "Grunge", "Shoegaze", "Surf Rock",
    "Stoner Rock", "Southern Rock", "Hard Rock", "Soft Rock", "Art Rock", "Glam Rock",
    "Noise Rock", "Math Rock", "Post-Rock", "Folk Rock", "Heartland Rock", "Brit Rock",
    "Space Rock", "Krautrock",
    # Punk
    "Punk", "Hardcore Punk", "Pop Punk", "Ska Punk", "Post-Hardcore",
    # Emo
    "Emo", "Midwest Emo", "Screamo",
    # Metal
    "Metal", "Heavy Metal", "Death Metal", "Black Metal", "Thrash Metal", "Doom Metal",
    "Power Metal", "Speed Metal", "Progressive Metal", "Symphonic Metal", "Metalcore",
    "Deathcore", "Nu Metal", "Industrial Metal", "Gothic Metal", "Sludge Metal",
    "Folk Metal", "Djent", "Groove Metal", "Post-Metal",
    # Pop
    "Pop", "Synth Pop", "Electropop", "Indie Pop", "Dream Pop", "Chamber Pop", "Art Pop",
    "Dance Pop", "Power Pop", "Baroque Pop", "Bedroom Pop", "K-Pop", "J-Pop", "Teen Pop",
    "Bubblegum Pop",
    # Hip Hop / Rap
    "Hip Hop", "Rap", "Trap", "Boom Bap", "Gangsta Rap", "Conscious Hip Hop",
    "Southern Hip Hop", "West Coast Hip Hop", "East Coast Hip Hop", "Dirty South", "Crunk",
    "Grime", "Drill", "Lo-Fi Hip Hop", "Abstract Hip Hop",
    # Electronic / Dance
    "Electronic", "EDM", "House", "Deep House", "Tech House", "Progressive House",
    "Techno", "Trance", "Drum and Bass", "Dubstep", "Ambient", "IDM", "Downtempo",
    "Trip Hop", "Breakbeat", "Jungle", "Garage", "UK Garage", "Future Bass", "Hardstyle",
    "Electro", "Electronica", "Chillwave", "Synthwave", "Vaporwave", "Industrial", "EBM",
    "Glitch", "Footwork", "Chillout", "Lo-Fi", "New Age",
    # R&B / Soul / Funk
    "R&B", "Soul", "Neo Soul", "Funk", "Disco", "Motown", "Gospel", "Quiet Storm",
    "Contemporary R&B", "New Jack Swing",
    # Jazz
    "Jazz", "Bebop", "Cool Jazz", "Free Jazz", "Fusion", "Smooth Jazz", "Acid Jazz",
    "Nu Jazz", "Swing", "Big Band", "Latin Jazz", "Vocal Jazz",
    # Blues
    "Blues", "Delta Blues", "Chicago Blues", "Electric Blues", "Blues Rock", "Country Blues",
    # Country
    "Country", "Alt-Country", "Americana", "Bluegrass", "Country Rock", "Outlaw Country",
    "Country Pop", "Honky Tonk", "Western Swing", "Nashville Sound",
    # Folk / Singer-Songwriter
    "Folk", "Indie Folk", "Contemporary Folk", "Singer-Songwriter", "Acoustic",
    "Freak Folk", "Folk Punk", "Neofolk",
    # Classical
    "Classical", "Opera", "Baroque", "Romantic", "Contemporary Classical", "Minimalism",
    "Orchestral", "Chamber Music", "Choral", "Soundtrack", "Film Score", "Musical Theatre",
    # Latin
    "Latin", "Reggaeton", "Salsa", "Bachata", "Cumbia", "Merengue", "Latin Pop",
    "Latin Rock", "Bossa Nova", "Samba", "MPB", "Tango", "Banda", "Norteño", "Corrido",
    "Tropical",
    # Reggae / Caribbean
    "Reggae", "Dancehall", "Dub", "Ska", "Rocksteady", "Calypso", "Soca",
    # World / International
    "World", "Afrobeat", "Afropop", "Afrobeats", "Bhangra", "Celtic", "Flamenco",
    "Fado", "Klezmer", "Polka", "Zydeco", "Highlife",
    # Alternative / Indie (broad umbrella genres Spotify uses heavily)
    "Alternative", "Indie", "Alternative Metal", "Alternative R&B",
    # Additional Rock
    "New Wave", "Darkwave", "Post-Grunge", "Slowcore", "Sadcore", "Post-Punk Revival",
    # Additional Metal
    "Grindcore", "Crust Punk", "Crossover Thrash", "Trap Metal",
    # Additional Hip Hop
    "Emo Rap", "Cloud Rap", "Phonk", "Horrorcore", "Nerdcore",
    # Additional Electronic
    "Dark Ambient", "Drone", "Witch House", "Hyperpop", "Future Funk",
    "Outrun", "Retrowave", "Chiptune", "Dance",
    # Additional Pop
    "German Pop", "French Pop", "Turkish Pop",
    # Additional Latin
    "Trap Latino", "Urbano Latino", "Tropicalia", "Mambo", "Bossa Nova",
    # Additional Reggae
    "Roots Reggae", "Lovers Rock",
    # Additional Jazz
    "Hard Bop", "Modal Jazz", "Gypsy Jazz",
    # Additional World
    "Qawwali", "Carnatic", "Hindustani",
    # Media
    "Video Game Music", "Anime", "Soundtrack", "Film Score",
    # Other
    "Experimental", "Avant-Garde", "Noise", "Spoken Word", "Comedy", "Instrumental",
    "A Cappella", "Worship", "Christian", "Christmas", "Holiday", "Easy Listening",
    "Lounge", "Psychedelic", "Progressive",
]

# Normalized lookup set — built once, used for O(1) matching
_DEFAULT_LOOKUP = {g.lower() for g in DEFAULT_GENRES}


def _normalize_for_match(genre: str) -> str:
    """Normalize a genre string for whitelist comparison.

    Handles common variations: 'R&B' vs 'RnB', 'Hip-Hop' vs 'Hip Hop'.
    """
    g = genre.lower().strip()
    g = g.replace('-', ' ').replace('&', ' and ')
    # Collapse multiple spaces
    return ' '.join(g.split())


def _build_lookup(genre_list):
    """Build a normalized lookup set from a genre list."""
    return {_normalize_for_match(g) for g in genre_list}


def filter_genres(genres, config_manager=None):
    """Filter a list of genres against the whitelist.

    Args:
        genres: List of genre strings to filter
        config_manager: ConfigManager instance (None = no filtering)

    Returns:
        Filtered list of genres. When strict mode is off, returns genres unchanged.
    """
    if not genres or not isinstance(genres, list):
        return genres or []

    # Check if strict mode is enabled
    if config_manager is None:
        return genres

    enabled = config_manager.get('genre_whitelist.enabled', False)
    if not enabled:
        return genres

    # Get user's whitelist (falls back to defaults if not configured)
    user_genres = config_manager.get('genre_whitelist.genres', None)
    if user_genres and isinstance(user_genres, list):
        lookup = _build_lookup(user_genres)
    else:
        lookup = _DEFAULT_LOOKUP

    # Filter — keep genres that match the whitelist (case-insensitive with normalization)
    filtered = [g for g in genres if _normalize_for_match(g) in lookup]
    return filtered
