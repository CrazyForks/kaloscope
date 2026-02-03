from sanic import Blueprint, HTTPResponse, Request, empty, json
from sanic_ext import validate
from tortoise.expressions import Q

from app.core.middleware import SessionHolder
from app.models.base import IDs, KVPair
from app.models.user import (
    FavoriteQuery,
    User,
    UserAvatar,
    UserCreate,
    UserFavorite,
    UserInfo,
    UserPwd,
    UserQuery,
    UserRole,
)
from app.services.user import UserFavoriteService, UserService

# subroutes for all user related operations
user = Blueprint("user", url_prefix="/user")


@user.get("/count")
async def count(_) -> HTTPResponse:
    """Get the count of users."""
    return json(await User.all().count())


@user.get("/list")
@validate(query=UserQuery)
async def list_users(_, query: UserQuery) -> HTTPResponse:
    """List the users."""
    queries = []
    if query.username:
        queries.append(Q(username__icontains=query.username))
    page = await UserService.dump_page(await User.page(*queries, **query.page_params))
    # get the last activity of the online users
    sessions = SessionHolder.get_sessions()
    activities = {
        u.id: u.last_activity
        for u in sorted(sessions.values(), key=lambda u: u.last_activity)
    }
    for user in page["list"]:
        user["last_activity"] = activities.get(user["id"])
    return json(page)


@user.post("/create")
@validate(form=UserCreate)
async def create_user(_, body: UserCreate) -> HTTPResponse:
    """Create a new user."""
    await UserService.create(body.username, body.password)
    return empty()


@user.post("/create_admin")
@validate(form=UserCreate)
async def create_admin(_, body: UserCreate) -> HTTPResponse:
    """Create the first admin user if no users exist."""
    count = await User.all().count()
    if not count:
        await UserService.create(body.username, body.password, UserRole.ADMIN)
    return empty()


@user.post("/delete")
@validate(json=IDs)
async def delete_users(_, body: IDs) -> HTTPResponse:
    """Delete the users."""
    await User.filter(id__in=body.ids).delete()
    return empty()


@user.post("/change_pwd")
@validate(form=UserPwd)
async def change_pwd(request: Request, body: UserPwd) -> HTTPResponse:
    """Change current user's password."""
    user: UserInfo = request.ctx.user
    await UserService.change_pwd(user.username, body.cur_pwd, body.new_pwd)
    return empty()


@user.post("/change_avatar")
@validate(form=UserAvatar)
async def change_avatar(request: Request, body: UserAvatar) -> HTTPResponse:
    """Change current user's avatar."""
    user: UserInfo = request.ctx.user
    return json(await UserService.change_avatar(user.id, body.avatar))


@user.post("/update_pref")
@validate(json=KVPair)
async def update_pref(request: Request, body: KVPair) -> HTTPResponse:
    """Update current user's preference."""
    user: UserInfo = request.ctx.user
    await UserService.update_pref(user.id, body)
    return json(body.model_dump())


@user.post("/favorites")
@validate(json=FavoriteQuery)
async def list_favorites(request: Request, body: FavoriteQuery) -> HTTPResponse:
    """List the current user's favorites."""
    user: UserInfo = request.ctx.user
    queries = [Q(user_id=user.id)]
    if body.indexer_id:
        queries.append(Q(indexer_id=body.indexer_id))
    if body.rsrc_ids:
        queries.append(Q(rsrc_id__in=body.rsrc_ids))
    page = await UserFavorite.page(*queries, **body.page_params)
    return json(await UserFavoriteService.dump_page(page))
