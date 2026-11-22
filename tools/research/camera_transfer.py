#!/usr/bin/env python3
"""Browse the camera's card and copy files off it over Wi-Fi.

This is the other half of Sony's app: not shooting, but getting the photographs. It uses the
``avContent`` service, which is a different one from ``camera``, and the shapes below are not
guessed — they are the fields Sony's own client reads (``ContentInfo``, ``Original``,
``ContentListSource`` in the shipped 7.6.0 package, E0660):

* every avContent answer has one element, ``result[0]``, and for the list methods that element
  *is* the list (``JsonUtil.getArray(result, 0)`` in ``AvcontentClient``).
* ``getContentList`` (version 1.3) lists ``Content`` entries; each carries ``uri``,
  ``contentKind`` and ``content``, a ``ContentInfo`` holding ``original[]`` (``fileName``,
  ``url``, ``stillObject``), ``largeUrl``, ``smallUrl`` and ``thumbnailUrl``.
* ``getSchemeList``/``getSourceList`` are called at version 1.0 and ``getContentCount`` at 1.2,
  the versions the client itself uses; the count answer is ``[{"count": n}]``.
* a listing is paged by ``stIdx`` and ``cnt``, with a hard ceiling of 24 items per call in the
  client's own code, so a page larger than that is not asked for here.
* the source must be discovered first: ``getSchemeList`` then ``getSourceList``.

**The camera must be in its send mode.** This is measured, not assumed: with the body in
"Control with Smartphone" the ``avContent`` service answers ``getVersions`` but every method
returns error ``12`` (no such method), and the whole device description reports
``ServerType: Control with Smartphone``. File access only exists while the camera is in
"Send to Smartphone" (E0662). So this tool refuses up front, in words, instead of failing later
with a puzzling error code.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scalarwebapi import (  # noqa: E402
    DEFAULT_HOST,
    DEFAULT_PORT,
    ApiError,
    ScalarWebApi,
    TransportError,
)

#: Sony's own client will not ask for more than this in one getContentList call.
MAX_PAGE = 24

#: The device description's server type that means "files are available". Any other value means
#: the camera is in a shooting mode, where avContent has no methods at all.
SEND_MODE = "Send to Smartphone"


def is_send_mode(server_type: str | None) -> bool:
    """Whether the camera reports the mode in which its card is browsable."""
    return bool(server_type) and server_type.strip() == SEND_MODE


def _first_list(answer: Any) -> list[Any]:
    """``result[0]`` when it is a list, else empty: the shape of every avContent list answer."""
    if isinstance(answer, list) and answer and isinstance(answer[0], list):
        return answer[0]
    return []


def content_items(answer: Any) -> list[dict[str, Any]]:
    """The file entries in a ``getContentList`` answer, flattened to what a download needs.

    ``result[0]`` is the list of ``Content`` entries; each entry's ``content`` object holds an
    ``original`` array with the real URLs. Anything that does not match is skipped rather than
    raising, because a card can hold a file type this client has never seen and one odd entry
    must not stop a transfer of the rest. A directory entry has no URL and is skipped too.
    """
    items: list[dict[str, Any]] = []
    for entry in _first_list(answer):
        if not isinstance(entry, dict):
            continue
        info = entry.get("content")
        if not isinstance(info, dict):
            continue
        original = info.get("original") or []
        if not isinstance(original, list):
            original = []
        best: dict[str, Any] | None = None
        for candidate in original:
            if isinstance(candidate, dict) and candidate.get("url"):
                best = candidate
                break
        url = (best or {}).get("url") or info.get("largeUrl") or info.get("smallUrl")
        if not url:
            continue
        items.append({
            "file_name": (best or {}).get("fileName") or Path(str(url)).name,
            "url": url,
            "thumbnail": info.get("thumbnailUrl"),
            "uri": entry.get("uri"),
            "still_object": (best or {}).get("stillObject"),
        })
    return items


def total_count(answer: Any) -> int | None:
    """The ``count`` in a ``getContentCount`` answer (``[{"count": n}]``), when it is one."""
    if isinstance(answer, list) and answer and isinstance(answer[0], dict):
        count = answer[0].get("count")
        return count if isinstance(count, int) and not isinstance(count, bool) else None
    return None


def safe_join(directory: Path, name: str) -> Path:
    """A path inside ``directory`` that cannot escape it.

    A camera sends file names from a FAT filesystem; treating them as trusted would let
    ``../../`` write anywhere, so only the last component is kept.
    """
    return directory / Path(str(name).replace("\\", "/")).name


def download(url: str, destination: Path, *, timeout: float = 60.0) -> int:
    """Copy one file to disk and return the byte count.

    Written to a ``.part`` file and renamed only when complete, so an interrupted transfer never
    leaves a truncated file under the real name; the partial file is removed on failure.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "a6400-transfer"})
    written = 0
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, \
                partial.open("wb") as handle:
            while True:
                chunk = response.read(1 << 16)
                if not chunk:
                    break
                handle.write(chunk)
                written += len(chunk)
        partial.replace(destination)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return written


