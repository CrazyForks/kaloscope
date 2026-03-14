from pathlib import Path

from lxml import etree

from app.core.media.handlers.base import (
    _HANDLERS,
    MediaHandler,
    MediaMeta,
    MetaKeywords,
)
from app.models.media import LibType, MediaLib, NFOType
from app.services.media import MediaItemService
from app.utils.extractor import extract_title, extract_year
from app.utils.xml import get_decimal, get_integer, get_text


class MovieMediaHandler(MediaHandler):
    """The media item handler for movie library type."""

    def accept(self) -> list[str]:
        """The mime types accepted by the movie library type.

        Returns:
            The list of mime types.
        """
        return ["video/*"]

    def hierarchies(self) -> list[int]:
        """The hierarchy levels for the movie library type.

        Movies
        ├── Film (1990).mp4
        ├── Film (1994).mp4
        ├── Film (2008)
        │   └── Film.mkv
        └── Film (2010)
            ├── Film-cd1.avi
            └── Film-cd2.avi

        Returns:
            The list of hierarchy levels.
        """
        return [1, 2]

    def extract_meta(self, data: etree._ElementTree) -> MediaMeta:
        """Extract the metadata for a movie.

        Args:
            data: The XML data to extract from.

        Returns:
            The extracted metadata.
        """
        meta = MediaMeta()
        movie = data.getroot()
        meta.title = get_text(movie, "title")
        meta.year = get_integer(movie, "year")
        art = movie.find("art")
        meta.cover = get_text(art, "poster")
        meta.backdrop = get_text(art, "fanart")
        meta.rating = get_decimal(movie, "rating")
        return meta

    async def gen_items(self, lib: MediaLib, path: Path) -> list[MetaKeywords]:
        """Generate the media items for a movie.

        Args:
            lib: The media library instance.
            path: The path to generate from.

        Returns:
            The list of metadata keywords for the media items.
        """
        result = []
        dir = Path(lib.dir)
        parts = path.relative_to(dir).parts
        if len(parts) == 1:
            m = MetaKeywords(path)
            m.nfo_path = dir / f"{m.item_name}.nfo"
            m.nfo_type = NFOType.MOVIE
            m.language = lib.language
            m.title = extract_title(m.item_name)
            m.year = extract_year(m.item_name)
            result.append(m)
            await MediaItemService.create(lib.id, None, m)
        elif len(parts) == 2:
            m1 = MetaKeywords(dir / parts[0])
            m1.nfo_path = dir / parts[0] / f"{m1.item_name}.nfo"
            m1.nfo_type = NFOType.MOVIE
            m1.language = lib.language
            m1.title = extract_title(m1.item_name)
            m1.year = extract_year(m1.item_name)
            result.append(m1)
            parent_item = await MediaItemService.create(lib.id, None, m1)

            m2 = MetaKeywords(path)
            m2.language = lib.language
            m2.title = extract_title(m2.item_name)
            m2.year = extract_year(m2.item_name)
            result.append(m2)
            await MediaItemService.create(lib.id, parent_item.id, m2)
        return result


_HANDLERS[LibType.MOVIE] = MovieMediaHandler()
