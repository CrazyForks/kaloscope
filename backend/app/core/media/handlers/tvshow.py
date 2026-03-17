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
from app.utils.extractor import (
    extract_episode,
    extract_season,
    extract_title,
    extract_year,
)
from app.utils.xml import get_decimal, get_integer, get_text


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
        meta = MediaMeta()
        root = data.getroot()
        meta.title = get_text(root, "title")
        meta.year = get_integer(root, "year")
        art = root.find("art")
        meta.poster = get_text(art, "poster")
        meta.backdrop = get_text(art, "fanart")
        meta.rating = get_decimal(root, "rating")
        meta.season = get_integer(root, "season")
        meta.episode = get_integer(root, "episode")
        return meta

    async def gen_items(self, lib: MediaLib, path: Path) -> list[MetaKeywords]:
        """Generate the media items for a TV show.

        Args:
            lib: The media library instance.
            path: The path to generate from.

        Returns:
            The list of metadata keywords for the media items.
        """
        result = []

        def _parent(path: Path, *, series: str, season: int | None) -> MetaKeywords:
            m = MetaKeywords(path)
            m.nfo_path = Path(m.item_dir) / f"{m.item_name}.nfo"
            if not m.nfo_path.exists():
                m.nfo_type = NFOType.TV_SHOW
            m.language = lib.language
            m.title = extract_title(series)
            m.year = extract_year(series)
            m.season = season
            return m

        def _child(path: Path, *, parent: MetaKeywords) -> MetaKeywords:
            m = MetaKeywords(path)
            if m.item_name != (dir := Path(m.item_dir)).name:
                m.nfo_path = dir / f"{m.item_name}.nfo"
                if not m.nfo_path.exists():
                    m.nfo_type = NFOType.EPISODE
            m.language = lib.language
            m.title = parent.title
            m.year = parent.year
            m.season = parent.season
            m.episode = extract_episode(m.item_name)
            return m

        dir = Path(lib.dir)
        parts = path.relative_to(dir).parts
        series = parts[0]
        # extract metadata for the parent item
        if len(parts) == 2:
            m1 = _parent(
                dir / series, series=series, season=extract_season(series) or 1
            )
        elif len(parts) == 3:
            season = parts[1]
            m1 = _parent(
                dir / series / season, series=series, season=extract_season(season)
            )
        else:
            return result

        # create parent item for the directory
        p = await MediaItemService.create(lib.id, None, m1)
        result.append(m1)
        # create child item for the file
        m2 = _child(path, parent=m1)
        await MediaItemService.create(lib.id, p.id, m2)
        result.append(m2)

        return result


_HANDLERS[LibType.TV_SHOW] = TVShowMediaHandler()
