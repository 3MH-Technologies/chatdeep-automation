"""Unit tests for the SSE parser (no network)."""
from chatdeep.sse import iter_events, parse_block


def _chunks(text: str, size: int = 7):
    """Split into awkward chunk sizes to exercise buffering."""
    for i in range(0, len(text), size):
        yield text[i:i + size]


def test_parse_block_basic():
    event, data = parse_block('event: delta\ndata: {"t":"hi"}')
    assert event == "delta"
    assert data == '{"t":"hi"}'


def test_parse_block_multiline_data_concat():
    # site semantics: trimmed data lines are concatenated
    event, data = parse_block('event: done\ndata: {"a":\ndata: 1}')
    assert event == "done"
    assert data == '{"a":1}'


def test_stream_full_sequence():
    raw = (
        "event: reasoning\r\n"
        "data: {}\r\n"
        "\r\n"
        "event: delta\r\n"
        'data: {"t":"مرح"}\r\n'
        "\r\n"
        "event: delta\r\n"
        'data: {"t":"با"}\r\n'
        "\r\n"
        "event: done\r\n"
        'data: {"finish":"stop","reasoned_ms":1234,"quota":{"day_used":1}}\r\n'
        "\r\n"
    )
    events = list(iter_events(_chunks(raw)))
    kinds = [e.kind for e in events]
    assert kinds == ["reasoning", "delta", "delta", "done"]
    assert events[1].text == "مرح"
    assert events[2].text == "با"
    assert events[3].data["finish"] == "stop"
    assert events[3].data["reasoned_ms"] == 1234


def test_trailing_block_without_final_blank_line():
    raw = 'event: delta\ndata: {"t":"x"}'
    events = list(iter_events([raw]))
    assert len(events) == 1 and events[0].text == "x"


def test_malformed_json_skipped():
    raw = 'event: delta\ndata: {oops}\n\nevent: delta\ndata: {"t":"ok"}\n\n'
    events = list(iter_events(_chunks(raw, 5)))
    assert [e.text for e in events] == ["ok"]


def test_error_event():
    raw = 'event: error\ndata: {"code":"dsc_upstream","message":"boom"}\n\n'
    ev = list(iter_events([raw]))[0]
    assert ev.kind == "error"
    assert ev.data["code"] == "dsc_upstream"


def test_bytes_chunks():
    raw = b'event: delta\ndata: {"t":"b"}\n\n'
    events = list(iter_events([raw[:10], raw[10:]]))
    assert events[0].text == "b"
