import mimetypes
from datetime import UTC, datetime
from pathlib import Path

import aiofiles
from lxml import etree
from sanic.log import Colors, logger

from app.core.constants import ENCODING, NFO_MIME_TYPE
from app.core.flow.context import RETVAL_KEY, Context
from app.core.media.handlers.base import MediaMeta, get_handler
from app.core.renderer import render
from app.models.media import MediaItem, MediaLib, NFOType

# the path to the NFO templates
TEMPLATES_PATH = Path(__file__).resolve().parents[3] / "static/templates"


def is_nfo(path: Path | str) -> bool:
    """Check if the path is an NFO file.

    Args:
        path: The path to check.

    Returns:
        True if the path is an NFO file, False otherwise.
    """
    if not isinstance(path, Path):
        path = Path(path)
    mime_type, _ = mimetypes.guess_file_type(path)
    return mime_type == NFO_MIME_TYPE


def is_locked(path: Path | str) -> bool:
    """Check if the NFO file is locked by reading the <lockdata> tag.

    Args:
        path: Path to the NFO file.

    Returns:
        True if the NFO file is locked, False otherwise.
    """
    if not isinstance(path, Path):
        path = Path(path)
    if not (path.exists() and path.is_file()):
        return False
    try:
        for _, element in etree.iterparse(path, events=("end",)):
            if element.tag == "lockdata":
                text = element.text
                element.clear()
                return text and text.lower() == "true"
        return False
    except Exception:
        logger.error("Failed to read existing NFO file!", exc_info=True)
        return False


async def gen_nfo(context: Context, tmpl: NFOType):
    """Generate NFO file for the given context and template type.

    Args:
        context: Context of the flow execution.
        tmpl: Template type to use for NFO generation.
    """
    bootparams = context.bootparams
    # ensure we have NFO type and path
    nfo_type = bootparams.get("nfo_type")
    nfo_path = bootparams.get("nfo_path")
    if not nfo_type or not nfo_path:
        return
    # ensure we have return value
    retval = context.get(RETVAL_KEY)
    if not retval or not isinstance(retval, list):
        return
    # check if NFO file is locked
    if is_locked(nfo_path):
        logger.info("NFO file is locked, skipping generation: %s", nfo_path)
        return
    # generate NFO file
    tmpl_path = TEMPLATES_PATH / f"{tmpl.value}.nfo"
    async with aiofiles.open(tmpl_path, encoding=ENCODING) as f:
        template = await f.read()
    async with aiofiles.open(nfo_path, "w", encoding=ENCODING) as f:
        await f.write(render(template, context=retval[0]))


async def parse_nfo(lib: MediaLib, path: Path | str) -> MediaMeta | None:
    """Parse the NFO file at the given path.

    Args:
        lib: The media library instance.
        path: The path to the NFO file.

    Returns:
        The parsed metadata as a MediaMeta object.
    """
    data = None
    if not isinstance(path, Path):
        path = Path(path)
    if path.exists() and path.is_file():
        try:
            data = etree.parse(path, parser=etree.XMLParser())
        except Exception:
            logger.error(
                f"Failed to parse the NFO file: {Colors.RED}%s{Colors.END}",
                path,
                exc_info=True,
            )

    # extract metadata from the NFO file
    meta = None
    if data is not None:
        handler = get_handler(lib.lib_type)
        meta = handler.extract_meta(data)
        meta.nfo_path = str(path.resolve())

    # update the media item with the metadata
    if meta is not None:
        await MediaItem.filter(
            lib_id=lib.id,
            dir=str(path.parent.resolve()),
            name=path.stem,
        ).update(
            nfo_path=meta.nfo_path,
            nfo_mtime=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC),
            title=meta.title,
            year=meta.year,
            season=meta.season,
            episode=meta.episode,
            poster=meta.poster,
            backdrop=meta.backdrop,
            rating=meta.rating,
        )

    return meta
