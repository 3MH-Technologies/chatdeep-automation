"""Image preparation that mirrors the browser client's canvas pipeline.

The site (``processImage`` in dsc-chat.js) accepts JPEG/PNG/GIF/WebP up to
4 per message, re-encodes every image through a ``<canvas>`` (which strips
EXIF), downscales to ``MAX_EDGE = 2048`` px at quality 0.9, and retries at
JPEG q=0.85 with 75% edges until the base64 payload is ≤ 5 MiB.  Only the
resulting *data URL* is ever transmitted.

This module reproduces that behaviour with Pillow.  GIFs are flattened to
their first frame (a canvas draw does the same in the browser).
"""

from __future__ import annotations

import base64
import io
import os
from typing import Union

from .errors import ValidationError

MAX_EDGE = 2048
MAX_BYTES = 5 * 1024 * 1024          # maxImageBytes from the widget config
SUPPORTED_INPUT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}

PathLike = Union[str, "os.PathLike[str]", bytes, io.IOBase]


def _mime_for(fmt: str) -> str:
    return {
        "JPEG": "image/jpeg",
        "PNG": "image/png",
        "WEBP": "image/webp",
    }.get(fmt.upper(), "image/jpeg")


def _data_url(raw: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def data_url_bytes(data_url: str) -> int:
    """Decoded byte-size of a data URL (same estimate the site uses)."""
    try:
        b64 = data_url[data_url.index(",") + 1:]
    except ValueError:
        return 0
    return len(b64) * 3 // 4


def prepare_image(source: PathLike, *, max_edge: int = MAX_EDGE,
                  max_bytes: int = MAX_BYTES) -> str:
    """Load *source* and return a base64 data URL ready for the chat payload.

    Args:
        source: file path, raw ``bytes``, or an open binary file object.
        max_edge: longest allowed side in px (site default: 2048).
        max_bytes: max decoded size (site default: 5 MiB).

    Raises:
        ValidationError: unsupported format or the image cannot be brought
            under *max_bytes* (``dsc_bad_image`` semantics).
    """
    try:
        from PIL import Image  # lazy import: Pillow only needed for images
    except ImportError as exc:  # pragma: no cover
        raise ValidationError(
            "معالجة الصور تتطلب Pillow — ثبّته عبر: pip install pillow",
            code="dsc_bad_image",
        ) from exc

    if isinstance(source, bytes):
        blob: io.BytesIO = io.BytesIO(source)
        name = "<bytes>"
    elif isinstance(source, io.IOBase):
        blob = source  # type: ignore[assignment]
        name = getattr(source, "name", "<stream>")
    else:
        path = os.fspath(source)
        ext = os.path.splitext(path)[1].lower()
        if ext and ext not in SUPPORTED_INPUT:
            raise ValidationError(
                f"صيغة غير مدعومة: {ext} (المسموح: JPEG/PNG/GIF/WebP)",
                code="dsc_bad_image",
            )
        blob = io.BytesIO(open(path, "rb").read())
        name = path

    try:
        img = Image.open(blob)
        img.load()
    except Exception as exc:
        raise ValidationError(f"تعذّر فتح الصورة {name}: {exc}", code="dsc_bad_image") from exc

    # Flatten transparency/animation like a canvas draw would.
    fmt = (img.format or "PNG").upper()
    if fmt == "GIF":  # keep first frame only
        img.seek(0)
    out_fmt = "PNG" if fmt == "PNG" else ("WEBP" if fmt == "WEBP" else "JPEG")

    if out_fmt == "JPEG" and img.mode not in ("RGB", "L"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
        img = bg
    elif out_fmt != "PNG" and img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    if out_fmt == "PNG" and img.mode == "P":
        img = img.convert("RGBA")

    quality = 0.9
    edge = max_edge
    while True:
        w, h = img.size
        scale = min(1.0, edge / max(w, h))
        resized = img.resize((max(1, round(w * scale)), max(1, round(h * scale))),
                             Image.LANCZOS) if scale < 1.0 else img
        buf = io.BytesIO()
        save_kw: dict = {"format": out_fmt}
        if out_fmt in ("JPEG", "WEBP"):
            save_kw["quality"] = int(quality * 100)
        if out_fmt == "JPEG":
            save_kw["optimize"] = True
        resized.save(buf, **save_kw)
        raw = buf.getvalue()
        if len(raw) <= max_bytes or edge <= 512:
            break
        # Same retry loop as the site: JPEG q=0.85, edges * 0.75.
        edge = round(edge * 0.75)
        out_fmt, quality = "JPEG", 0.85
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

    if len(raw) > max_bytes:
        raise ValidationError(
            f"الصورة {name} أكبر من {max_bytes // (1024 * 1024)} ميغابايت حتى بعد التصغير",
            code="dsc_bad_image",
        )
    return _data_url(raw, _mime_for(out_fmt))


def prepare_images(sources, *, max_images: int = 4,
                   max_bytes: int = MAX_BYTES) -> list:
    """Validate the count and prepare every image into data URLs."""
    sources = list(sources or [])
    if len(sources) > max_images:
        raise ValidationError(
            f"الحد الأقصى {max_images} صور في الرسالة (المرفوع: {len(sources)})",
            code="dsc_too_many_images",
        )
    return [prepare_image(s, max_bytes=max_bytes) for s in sources]
