"""Per-kind generators for the personalized-playlists subsystem.

Each module in this subpackage:
1. Defines a generator function ``generate(deps, variant, config)``
   that returns a ``List[Track]``.
2. Calls ``get_registry().register(spec)`` at import time so the
   manager auto-discovers it.

The legacy ``core.personalized_playlists.PersonalizedPlaylistsService``
keeps its existing implementations — the wrappers in this package
just adapt the call surface (`PlaylistConfig` → method kwargs) and
coerce results into ``Track`` instances.

To register every generator, import this package — `from
core.personalized import generators` — typically done once at
application startup."""

# Importing each module triggers its registration side-effect.
from core.personalized.generators import hidden_gems  # noqa: F401
from core.personalized.generators import discovery_shuffle  # noqa: F401
from core.personalized.generators import popular_picks  # noqa: F401
from core.personalized.generators import time_machine  # noqa: F401
from core.personalized.generators import genre_playlist  # noqa: F401
from core.personalized.generators import daily_mix  # noqa: F401
from core.personalized.generators import fresh_tape  # noqa: F401
from core.personalized.generators import archives  # noqa: F401
from core.personalized.generators import seasonal_mix  # noqa: F401
