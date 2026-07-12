"""Authorization tests for media delivery routes."""

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import app.main  # noqa: F401  # initialize route imports in application order
from app.core.config import KaloscopeConfig
from app.core.exceptions import ForbiddenException
from app.models.media import TranscodeQuery
from app.routes import media as media_route


def _request():
    return SimpleNamespace(headers={})


def test_stream_rejects_unregistered_path(monkeypatch):
    handler = inspect.unwrap(media_route.get_item_stream)
    request = _request()
    query = TranscodeQuery(path="/private/secret.mkv")

    monkeypatch.setattr(
        media_route.async_os.path, "exists", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        media_route.async_os, "stat", AsyncMock(return_value=SimpleNamespace(st_size=1))
    )
    monkeypatch.setattr(media_route, "file_stream", AsyncMock())
    monkeypatch.setattr(
        media_route.MediaItem,
        "filter",
        lambda **_kwargs: SimpleNamespace(exists=AsyncMock(return_value=False)),
    )

    with pytest.raises(ForbiddenException):
        asyncio.run(handler(request, query))


def test_stream_allows_registered_path(monkeypatch):
    handler = inspect.unwrap(media_route.get_item_stream)
    request = _request()
    query = TranscodeQuery(path="/media/movie.mkv")
    response = object()
    stream = AsyncMock(return_value=response)
    filters = []

    def filter_items(**kwargs):
        filters.append(kwargs)
        return SimpleNamespace(exists=AsyncMock(return_value=True))

    monkeypatch.setattr(media_route.MediaItem, "filter", filter_items)
    monkeypatch.setattr(
        media_route.async_os.path, "exists", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        media_route.async_os, "stat", AsyncMock(return_value=SimpleNamespace(st_size=1))
    )
    monkeypatch.setattr(media_route, "file_stream", stream)

    assert asyncio.run(handler(request, query)) is response
    assert filters == [{"path": "/media/movie.mkv"}]
    stream.assert_awaited_once()


def test_probe_rejects_unregistered_path(monkeypatch):
    handler = inspect.unwrap(media_route.probe_media_duration)
    request = _request()
    query = SimpleNamespace(path="/private/secret.mkv")

    monkeypatch.setattr(
        media_route.MediaItem,
        "filter",
        lambda **_kwargs: SimpleNamespace(exists=AsyncMock(return_value=False)),
    )
    monkeypatch.setattr(
        media_route,
        "probe_duration",
        AsyncMock(side_effect=AssertionError("unauthorized path was probed")),
    )

    with pytest.raises(ForbiddenException):
        asyncio.run(handler(request, query))


def test_hls_rejects_path_outside_transcode_root(monkeypatch, tmp_path):
    handler = inspect.unwrap(media_route.serve_hls_file)
    request = _request()
    root = tmp_path / "transcoded"
    (tmp_path / "secret.ts").write_bytes(b"secret")

    monkeypatch.setattr(
        media_route.MediaItem,
        "filter",
        Mock(return_value=SimpleNamespace(first=AsyncMock(return_value=object()))),
    )
    monkeypatch.setattr(KaloscopeConfig, "get_workspace", lambda _name: root)
    monkeypatch.setattr(
        media_route, "output_dir", lambda hash, profile: root / hash / profile
    )
    monkeypatch.setattr(media_route, "file_stream", AsyncMock())

    with pytest.raises(ForbiddenException):
        asyncio.run(handler(request, ".", "..", "secret", "ts"))


def test_hls_allows_file_in_transcode_root(monkeypatch, tmp_path):
    handler = inspect.unwrap(media_route.serve_hls_file)
    request = _request()
    response = object()
    stream = AsyncMock(return_value=response)
    out_dir = tmp_path / "hash" / "profile"
    out_dir.mkdir(parents=True)
    segment = out_dir / "segment_000000.ts"
    segment.write_bytes(b"segment")
    monkeypatch.setattr(
        media_route.MediaItem,
        "filter",
        Mock(side_effect=AssertionError("HLS queried MediaItem")),
    )
    monkeypatch.setattr(KaloscopeConfig, "get_workspace", lambda _name: tmp_path)
    monkeypatch.setattr(media_route, "output_dir", lambda *_args: out_dir)
    monkeypatch.setattr(media_route, "file_stream", stream)

    result = asyncio.run(
        handler(request, "media-hash", "profile", "segment_000000", "ts")
    )

    assert result is response
    stream.assert_awaited_once()
