"""Loading recorded broker responses and replaying offline streams (M1/E1-S1).

Parsing a broker's wire format needs authentic recorded payloads on disk: real HTTP and WebSocket
responses captured once, sanitized, and checked into tests/fixtures/ for deterministic, offline CI.

Provides:
- load_fixture: load JSON, text, or binary fixtures from tests/fixtures/
- recorded_transport: in-process httpx MockTransport with longest-prefix route matching
- RecordedWebSocketClient: in-memory mock WebSocket stream replaying recorded frames
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Self

import httpx

__all__ = [
    "RecordedWebSocketClient",
    "fixtures_dir",
    "load_fixture",
    "recorded_transport",
]

_FIXTURE_DIRNAME = "fixtures"
_TESTS_DIRNAME = "tests"
_MAX_PARENTS = 6


def _caller_directory() -> Path:
    """The directory holding the module that called into this one."""
    frame = inspect.currentframe()
    while frame is not None and frame.f_globals.get("__name__") == __name__:
        frame = frame.f_back
    if frame is None:
        return Path.cwd()
    filename = frame.f_globals.get("__file__")
    return Path.cwd() if filename is None else Path(filename).resolve().parent


def fixtures_dir(start: Path | None = None) -> Path:
    """The ``tests/fixtures/`` directory that owns the calling test.

    Searched outward from ``start`` (the calling module's directory by default): the first
    ancestor holding either ``tests/fixtures/`` or ``fixtures/`` wins.
    """
    searched: list[Path] = []
    current = Path(start).resolve() if start is not None else _caller_directory()
    for directory in (current, *current.parents):
        candidate = directory / _TESTS_DIRNAME / _FIXTURE_DIRNAME
        if candidate.is_dir():
            return candidate
        candidate = directory / _FIXTURE_DIRNAME
        if candidate.is_dir():
            return candidate
        searched.append(directory)
        if len(searched) > _MAX_PARENTS:
            break
    raise FileNotFoundError(
        f"no {_TESTS_DIRNAME}/{_FIXTURE_DIRNAME} directory found above {current}; looked in: "
        + ", ".join(str(d) for d in searched)
    )


def load_fixture(
    relative_path: str | Path,
    *,
    start: Path | None = None,
    binary: bool = False,
) -> Any:
    """Read a recorded broker response as JSON, text, or binary relative to fixtures_dir().

    Args:
        relative_path: Path relative to tests/fixtures/
        start: Optional starting search directory
        binary: If True, returns raw bytes without decoding

    Raises:
        FileNotFoundError: If fixture does not exist on disk
        json.JSONDecodeError: If JSON decoding fails
    """
    candidate = Path(relative_path)
    if candidate.is_absolute():
        raise ValueError(
            f"load_fixture() takes a path relative to {_TESTS_DIRNAME}/{_FIXTURE_DIRNAME}/, "
            f"not an absolute path: {relative_path}"
        )
    directory = fixtures_dir(start)
    path = directory / candidate
    if not path.is_file():
        raise FileNotFoundError(
            f"no recorded broker response at {relative_path}; looked in {directory}"
        )

    if binary or candidate.suffix in {".bin", ".dat", ".proto"}:
        return path.read_bytes()

    text = path.read_text(encoding="utf-8")
    if candidate.suffix in {".csv", ".txt", ".text"}:
        return text

    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise json.JSONDecodeError(
            f"{error.msg} (not valid JSON) in {path}", error.doc, error.pos
        ) from error


def _content_type(path: Path) -> str:
    if path.suffix == ".csv":
        return "text/csv"
    if path.suffix in {".txt", ".text"}:
        return "text/plain"
    if path.suffix in {".bin", ".dat", ".proto"}:
        return "application/octet-stream"
    return "application/json"


def _load_recorded(path: Path) -> tuple[Any, str]:
    """The recorded body and its content type."""
    if path.suffix in {".bin", ".dat", ".proto"}:
        return path.read_bytes(), "application/octet-stream"
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        return json.loads(text), "application/json"
    return text, _content_type(path)


def recorded_transport(
    routes: Mapping[str, str],
    *,
    fixtures: Path,
    fallback: Callable[[httpx.Request], httpx.Response] | None = None,
) -> httpx.MockTransport:
    """Replay recorded broker responses over an in-process HTTP transport.

    ``routes`` maps a path fragment to a fixture file *relative to* ``fixtures``
    (``{"/marketfeed/quote": "dhan/quote.json"}``). A request is served by the longest
    fragment contained in its path, so ``/instruments/historical/738561/day`` matches
    ``/instruments/historical`` and not the shorter ``/instruments``.
    """
    directory = Path(fixtures)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        match: str | None = None
        for fragment in routes:
            if fragment in path and (match is None or len(fragment) > len(match)):
                match = fragment
        if match is None:
            if fallback is not None:
                return fallback(request)
            return httpx.Response(
                404,
                json={"status": "error", "message": f"no recording for {request.method} {path}"},
            )
        body, content_type = _load_recorded(directory / routes[match])
        if content_type == "application/json":
            return httpx.Response(200, json=body)
        if content_type == "application/octet-stream":
            return httpx.Response(200, content=body, headers={"content-type": content_type})
        return httpx.Response(200, text=body, headers={"content-type": content_type})

    return httpx.MockTransport(handler)


class RecordedWebSocketClient:
    """In-memory offline WebSocket client that replays pre-recorded broker frames.

    Provides deterministic streaming for binary feeds (KiteTicker, Dhan binary feed,
    SmartStream, Upstox Proto feed) without network IO.
    """

    def __init__(
        self,
        frames: Sequence[str | bytes | dict[str, Any]] | None = None,
        *,
        loop_stream: bool = False,
    ) -> None:
        self._raw_frames = list(frames or [])
        self._loop_stream = loop_stream
        self._sent_messages: list[str | bytes | dict[str, Any]] = []
        self._is_connected = False
        self._index = 0

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    @property
    def sent_messages(self) -> list[str | bytes | dict[str, Any]]:
        return list(self._sent_messages)

    async def connect(self) -> None:
        self._is_connected = True

    async def disconnect(self) -> None:
        self._is_connected = False

    async def send(self, message: str | bytes | dict[str, Any]) -> None:
        if not self._is_connected:
            raise RuntimeError("Cannot send on disconnected WebSocket")
        self._sent_messages.append(message)

    async def receive(self) -> str | bytes:
        if not self._is_connected:
            raise RuntimeError("Cannot receive on disconnected WebSocket")
        if self._index >= len(self._raw_frames):
            if self._loop_stream and self._raw_frames:
                self._index = 0
            else:
                raise StopAsyncIteration("End of recorded WebSocket frames")
        frame = self._raw_frames[self._index]
        self._index += 1
        if isinstance(frame, dict):
            return json.dumps(frame)
        return frame

    def __aiter__(self) -> RecordedWebSocketClient:
        return self

    async def __anext__(self) -> str | bytes:
        try:
            return await self.receive()
        except StopAsyncIteration:
            raise
        except Exception as exc:
            raise StopAsyncIteration from exc

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        await self.disconnect()
