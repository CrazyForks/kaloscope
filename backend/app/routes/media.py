import re

from aiofiles import os as async_os
from sanic import Blueprint, HTTPResponse, Request, empty, json
from sanic.exceptions import InvalidRangeType, RangeNotSatisfiable
from sanic.log import logger
from sanic.response import ResponseStream, file_stream
from sanic_ext import validate
from tortoise.expressions import Q, RawSQL

from app.core.decorators import authorize
from app.core.media.shelver import parse_nfo
from app.core.media.watcher import LibWatcher
from app.models.base import IDs, Range
from app.models.flow import GraphCategory
from app.models.media import (
    MediaItem,
    MediaLib,
    MediaLibUpsert,
    MediaQuery,
    MediaStream,
)
from app.models.user import UserInfo, UserRole
from app.services.flow import FlowTriggerService
from app.services.media import MediaItemService, MediaLibService

# subroutes for all media related operations
media = Blueprint("media", url_prefix="/media")


@media.get("/lib/list")
@authorize()
async def list_libraries(request: Request) -> HTTPResponse:
    """List the media libraries."""
    queries = []
    # filter the libraries by the user's permissions
    user: UserInfo = request.ctx.user
    if user.perms is not None:
        queries.append(Q(id__in=user.perms.media_lib_ids))
    # list the libraries without pagination
    media_libs = await MediaLibService.dump_list(MediaLib.filter(*queries))
    # attach the list of flow triggers
    for media_lib in media_libs:
        media_lib["triggers"] = await FlowTriggerService.get_triggers(
            GraphCategory.INGEST, media_lib["id"]
        )
    return json(media_libs)


@media.post("/lib/sort")
@validate(json=IDs)
async def sort_libraries(_, body: IDs) -> HTTPResponse:
    """Sort the media libraries."""
    await MediaLibService.update_priorities(body.ids)
    return empty()


@media.post("/lib/upsert")
@authorize(role=UserRole.ADMIN)
@validate(json=MediaLibUpsert)
async def upsert_library(_, body: MediaLibUpsert) -> HTTPResponse:
    """Create or update a media library."""
    lib = await MediaLibService.upsert(body)
    return json(await MediaLibService.dump(lib))


@media.post("/lib/delete")
@authorize(role=UserRole.ADMIN)
@validate(json=IDs)
async def delete_libraries(_, body: IDs) -> HTTPResponse:
    """Delete the media libraries."""
    for id in body.ids:
        try:
            await MediaLibService.delete(int(id))
        except Exception as e:
            if len(body.ids) == 1:
                raise e
            logger.error("Failed to delete the media library: %s", id, exc_info=True)
    return empty()


@media.get("/lib/<id:int>/scan")
async def scan_library(request: Request, id: int) -> HTTPResponse:
    """Scan the media library."""
    lib = await MediaLib.get(id=id)
    watcher: LibWatcher = request.app.ctx.lib_watcher
    await watcher.scan_directory(lib, validation=True)
    return empty()


@media.get("/list")
@validate(query=MediaQuery)
async def list_items(_, query: MediaQuery) -> HTTPResponse:
    """List the media items."""
    queries = [Q(path=query.path) if query.path else Q(visible=True)]
    if query.lib_id:
        queries.append(Q(lib_id=query.lib_id))
    if query.keyword:
        queries.append(Q(keyword__icontains=query.keyword))
    page = await MediaItem.page(
        *queries,
        **query.page_params,
        annotations={"keyword": RawSQL("IFNULL(title, name)")},
    )
    return json(
        await MediaItemService.dump_page(page, exclude={"lib", "parent", "children"})
    )


@media.get("/<id:int>")
async def get_item_details(_, id: int) -> HTTPResponse:
    """Get the details of the media item."""
    item = await MediaItemService.dump(await MediaItem.get(id=id))
    if nfo_path := item["nfo_path"]:
        item["metadata"] = parse_nfo(item["lib"]["lib_type"], nfo_path)
    return json(item)


@media.get("/stream")
@validate(query=MediaStream)
async def get_item_stream(request: Request, query: MediaStream) -> ResponseStream:
    """Get the media item stream with HTTP Range support."""
    path = query.path
    stat = await async_os.stat(path)
    total = stat.st_size
    headers = {"Accept-Ranges": "bytes"}

    # get the range header from the request
    range = request.headers.get("Range")
    if range:
        # parse the range header
        match = re.match(r"bytes=(\d*)-(\d*)", range)
        if not match:
            raise InvalidRangeType

        start, end = match.groups()
        start = int(start) if start else 0
        end = int(end) if end else total - 1

        # validate range
        if start >= total or end >= total or start > end:
            raise RangeNotSatisfiable

        # stream the requested range
        return await file_stream(
            path,
            headers=headers,
            _range=Range(start=start, end=end, size=end - start + 1, total=total),
        )

    # if no range header, return the entire file
    return await file_stream(path, headers=headers)