class Transfer:
    """The avContent conversation: schemes, sources, pages, files."""

    def __init__(self, api: ScalarWebApi) -> None:
        self.api = api

    def schemes(self) -> list[str]:
        result = self.api.call("getSchemeList", service="avContent", version="1.0")
        return [s["scheme"] for s in _first_list(result)
                if isinstance(s, dict) and isinstance(s.get("scheme"), str)]

    def sources(self, scheme: str) -> list[dict[str, Any]]:
        result = self.api.call("getSourceList", [{"scheme": scheme}],
                               service="avContent", version="1.0")
        return [s for s in _first_list(result) if isinstance(s, dict)]

    def count(self, uri: str) -> int | None:
        result = self.api.call("getContentCount", [{"uri": uri, "view": "flat"}],
                               service="avContent", version="1.2")
        return total_count(result)

    def page(self, uri: str, start: int = 0, count: int = MAX_PAGE) -> Any:
        if not 1 <= count <= MAX_PAGE:
            raise ValueError(f"a page holds 1 to {MAX_PAGE} items; Sony's client never asks "
                             "for more")
        return self.api.call(
            "getContentList",
            [{"uri": uri, "stIdx": start, "cnt": count, "view": "flat", "sort": "ascending"}],
            service="avContent", version="1.3")


def server_type(host: str, port: int = 64321, timeout: float = 8.0) -> str | None:
    """The camera's ``X_ServerType``, which names the mode it is in.

    The same value the route decision reads, taken from the device description on the UPnP
    port. Absent or unreadable answers ``None``, which the caller treats as "not send mode"
    rather than guessing.
    """
    import re
    url = f"http://{host}:{port}/DigitalImagingDesc.xml"
    try:
        body = urllib.request.urlopen(
            urllib.request.Request(url, headers={"User-Agent": "a6400-transfer"}),
            timeout=timeout).read().decode(errors="replace")
    except Exception:
        return None
    match = re.search(r"<X_ServerType>([^<]*)<", body)
    return match.group(1).strip() if match else None


def run_live(host: str, port: int, scheme: str | None, limit: int, out: Path | None,
             transcript: Path | None = None) -> int:
    api = ScalarWebApi(host=host, port=port, transcript=transcript)
    transfer = Transfer(api)

    mode = server_type(host)
    if mode is not None and not is_send_mode(mode):
        print(
            f"The camera reports ServerType={mode!r}. File access exists only in "
            f"{SEND_MODE!r} mode; switch the camera to it and run this again (E0662).\n"
            "In a shooting mode the avContent service answers error 12 for every method.",
            file=sys.stderr)
        return 2

    try:
        schemes = transfer.schemes()
    except ApiError as exc:
        if exc.code == 12:
            print(
                "The camera is not serving file access. Its whole device description reports "
                f"ServerType other than {SEND_MODE!r}, which means it is in a shooting mode.\n"
                "Switch the camera to 'Send to Smartphone' and run this again; measured in E0662.",
                file=sys.stderr)
            return 2
        print(f"cannot list schemes: {exc}", file=sys.stderr)
        return 1
    except TransportError as exc:
        print(f"cannot reach the camera at {host}:{port}: {exc}", file=sys.stderr)
        return 2

    print("schemes:", ", ".join(schemes) or "none")
    chosen = scheme or (schemes[0] if schemes else None)
    if not chosen:
        print("the camera reported no browsable source", file=sys.stderr)
        return 1

    try:
        sources = transfer.sources(chosen)
    except (ApiError, TransportError) as exc:
        print(f"cannot list sources for {chosen}: {exc}", file=sys.stderr)
        return 1

    print(f"sources in {chosen}:")
    for source in sources:
        print(f"  {source}")
    if not sources:
        return 1

    uri = sources[0].get("source") or sources[0].get("uri")
    if not uri:
        print("the first source carries no uri", file=sys.stderr)
        return 1

    try:
        reported = transfer.count(str(uri))
    except (ApiError, TransportError) as exc:
        reported = None
        print(f"  (getContentCount failed: {exc})", file=sys.stderr)

    items: list[dict[str, Any]] = []
    start = 0
    while len(items) < limit:
        want = min(MAX_PAGE, limit - len(items))
        try:
            page = content_items(transfer.page(str(uri), start, want))
        except (ApiError, TransportError) as exc:
            print(f"listing stopped at item {start}: {exc}", file=sys.stderr)
            break
        items.extend(page)
        start += want
        if len(page) < want:
            break
    print(f"\n{len(items)} file(s) listed from {uri} (camera counts {reported})")
    for item in items:
        print(f"  {item['file_name']}  {item['url']}")

    failed = 0
    if out:
        for item in items:
            target = safe_join(out, item["file_name"])
            try:
                size = download(item["url"], target)
            except (OSError, ValueError) as exc:     # URLError and timeouts are OSError
                failed += 1
                print(f"  FAILED {item['file_name']}: {exc}", file=sys.stderr)
                continue
            print(f"  saved {item['file_name']} ({size} bytes)")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--scheme", default=None, help="storage, upnp; default is the first offered")
    parser.add_argument("--limit", type=int, default=MAX_PAGE,
                        help="how many files to list (paged 24 at a time)")
    parser.add_argument("--out", type=Path, default=None, help="directory to copy into")
    parser.add_argument("--transcript", type=Path, default=None,
                        help="append every call and the camera's raw answer to this JSONL file")
    args = parser.parse_args(argv)
    if args.limit < 1:
        parser.error("--limit must be at least 1")

    return run_live(args.host, args.port, args.scheme, args.limit, args.out, args.transcript)


if __name__ == "__main__":
    sys.exit(main())