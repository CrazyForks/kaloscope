from app.core.media.filters.base import _FILTERS, EventFilter
from app.models.media import LibType


class TVShowEventFilter(EventFilter):
    """The event filter for TV show library type."""

    def accept(self) -> list[str]:
        """The mime types accepted by the TV show library type.

        Returns:
            The list of mime types.
        """
        return ["video/*"]

    def hierarchies(self) -> list[int]:
        """The hierarchy levels for the TV show library type.

        Shows
        ├── Series Name A (2010)
        │   ├── Season 00
        │   │   ├── Some Special.mkv
        │   │   ├── Series Name A S00E01.mkv
        │   │   └── Series Name A S00E02.mkv
        │   ├── Season 01
        │   │   ├── Series Name A S01E01-E02.mkv
        │   │   ├── Series Name A S01E03.mkv
        │   │   └── Series Name A S01E04.mkv
        │   └── Season 02
        │       ├── Series Name A S02E01.mkv
        │       ├── Series Name A S02E02.mkv
        │       ├── Series Name A S02E03 Part 1.mkv
        │       └── Series Name A S02E03 Part 2.mkv
        └── Series Name B (2018)
            ├── Season 01
            |   ├── Series Name B S01E01.mkv
            |   └── Series Name B S01E02.mkv
            └── Season 02
                ├── Series Name B S02E01-E02.mkv
                └── Series Name B S02E03.mkv

        Returns:
            The list of hierarchy levels.
        """
        return [1, 2, 3]


_FILTERS[LibType.TV_SHOW] = TVShowEventFilter()
