"""Dynamic import and blueprint registration utilities."""

import importlib
import inspect
import os
from glob import glob
from importlib import import_module, util
from inspect import getmembers
from pathlib import Path
from types import ModuleType
from typing import Any

from sanic import Sanic
from sanic.blueprints import Blueprint


def import_subclasses(cls: type, globals: dict[str, Any]):
    """Import all subclasses of a given class in the current directory.

    Args:
        cls: The class to find subclasses.
        globals: The globals dictionary of the caller.
    """
    modname = globals["__name__"]
    modpath = Path(globals["__file__"])
    for file in [
        f for f in os.listdir(modpath.parent) if f.endswith(".py") and f != modpath.name
    ]:
        module = importlib.import_module(f".{file[:-3]}", package=modname)
        for name, member in inspect.getmembers(module):
            if (
                inspect.isclass(member)
                and issubclass(member, cls)
                and member is not cls
            ):
                globals[name] = member


def register_blueprints(
    app: Sanic, *module_names: str | ModuleType, url_prefix: str | None = None
):
    """Modified version of the Sanic auto-discovery example.

    See https://sanic.dev/en/guide/how-to/autodiscovery.html for more details.

    Args:
        app: The Sanic application instance.
        module_names: The module names to search for blueprints.
        url_prefix: The URL prefix to be prepended to all blueprint routes.
    """

    blueprints = set()
    _imported = set()

    def _find_bps(module):
        nonlocal blueprints
        for _, member in getmembers(module):
            if isinstance(member, Blueprint):
                blueprints.add(member)

    for module in module_names:
        if isinstance(module, str):
            module = import_module(module, app.__module__)
        if module.__file__ is not None:
            for path in glob(f"{Path(module.__file__).parent}/**/*.py", recursive=True):
                if path not in _imported:
                    name = "module"
                    if "__init__" in path:
                        *_, name, __ = path.split(os.sep)
                    spec = util.spec_from_file_location(name, path)
                    if spec is not None and spec.loader is not None:
                        specmod = util.module_from_spec(spec)
                        spec.loader.exec_module(specmod)
                        _find_bps(specmod)
                        _imported.add(path)

    if url_prefix is not None:
        app.blueprint(Blueprint.group(*blueprints, url_prefix=url_prefix))
    else:
        for bp in blueprints:
            app.blueprint(bp)
