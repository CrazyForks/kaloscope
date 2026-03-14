from pathlib import Path

from lxml import etree

from app.core.media.handlers.base import (
    _HANDLERS,
    MediaHandler,
    MediaMeta,
    MetaKeywords,
)
from app.models.media import LibType, MediaLib


class TVShowMediaHandler(MediaHandler):
    """The media item handler for TV show library type."""

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
        │   ├── Series Name A S01E01.mkv
        │   ├── Series Name A S01E02.mkv
        │   └── Season 02
        │       ├── Series Name A S02E01.mkv
        │       └── Series Name A S02E02.mkv
        └── Series Name B (2018)
            ├── Season 01
            │   ├── Series Name B S01E01.mkv
            │   └── Series Name B S01E02.mkv
            └── Season 02
                ├── Series Name B S02E01.mkv
                └── Series Name B S02E03.mkv

        Returns:
            The list of hierarchy levels.
        """
        return [2, 3]

    def extract_meta(self, data: etree._ElementTree) -> MediaMeta:
        """Extract the metadata for a TV show.

        Args:
            data: The XML data to extract from.

        Returns:
            The extracted metadata.
        """
        return MediaMeta()

    async def gen_items(self, lib: MediaLib, path: Path) -> list[MetaKeywords]:
        """Generate the media items for a TV show.

        Args:
            lib: The media library instance.
            path: The path to generate from.

        Returns:
            The list of metadata keywords for the media items.
        """
        return []


_HANDLERS[LibType.TV_SHOW] = TVShowMediaHandler()
