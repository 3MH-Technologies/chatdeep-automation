"""Server-Sent-Events parser that mirrors ``readStream`` from dsc-chat.js.

The chat endpoint streams ``text/event-stream`` blocks separated by a blank
line.  Each block contains ``event: <kind>`` and one or more ``data: <json>``
lines.  The site's parser:

* strips ``\\r``,
* concatenates *trimmed* ``data:`` payloads of one block,
* JSON-decodes the concatenation,
* dispatches on ``event``: ``delta`` (``{"t": "..."}``), ``reasoning``,
  ``done``, ``error``.

This module implements the same semantics over any iterator of ``bytes`` or
``str`` chunks, so it works with ``requests`` (``iter_content``),
``httpx`` or unit-test fixtures.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, Iterator, Tuple, Union

from .models import EVENT_DELTA, EVENT_DONE, EVENT_ERROR, EVENT_REASONING, StreamEvent

Chunk = Union[bytes, str]


def parse_block(block: str) -> Tuple[str, str]:
    """Parse one SSE block into ``(event, data)`` following the site's rules."""
    event, data = "message", ""
    for line in block.split("\n"):
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data += line[5:].strip()
    return event, data


def iter_events(chunks: Iterable[Chunk]) -> Iterator[StreamEvent]:
    """Yield :class:`StreamEvent` objects from a stream of raw chunks.

    Blocks are delimited by ``\n\n`` (after ``\r`` removal).  A trailing
    non-empty buffer at EOF is dispatched as a final block, exactly like the
    browser client does.  Undecodable JSON payloads are skipped.
    """
    buffer = ""
    for chunk in chunks:
        if isinstance(chunk, bytes):
            chunk = chunk.decode("utf-8", errors="replace")
        buffer += chunk.replace("\r", "")
        while True:
            idx = buffer.find("\n\n")
            if idx == -1:
                break
            block, buffer = buffer[:idx], buffer[idx + 2:]
            ev = _dispatch(block)
            if ev is not None:
                yield ev
    if buffer.strip():
        ev = _dispatch(buffer)
        if ev is not None:
            yield ev


def _dispatch(block: str) -> StreamEvent | None:
    event, data = parse_block(block)
    if not data:
        return None
    try:
        payload: Dict[str, Any] = json.loads(data)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        payload = {"value": payload}
    # The site only reacts to these four kinds; everything else is ignored
    # there, but we surface it so automation can observe protocol changes.
    if event not in (EVENT_DELTA, EVENT_REASONING, EVENT_DONE, EVENT_ERROR):
        return StreamEvent(kind=event or "message", data=payload)
    if event == EVENT_DELTA and not isinstance(payload.get("t"), str):
        return None  # site ignores malformed deltas
    return StreamEvent(kind=event, data=payload)
