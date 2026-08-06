"""G6 · image auto-orientation tests (Pillow-based — runs in the backend image/CI).

Validates EXIF auto-orientation is applied (safe) and that a correctly-oriented,
already-small image is returned untouched. OSD is opt-in/off so not exercised here
(it needs a real scan corpus; see config.ocr_osd_autorotate).
"""
from __future__ import annotations

import io

from PIL import Image

from app import ingestion_vision as iv


def _jpeg(size=(800, 400), exif_orient=None) -> bytes:
    im = Image.new("RGB", size, "white")
    if exif_orient is not None:
        ex = im.getexif()
        ex[0x0112] = exif_orient
        buf = io.BytesIO()
        im.save(buf, "JPEG", exif=ex)
    else:
        buf = io.BytesIO()
        im.save(buf, "JPEG")
    return buf.getvalue()


def test_exif_orientation_is_corrected():
    # Orientation 6 = a landscape capture that should display as portrait.
    raw = _jpeg((800, 400), exif_orient=6)
    out, mime = iv.prepare_image_for_vision(raw, "image/jpeg")
    assert mime == "image/jpeg"
    w, h = Image.open(io.BytesIO(out)).size
    assert (w, h) == (400, 800), "EXIF rotation not applied"


def test_plain_small_image_untouched():
    raw = _jpeg((800, 400), exif_orient=None)
    out, mime = iv.prepare_image_for_vision(raw, "image/jpeg")
    # No rotation + already within limits → returned as-is.
    assert out == raw


def test_autorotate_noop_without_exif_or_osd():
    img = Image.new("RGB", (640, 480), "white")
    out, changed = iv._autorotate(img, osd_enabled=False)
    assert changed is False
    assert out.size == (640, 480)


def test_autorotate_applies_exif():
    raw = _jpeg((800, 400), exif_orient=6)
    img = Image.open(io.BytesIO(raw))
    out, changed = iv._autorotate(img, osd_enabled=False)
    assert changed is True
    assert out.size == (400, 800)
