import asyncio
import base64
import uuid
from functools import cached_property
from multiprocessing.synchronize import Event, Lock
from pathlib import Path
from typing import Any

import aiofiles
from git import GitError, InvalidGitRepositoryError
from git.repo import Repo
from sanic import Sanic
from sanic.log import Colors, logger
from sanic.request.form import File
from tortoise import timezone

from app.core.config import KaloscopeConfig
from app.core.constants import ENCODING
from app.models.flow import FlowRepository, FlowTemplate
from app.utils import json


class RepoFetcher:
    """The GitHub repository fetcher."""

    _REPO_FETCHER = "repo_fetcher"

    def __init__(self, app: Sanic):
        """Initialize the repository fetcher.

        Args:
            app: The Sanic application.
        """
        self._app = app
        self._task = None
        # ensure that only one instance is running
        if self._fetcher_lock.acquire(block=False):
            try:
                if not self._fetcher_flag.is_set():
                    self._task = self._REPO_FETCHER
                    self._fetcher_flag.set()
            finally:
                self._fetcher_lock.release()

    @cached_property
    def _fetcher_lock(self) -> Lock:
        return self._app.shared_ctx.flow_lock

    @cached_property
    def _fetcher_flag(self) -> Event:
        return self._app.shared_ctx.repo_fetcher_flag

    async def start(self):
        """Start the repository fetcher."""
        if self._task:
            self._app.add_task(self.interval(), name=self._task)

    async def shutdown(self):
        """Shutdown the repository fetcher."""
        if self._task:
            self._fetcher_flag.clear()
            await self._app.cancel_task(self._task)

    async def interval(self):
        """Synchronize the flow repositories."""
        while True:
            try:
                repositories = await FlowRepository.all()
                for repo in repositories:
                    await fetch_origin(repo)

                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.error("Failed to synchronize the repositories!", exc_info=True)
                # wait for 10 minutes before retrying
                await asyncio.sleep(600)


async def fetch_origin(repo: FlowRepository):
    """Fetch the latest changes from the remote repository.

    Args:
        repo: The repository to synchronize.
    """
    repo_dir = Path(KaloscopeConfig.get_workspace("repositories"))
    repo_path = repo_dir / repo.repo_name
    repo_path.mkdir(parents=True, exist_ok=True)
    try:
        try:
            # try to pull the latest changes from the remote repository
            Repo(repo_path).remotes.origin.pull()
            logger.info(
                f"Pulled latest changes for repository: {Colors.GREEN}%s{Colors.END}",
                repo_path,
            )
        except InvalidGitRepositoryError:
            # if the path is not a valid git repository, clone it
            Repo.clone_from(repo.repo_url, to_path=repo_path)
            logger.info(
                f"Cloned repository from %s to: {Colors.GREEN}%s{Colors.END}",
                repo.repo_url,
                repo_path,
            )
    except GitError:
        logger.error(
            f"Failed to synchronize the repository: {Colors.RED}%s{Colors.END}",
            repo.repo_name,
            exc_info=True,
        )

    # scan the directory for changes
    await scan_directory(repo_path, repo.repo_name)

    # update the last synchronized time
    repo.updated_at = timezone.now()
    await repo.save()


async def scan_directory(path: Path, repo: str):
    """Scan the directory for flow graph templates and update the database.

    Args:
        path: The path to the repository.
        repo: The name of the repository.
    """
    # update the newest flag for all templates in the repository
    await FlowTemplate.filter(repo_id=repo).update(newest=False)

    # find all JSON files in the repository
    existing_ids = []
    for file in [*path.glob("*.json"), *path.glob("*/*.json")]:
        # read and parse the JSON file
        async with aiofiles.open(file, "rb") as f:
            data = json.try_loads((await f.read()).decode(ENCODING), with_comments=True)

        # validate the template
        if not isinstance(data, dict):
            continue
        name = data.get("name")
        category = data.get("category")
        revision = data.get("revision")
        definition = data.get("definition")
        if not name or not category or not revision or not definition:
            continue

        tmpl_path = str(file.relative_to(path))
        tmpl = await FlowTemplate.get_or_none(
            repo_id=repo, path=tmpl_path, revision=revision
        )
        if not tmpl:
            # create a new template
            await FlowTemplate.create(
                repo_id=repo,
                path=tmpl_path,
                name=name,
                icon=await save_icon(data.get("icon")),
                description=data.get("description"),
                category=category,
                revision=revision,
                definition=definition,
                newest=True,
            )
        else:
            # collect the IDs of existing templates
            existing_ids.append(tmpl.id)

    if existing_ids:
        # update the newest flag for existing templates
        await FlowTemplate.filter(id__in=existing_ids).update(newest=True)


async def save_icon(icon: Any) -> str | None:
    """Save the icon file to the workspace.

    Args:
        icon: The uploaded icon file or a base64 encoded string.

    Returns:
        The icon file path, or None if no icon is provided.
    """
    if icon is None:
        return None

    icon_bytes = None
    if isinstance(icon, str):
        # decode base64 encoded icon string
        b64str = icon.split(",")[1]
        icon_bytes = base64.b64decode(b64str)
    elif isinstance(icon, File):
        # get the body of the uploaded file
        icon_bytes = icon.body
    elif isinstance(icon, bytes):
        # if icon is already bytes, use it directly
        icon_bytes = icon

    if not icon_bytes:
        logger.error("No icon data provided.")
        return None

    # save the icon file
    icon_dir = Path(KaloscopeConfig.get_workspace("images")) / "icons"
    icon_dir.mkdir(parents=True, exist_ok=True)
    icon_file = f"{uuid.uuid4().hex}.webp"
    async with aiofiles.open(icon_dir / icon_file, "wb") as f:
        await f.write(icon_bytes)
    return f"icons/{icon_file}"
