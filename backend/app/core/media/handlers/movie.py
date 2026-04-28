from pathlib import Path

from lxml import etree

from app.core.media.handlers.base import (
    _HANDLERS,
    Actor,
    MediaHandler,
    MediaMeta,
    MetaKeywords,
)
from app.models.media import LibType, MediaLib, NFOType
from app.services.media import MediaItemService
from app.utils.extractor import extract_title, extract_year
from app.utils.xml import get_all_text, get_decimal, get_integer, get_text


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
        root = data.getroot()
        meta.uniqueid = get_text(root, "uniqueid")
        meta.title = get_text(root, "title")
        meta.originaltitle = get_text(root, "originaltitle")
        meta.tagline = get_text(root, "tagline")
        meta.plot = get_text(root, "plot")
        meta.rating = get_decimal(root, "rating")
        meta.year = get_integer(root, "year")
        meta.premiered = get_text(root, "premiered")
        meta.country = get_text(root, "country")
        meta.mpaa = get_text(root, "mpaa")
        # multiple
        meta.tags = get_all_text(root, "tag")
        meta.genres = get_all_text(root, "genre")
        meta.studios = get_all_text(root, "studio")
        meta.directors = get_all_text(root, "director")
        meta.writers = get_all_text(root, "writer")
        meta.credits = get_all_text(root, "credits")
        # actors
        actors: list[Actor] = []
        for el in root.findall("actor"):
            actors.append(
                Actor(
                    name=get_text(el, "name"),
                    role=get_text(el, "role"),
                    thumb=get_text(el, "thumb"),
                )
            )
        meta.actors = actors or None
        # images
        art = root.find("art")
        meta.poster = get_text(art, "poster")
        meta.backdrop = get_text(art, "fanart")
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

        def _meta_keywords(path: Path, *, nfo: bool = False) -> MetaKeywords:
            m = MetaKeywords(path)
            if nfo:
                m.nfo_path = Path(m.item_dir) / f"{m.item_name}.nfo"
                if not m.nfo_path.exists():
                    m.nfo_type = NFOType.MOVIE
            m.language = lib.language
            m.title = extract_title(m.item_name)
            m.year = extract_year(m.item_name)
            return m

        dir = Path(lib.dir)
        parts = path.relative_to(dir).parts
        if len(parts) == 1:
            m = _meta_keywords(path, nfo=True)
            await MediaItemService.create(lib.id, None, m)
            result.append(m)
        elif len(parts) == 2:
            # create parent item for the directory
            m1 = _meta_keywords(dir / parts[0], nfo=True)
            p = await MediaItemService.create(lib.id, None, m1)
            result.append(m1)
            # create child item for the file
            m2 = _meta_keywords(path)
            await MediaItemService.create(lib.id, p.id, m2)
            result.append(m2)

        return result


_HANDLERS[LibType.MOVIE] = MovieMediaHandler()
