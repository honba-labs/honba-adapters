"""Loading recorded broker responses from an adapter's own ``tests/fixtures/``.

Parsing a broker's wire format needs the wire format on disk: a real response, captured once,
checked into git and replayed in every unit test. This is the stdlib-only reader for it.

    from honba_adapters_shared import load_fixture

    def test_dhan_parses_quote() -> None:
        quote = parse_quote(load_fixture("dhan_quote_response.json"))
        assert quote.bid_price == 2450.45

Nothing is cached and nothing is interpreted: the fixture is the recording, read verbatim, so a
regression shows up as a diff against what the broker actually sent.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import httpx

__all__ = ["fixtures_dir", "load_fixture", "recorded_transport"]

_FIXTURE_DIRNAME = "fixtures"
_TESTS_DIRNAME = "tests"
_MAX_PARENTS = 6


def _caller_directory() -> Path:
    """The directory holding the module that called into this one."""
    frame = inspect.currentframe()
    while frame is not None and frame.f_globals.get("__name__") == __name__:
        frame = frame.f_back
    if frame is None:  # pragma: no cover - only if the interpreter dropped every frame
        return Path.cwd()
    filename = frame.f_globals.get("__file__")
    return Path.cwd() if filename is None else Path(filename).resolve().parent


def fixtures_dir(start: Path | None = None) -> Path:
    """The ``tests/fixtures/`` directory that owns the calling test.

    Searched outward from ``start`` (the calling module's directory by default): the first
    ancestor holding either ``tests/fixtures/`` or ``fixtures/`` wins, so a test in
    ``<broker>/tests/test_parsing.py`` and a test in ``<broker>/tests/unit/`` find the same
    directory, and one in ``<broker>/tests/unit/`` can keep a local ``fixtures/`` of its own.

    ``start`` is exposed so a ``conftest.py`` can point at a shared recording tree, and so this
    helper is testable without depending on the call stack.
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


def load_fixture(relative_path: str | Path, *, start: Path | None = None) -> Any:
    """Read a recorded broker response as JSON, relative to :func:`fixtures_dir`.

    Raises :class:`FileNotFoundError` naming every directory searched, so a missing recording
    reads as "record this response" rather than as a mysterious test failure.
    """
    candidate = Path(relative_path)
    if candidate.is_absolute():
        raise ValueError(
            f"load_fixture() takes a path relative to {_TESTS_DIRNAME}/{_FIXTURE_DIRNAME}/, "
            f"not an absolute path: {relative_path}"
        )
    directory = fixtures_dir(start)
    path = directory / candidate
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise FileNotFoundError(
            f"no recorded broker response at {relative_path}; looked in {directory}"
        ) from None
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
    return "application/json"


def _load_recorded(path: Path) -> tuple[Any, str]:
    """The recorded body and its content type. ``.json`` is parsed; anything else is text."""
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
    ``/instruments/historical`` and not the shorter ``/instruments``. A ``.json`` fixture
    is a 200 JSON body; ``.csv`` and other files are 200 text with a matching content type.

    A request that matches no fragment goes to ``fallback`` (an adapter's own handler, for
    stateful endpoints such as the order lifecycle) or, without one, is a 404 naming the
    miss. Deterministic: fixtures are read from disk, never a network or a clock.
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
        return httpx.Response(200, text=body, headers={"content-type": content_type})

    return httpx.MockTransport(handler)
