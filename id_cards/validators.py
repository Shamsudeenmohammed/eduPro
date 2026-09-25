"""
id_cards/validators.py

Backend photo validation for student passport photographs.

Never trust front-end validation: we re-verify content, format, dimensions,
aspect ratio and file size server side.
"""

import io

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from PIL import Image, ImageOps


class PhotoValidationError(ValidationError):
    pass


DEFAULTS = {
    "MAX_MB": 4,
    "MIN_WIDTH": 300,
    "MIN_HEIGHT": 380,
    "MAX_WIDTH": 4000,
    "MAX_HEIGHT": 4000,
    "MIN_ASPECT": 0.75,   # portrait ≥ 3:4
    "MAX_ASPECT": 1.0,    # portrait ≤ 1:1
    "ALLOWED_FORMATS": ("JPEG", "PNG"),
}


def _validate_payload(image_file, request=None):
    """
    Validate a possibly-untrusted image file object.

    Returns (PIL.Image, normalized_height, normalized_width) on success,
    raises PhotoValidationError otherwise.
    """
    cls = PhotoValidationError

    # 1) File size
    try:
        image_file.seek(0, 2)
        size_bytes = image_file.tell()
        image_file.seek(0)
    except (AttributeError, OSError):
        raise cls(_("Could not read the uploaded file."))

    max_mb = getattr(settings, "ID_CARDS_MAX_PHOTO_MB", DEFAULTS["MAX_MB"])
    max_bytes = int(max_mb * 1024 * 1024)
    if size_bytes <= 0:
        raise cls(_("The uploaded file is empty."))
    if size_bytes > max_bytes:
        raise cls(
            _("The file is too large (%(size)s MB). Maximum allowed is %(max)s MB.")
            % {"size": round(size_bytes / (1024 * 1024), 2), "max": max_mb}
        )

    # 2) Content / validity — Pillow verify() detects corrupt images.
    try:
        image_file.seek(0)
        probe = Image.open(image_file)
        probe.verify()
    except Exception:
        raise cls(_("The uploaded file is not a valid image."))

    # 3) Format
    try:
        image_file.seek(0)
        image = Image.open(image_file)
        fmt = image.format
    except Exception:
        raise cls(_("The uploaded file is not a valid image."))

    allowed = getattr(settings, "ID_CARDS_ALLOWED_FORMATS", DEFAULTS["ALLOWED_FORMATS"])
    if fmt not in allowed:
        raise cls(
            _("Unsupported image format. Allowed formats: %(fmts)s.")
            % {"fmts": ", ".join(allowed)}
        )

    # 4) Load pixels (rejects truncated/corrupt payloads the header check missed)
    try:
        image = ImageOps.exif_transpose(image)
        image.load()
    except Exception:
        raise cls(_("The image data is corrupt and cannot be opened."))

    # 5) Dimensions
    width, height = image.size
    min_w = getattr(settings, "ID_CARDS_PHOTO_MIN_WIDTH", DEFAULTS["MIN_WIDTH"])
    min_h = getattr(settings, "ID_CARDS_PHOTO_MIN_HEIGHT", DEFAULTS["MIN_HEIGHT"])
    max_w = getattr(settings, "ID_CARDS_PHOTO_MAX_WIDTH", DEFAULTS["MAX_WIDTH"])
    max_h = getattr(settings, "ID_CARDS_PHOTO_MAX_HEIGHT", DEFAULTS["MAX_HEIGHT"])
    if width < min_w or height < min_h:
        raise cls(
            _("The photograph is too small. Minimum resolution is %(w)s × %(h)s px. "
              "You uploaded %(aw)s × %(ah)s px.")
            % {"w": min_w, "h": min_h, "aw": width, "ah": height}
        )
    if width > max_w or height > max_h:
        raise cls(
            _("The photograph is too large. Maximum resolution is %(w)s × %(h)s px.")
            % {"w": max_w, "h": max_h}
        )

    # 6) Aspect ratio (portrait passport-style)
    ratio = height / width if width else 0
    min_aspect = getattr(settings, "ID_CARDS_PHOTO_MIN_ASPECT", DEFAULTS["MIN_ASPECT"])
    max_aspect = getattr(settings, "ID_CARDS_PHOTO_MAX_ASPECT", DEFAULTS["MAX_ASPECT"])
    if not (min_aspect <= ratio <= max_aspect):
        raise cls(
            _("The photograph must be portrait (aspecting between %(a)s:%(b)s and "
              "%(c)s:%(d)s). Current ratio is %(r).2f:1.")
            % {
                "a": int(1 / max_aspect * 100), "b": 100,
                "c": int(1 / min_aspect * 100), "d": 100,
                "r": ratio,
            }
        )

    return image, width, height


def validate_photo(file):
    """Validate an uploaded file; raises PhotoValidationError on failure."""
    image, width, height = _validate_payload(file)
    image.close()
    return True


def normalize_photo(file, max_px=800):
    """
    Validate and return a normalized, EXIF-rotated JPEG in memory
    (strictly bounded dimensions), ready for storage.
    """
    image, width, height = _validate_payload(file)
    try:
        image = ImageOps.exif_transpose(image)
        image = image.convert("RGB")

        # Downscale oversized faces while preserving the portrait ratio.
        longest = max(image.size)
        if longest > max_px:
            ratio = max_px / longest
            new_size = (max(1, int(width * ratio)), max(1, int(height * ratio)))
            image = image.resize(new_size, Image.LANCZOS)

        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=90, optimize=True)
        buffer.seek(0)
        return buffer
    finally:
        image.close()