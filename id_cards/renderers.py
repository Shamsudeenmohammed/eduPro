"""
id_cards/renderers.py

Render ID cards to images (PIL) and to print-ready PDF sheets (reportlab).

* All rendering is done locally — no external service.
* The QR code encodes the *verification URL* derived from the card's secure
  token (never the student ID).
* PDF sheets are laid out for A4 with crop marks.  In duplex mode the back of
  each card is mirrored and column-reversed so that a top-edge (long-edge)
  flip aligns backs with their matching fronts.
"""

import io
import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("eduPro.id_cards")

DPI = 300

# ─────────────────────────────────────────────────────────────────────────────
# FONT RESOLUTION
# ─────────────────────────────────────────────────────────────────────────────

_FONT_DIRS = [
    Path("C:/Windows/Fonts"),
    Path("/usr/share/fonts/truetype/dejavu"),
    Path("/usr/share/fonts/truetype/liberation2"),
    Path("/usr/share/fonts/truetype/liberation"),
]

_FONT_CANDIDATES = [
    ("bold", ["arialbd.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"]),
    ("regular", ["arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"]),
    ("italic", ["ariali.ttf", "DejaVuSans-Oblique.ttf"]),
]

_FONT_CACHE = {}


def _find_font(style):
    if style in _FONT_CACHE:
        return _FONT_CACHE[style]
    path = None
    for directory in _FONT_DIRS:
        if not directory.exists():
            continue
        for name in _FONT_CANDIDATES[0][1] if style == "bold" else (
            _FONT_CANDIDATES[1][1] if style == "regular" else _FONT_CANDIDATES[2][1]
        ):
            candidate = directory / name
            if candidate.exists():
                path = str(candidate)
                break
        if path:
            break
    _FONT_CACHE[style] = path
    return path


def _font(style="regular", size=24):
    path = _find_font(style)
    if path:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


# ─────────────────────────────────────────────────────────────────────────────
# COLOUR HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _rgb(value, fallback="#000000"):
    value = (value or fallback or "").strip().lstrip("#")
    try:
        if len(value) == 3:
            value = "".join(ch * 2 for ch in value)
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        return (0, 0, 0)


def _text_wrap(draw, text, font, max_width):
    """Split ``text`` into lines that fit ``max_width`` px."""
    if not text:
        return []
    raw_lines = text.splitlines() or [text]
    lines = []
    for raw in raw_lines:
        if not raw:
            lines.append("")
            continue
        words = raw.split()
        line = ""
        for word in words:
            trial = f"{line} {word}".strip()
            if draw.textlength(trial, font=font) <= max_width:
                line = trial
            else:
                if line:
                    lines.append(line)
                line = word
        lines.append(line)
    return lines


def _draw_wrapped(draw, xy, text, font, fill, max_width, line_gap=6):
    x, y = xy
    lines = _text_wrap(draw, text, font, max_width)
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += font.size + line_gap
    return y


# ─────────────────────────────────────────────────────────────────────────────
# SIZING
# ─────────────────────────────────────────────────────────────────────────────

def card_size_px(template):
    w = _mm_to_px(template.card_width_mm or 85.60)
    h = _mm_to_px(template.card_height_mm or 53.98)
    return w, h


def _mm_to_px(mm):
    return max(1, int(round(float(mm) / 25.4 * DPI)))


def _px_to_mm(px):
    return float(px) / DPI * 25.4


# ─────────────────────────────────────────────────────────────────────────────
# IMAGE HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _open_image(image_field):
    """Load a model ImageField as a PIL image (best-effort)."""
    if image_field is None:
        return None
    try:
        with image_field.open("rb") as handle:
            img = Image.open(handle)
            img.load()
            return img
    except ValueError:
        # Empty optional field (no background/logo uploaded) — expected.
        return None
    except Exception:  # noqa: BLE001 — missing/corrupt media must not crash
        logger.exception("Could not open image %s", getattr(image_field, "name", ""))
        return None


def _cover(img, size):
    """Resize-and-crop an image to exactly ``size`` (w, h)."""
    img = img.convert("RGB")
    target_w, target_h = size
    ratio = max(target_w / img.width, target_h / img.height)
    img = img.resize((max(1, round(img.width * ratio)), max(1, round(img.height * ratio))),
                     Image.Resampling.LANCZOS)
    left = (img.width - target_w) // 2
    top = (img.height - target_h) // 2
    return img.crop((left, top, left + target_w, top + target_h))


# ─────────────────────────────────────────────────────────────────────────────
# LAYOUT CONSTANTS (px @300dpi, CR80 85.6×53.98 mm → 1011×638)
# ─────────────────────────────────────────────────────────────────────────────

PAD = 26
HEADER_H = 150
PHOTO_W, PHOTO_H = 186, 224
QR_SIZE = 128


# ─────────────────────────────────────────────────────────────────────────────
# FRONT RENDERING
# ─────────────────────────────────────────────────────────────────────────────

def _base_canvas(template, img_size):
    """Blank card canvas with gradient (or background image) fill."""
    w, h = img_size
    img = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(img)

    background = _open_image(template.background)
    if background is not None:
        img.paste(_cover(background, (w, h)), (0, 0))
        return img

    primary = _rgb(template.primary_color, "#1d4ed8")
    secondary = _rgb(template.secondary_color, "#0f172a")
    for y in range(h):
        t = y / max(1, h - 1)
        color = tuple(
            round(p * (1 - t) + s * t) for p, s in zip(primary, secondary)
        )
        draw.line([(0, y), (w, y)], fill=color)
    return img


def _institution_logo(template, target=90):
    logo = _open_image(template.logo_url())
    return _cover(logo, (target, target)) if logo else None


def _photo_image(card, target):
    photo = _open_image(card.photo.image) if card.photo else None
    if photo is None:
        return None
    return _cover(photo, target)


def _qr_image(card, request=None):
    from segno import make_qr
    url = _verification_absolute_url(card, request)
    qrcode = make_qr(url, error="m", boost_error=False)
    buffer = io.BytesIO()
    qrcode.save(buffer, kind="png", scale=1.7, border=0, dark="000000", light="ffffff")
    buffer.seek(0)
    img = Image.open(buffer).convert("1")
    img = img.resize(((img.width // 4) * 4, (img.height // 4) * 4), Image.Resampling.NEAREST)
    return img


def _verification_absolute_url(card, request=None):
    from django.urls import reverse
    path = reverse("id_cards:verify_card", args=[card.verification_token])
    if request is not None:
        return request.build_absolute_uri(path)
    from django.conf import settings
    base = getattr(settings, "SITE_URL", "").rstrip("/")
    return f"{base}{path}" if base else path


def render_front(card, request=None):
    """Return a PIL image of the front of the card."""
    template = card.template
    if template is None:
        template = card.institution.id_card_templates.filter(status="active").first() \
            if hasattr(card.institution, "id_card_templates") else None
    if template is None:
        from .models import IDCardTemplate
        template = IDCardTemplate.objects.filter(
            institution=card.institution, status="active").first()
    if template is None:
        raise ValueError("No template available to render card %s." % card.card_number)

    w, h = card_size_px(template)
    img = _base_canvas(template, (w, h))
    draw = ImageDraw.Draw(img, "RGBA")

    text_color = _rgb(template.text_color, "#ffffff")
    accent = _rgb(template.primary_color, "#1d4ed8")

    # ── Header band ───────────────────────────────────────────────────────
    band = _rgb(template.secondary_color, "#0f172a")
    draw.rectangle([0, 0, w, HEADER_H], fill=band + (235,))

    logo_img = _institution_logo(template, 84)
    inst_name = template.institution.name or ""

    if logo_img:
        img.paste(logo_img, (PAD, (HEADER_H - logo_img.height) // 2), logo_img)
    else:
        draw.rectangle(
            [PAD, (HEADER_H - 56) // 2, PAD + 56, (HEADER_H + 56) // 2],
            fill=accent + (180,),
        )
        draw.text((PAD + 8, (HEADER_H - 24) // 2),
                  (inst_name[:2].upper() if inst_name else "INST"),
                  font=_font("bold", 26), fill=_rgb("#ffffff", "#ffffff"))

    name_x = PAD + (logo_img.width if logo_img else 70)
    _draw_wrapped(draw, (name_x, (HEADER_H - 60) // 2), inst_name,
                  _font("bold", 42), text_color, w - name_x - PAD - 20, 4)

    # ── Body ───────────────────────────────────────────────────────────────
    body_top = HEADER_H + 18
    photo = _photo_image(card, (PHOTO_W, PHOTO_H))
    photo_x = w - PAD - PHOTO_W
    if photo:
        img.paste(photo, (photo_x, body_top))
    else:
        draw.rectangle(
            [photo_x, body_top, photo_x + PHOTO_W, body_top + PHOTO_H],
            fill=(255, 255, 255, 40), outline=(255, 255, 255, 120), width=2,
        )
        initials = _initials(card)
        f = _font("bold", 54)
        tw = draw.textlength(initials, font=f)
        draw.text((photo_x + (PHOTO_W - tw) / 2, body_top + PHOTO_H / 2 - 40),
                  initials, font=f, fill=(255, 255, 255, 120))

    fields = _card_fields(card)
    content_width = photo_x - PAD - 18
    y = body_top

    name = fields.get("full_name") or "Student"
    name_font = _font("bold", 40)
    y = _draw_wrapped(draw, (PAD, y), name, name_font, text_color,
                      content_width, 4)
    y += 8
    draw.line([PAD, y, PAD + content_width, y], fill=(255, 255, 255, 40), width=2)
    y += 12

    rows = _front_rows(template, card, fields)
    for label, value in rows:
        if not value:
            continue
        draw.text((PAD, y), label, font=_font("regular", 18),
                  fill=(255, 255, 255, 170))
        label_w = draw.textlength(f"{label}:", font=_font("regular", 18))
        draw.text((PAD + label_w + 10, y), value, font=_font("bold", 20),
                  fill=text_color)
        y += 28

    # ── Footer / QR ────────────────────────────────────────────────────────
    qr = _qr_image(card, request)
    if qr:
        qr_box = QR_SIZE
        qr_x = w - PAD - qr_box
        qr_y = h - PAD - qr_box
        draw.rectangle([qr_x - 5, qr_y - 5, qr_x + qr_box + 5, qr_y + qr_box + 5],
                       fill=(255, 255, 255, 255))
        qr = qr.resize((qr_box, qr_box), Image.Resampling.NEAREST)
        img.paste(qr, (qr_x, qr_y))

        qr_label = "SCAN TO VERIFY"
        fl = _font("bold", 16)
        draw.text((qr_x - draw.textlength(qr_label, font=fl) - 14,
                   qr_y + qr_box // 2 - 8), qr_label, font=fl, fill=text_color)

    card_no = fields.get("card_number")
    if card_no:
        draw.text((PAD, h - PAD - 40), f"ID: {card_no}", font=_font("bold", 22),
                  fill=text_color)
        dates = []
        if fields.get("issue_date"):
            dates.append("ISSUE {}".format(fields["issue_date"]))
        if fields.get("expiry_date"):
            dates.append("EXP {}".format(fields["expiry_date"]))
        if dates:
            draw.text((PAD, h - PAD - 16), "  ".join(dates),
                      font=_font("regular", 16), fill=(255, 255, 255, 170))

    return img


def _initials(card):
    parts = [card.student.first_name, card.student.last_name]
    return (parts[0][0] if parts[0] else "") + (parts[1][0] if parts[1] else "")


def _card_fields(card):
    from .services import card_fields_for
    fields = card_fields_for(card.student)
    fields.update({
        "card_number": card.card_number,
        "issue_date": card.issue_date.isoformat() if card.issue_date else "",
        "expiry_date": card.expiry_date.isoformat() if card.expiry_date else "",
    })
    return fields


def _front_rows(template, card, fields):
    from .models import DEFAULT_FRONT_FIELDS
    selected = template.front_fields or DEFAULT_FRONT_FIELDS
    pairs = {
        "student_number": ("STUDENT ID", fields.get("student_number", "")),
        "programme": ("PROGRAMME", fields.get("programme", "")),
        "department": ("DEPARTMENT", fields.get("department", "")),
        "faculty": ("FACULTY", fields.get("faculty", "")),
        "issue_date": ("ISSUE DATE", fields.get("issue_date", "")),
        "expiry_date": ("EXPIRY DATE", fields.get("expiry_date", "")),
    }
    return [pairs[key] for key in selected if key in pairs]


# ─────────────────────────────────────────────────────────────────────────────
# BACK RENDERING
# ─────────────────────────────────────────────────────────────────────────────

def render_back(card, request=None):
    """Return a PIL image of the back of the card."""
    template = card.template
    if template is None:
        from .models import IDCardTemplate
        template = IDCardTemplate.objects.filter(
            institution=card.institution, status="active").first()
    if template is None:
        raise ValueError("No template available to render card %s." % card.card_number)

    w, h = card_size_px(template)
    img = _base_canvas(template, (w, h))
    draw = ImageDraw.Draw(img, "RGBA")

    text_color = _rgb(template.text_color, "#ffffff")
    accent = _rgb(template.primary_color, "#1d4ed8")
    muted = (255, 255, 255, 150)

    # ── Watermark ──────────────────────────────────────────────────────────
    wm = _open_image(template.watermark) if template.watermark else None
    if wm is None and template.institution and template.institution.logo:
        wm = _open_image(template.institution.logo)
    if wm:
        wm = _cover(wm, (320, 240)).convert("RGBA")
        wm.putalpha(40)
        img.paste(wm, ((w - wm.width) // 2, (h - wm.height) // 2), wm)

    institution = template.institution
    y = PAD
    _draw_wrapped(draw, (PAD, y), institution.name, _font("bold", 30),
                  text_color, w - 2 * PAD, 4)
    y += 34

    contact = []
    if getattr(institution, "address", None):
        contact.append(str(institution.address))
    if getattr(institution, "phone", None):
        contact.append(f"Tel: {institution.phone}")
    if getattr(institution, "email", None):
        contact.append(f"Email: {institution.email}")
    if getattr(institution, "website", None):
        contact.append(f"Web: {institution.website}")
    for line in contact:
        draw.text((PAD, y), line, font=_font("regular", 18), fill=muted)
        y += 22
    y += 6

    # ── Return instructions box ────────────────────────────────────────────
    ret = (template.return_instructions or
           "If found, please return to the institution registry or any campus security office.")
    ret_box_top = y
    ret_lines = _text_wrap(draw, ret, _font("regular", 18), w - 2 * PAD - 60)
    box_h = len(ret_lines) * 24 + 20
    draw.rounded_rectangle(
        [PAD, ret_box_top, w - PAD - 56, ret_box_top + box_h],
        radius=12, fill=(255, 255, 255, 14), outline=accent + (160,), width=2,
    )
    _draw_wrapped(draw, (PAD + 14, ret_box_top + 10), ret,
                  _font("regular", 18), text_color, w - 2 * PAD - 84, 2)
    y = ret_box_top + box_h + 14

    # ── Terms / signature area ─────────────────────────────────────────────
    terms = template.card_terms or ""
    if terms:
        terms_font = _font("regular", 15)
        terms_lines = _text_wrap(draw, terms, terms_font, (w - 2 * PAD) // 2)
        _draw_wrapped(draw, (PAD, y), terms, terms_font, muted, (w - 2 * PAD) // 2, 2)

    # Student signature (bottom-left)
    sig_y = h - PAD - 40
    if template.show_signature_line:
        draw.line([PAD, sig_y, PAD + 230, sig_y], fill=(255, 255, 255, 120), width=2)
        draw.text((PAD, sig_y + 8), "STUDENT SIGNATURE", font=_font("regular", 15), fill=muted)

    # Authorized signature (bottom-right, left of the QR block)
    if template.authorized_signature_name or template.show_signature_line:
        right = w - PAD - QR_SIZE - 24
        draw.line([right - 240, sig_y, right, sig_y], fill=(255, 255, 255, 120), width=2)
        sig_name = template.authorized_signature_name or "AUTHORIZED SIGNATURE"
        draw.text((right - draw.textlength(sig_name, font=_font("regular", 15)),
                   sig_y + 8), sig_name, font=_font("regular", 15), fill=muted)

    # QR (verification) — bottom-right
    qr = _qr_image(card, request)
    if qr:
        qr_box = QR_SIZE
        qr_x = w - PAD - qr_box
        qr_y = h - PAD - qr_box
        draw.rectangle([qr_x - 5, qr_y - 5, qr_x + qr_box + 5, qr_y + qr_box + 5],
                       fill=(255, 255, 255, 255))
        draw.text((qr_x + 4, qr_y - 18), "SCAN TO VERIFY",
                  font=_font("bold", 15), fill=muted)
        qr = qr.resize((qr_box, qr_box), Image.Resampling.NEAREST)
        img.paste(qr, (qr_x, qr_y))

    return img


# ─────────────────────────────────────────────────────────────────────────────
# PDF EXPORT
# ─────────────────────────────────────────────────────────────────────────────

PAGE_W_MM, PAGE_H_MM = 210.0, 297.0
MARGIN_MM = 10.0
GAP_MM = 3.0
CROP_LEN_MM = 3.0


def _grid_counts(template):
    card_w = float(template.card_width_mm or 85.60)
    card_h = float(template.card_height_mm or 53.98)
    usable_w = PAGE_W_MM - 2 * MARGIN_MM + GAP_MM
    usable_h = PAGE_H_MM - 2 * MARGIN_MM + GAP_MM
    cols = max(1, int((usable_w) // (card_w + GAP_MM)))
    rows = max(1, int((usable_h) // (card_h + GAP_MM)))
    return cols, rows, card_w, card_h


def _mirror_horizontal(img):
    return img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)


def render_card_image(card, side="front", request=None):
    if side == "front":
        return render_front(card, request)
    return render_back(card, request)


def _image_bytes(img):
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=92)
    return buffer.getvalue()


def _draw_card(pdf, img, x_mm, y_mm, w_mm, h_mm):
    from reportlab.lib.utils import ImageReader

    w_pt = w_mm * 72 / 25.4
    h_pt = h_mm * 72 / 25.4
    pdf.drawImage(
        ImageReader(io.BytesIO(_image_bytes(img))),
        x_mm * 72 / 25.4, y_mm * 72 / 25.4,
        width=w_pt, height=h_pt,
    )
    _crop_marks(pdf, x_mm, y_mm, w_mm, h_mm)


def _draw_page(pdf, images, cols, card_w_mm, card_h_mm, mirror_row=False):
    for idx, img in enumerate(images):
        r, c = divmod(idx, cols)
        if mirror_row:
            img = _mirror_horizontal(img)
            c = cols - 1 - c
        x_mm = MARGIN_MM + c * (card_w_mm + GAP_MM)
        y_mm = PAGE_H_MM - MARGIN_MM - (r + 1) * card_h_mm - r * GAP_MM
        _draw_card(pdf, img, x_mm, y_mm, card_w_mm, card_h_mm)


def _grid_counts_pair(template):
    """Grid counts for combined sheets: each cell holds a card's front + back."""
    card_w = float(template.card_width_mm or 85.60)
    card_h = float(template.card_height_mm or 54.0)
    cell_w = card_w * 2 + GAP_MM
    usable_w = PAGE_W_MM - 2 * MARGIN_MM + GAP_MM
    usable_h = PAGE_H_MM - 2 * MARGIN_MM + GAP_MM
    cols = max(1, int(usable_w // (cell_w + GAP_MM)))
    rows = max(1, int(usable_h // (card_h + GAP_MM)))
    return cols, rows, card_w, card_h, cell_w


def _draw_combined_page(pdf, pairs, cols, card_w_mm, card_h_mm, cell_w_mm):
    """Lay out (front, back) pairs side-by-side: front left, back right."""
    for idx, (front, back) in enumerate(pairs):
        r, c = divmod(idx, cols)
        x_mm = MARGIN_MM + c * (cell_w_mm + GAP_MM)
        y_mm = PAGE_H_MM - MARGIN_MM - (r + 1) * card_h_mm - r * GAP_MM
        _draw_card(pdf, front, x_mm, y_mm, card_w_mm, card_h_mm)
        _draw_card(pdf, back, x_mm + card_w_mm + GAP_MM, y_mm, card_w_mm, card_h_mm)


def _crop_marks(pdf, x_mm, y_mm, w_mm, h_mm):
    c = CROP_LEN_MM
    x = x_mm * 72 / 25.4
    y = y_mm * 72 / 25.4
    w = w_mm * 72 / 25.4
    h = h_mm * 72 / 25.4
    pdf.setStrokeColorRGB(0.55, 0.55, 0.55)
    pdf.setLineWidth(0.4)
    corners = [(x, y + h, x + c, y + h), (x, y + h, x, y + h - c),
               (x + w, y + h, x + w - c, y + h), (x + w, y + h, x + w, y + h - c),
               (x, y, x + c, y), (x, y, x, y + c),
               (x + w, y, x + w - c, y), (x + w, y, x + w, y + c)]
    for x1, y1, x2, y2 in corners:
        pdf.line(x1, y1, x2, y2)
    pdf.setStrokeColorRGB(0, 0, 0)


def pdf_for_cards(cards, request=None, mode="duplex", back_order="mirrored"):
    """
    Build a print-ready PDF for ``cards``.

    ``mode``: "front" | "back" | "duplex" | "combined"
    ``back_order``: "mirrored" (recommended; aligns backs under fronts for a
        top-edge duplex flip) or "direct".

    In "combined" mode each card's front and back are placed side-by-side
    (front left, back right) on the same sheet so that each pair is kept
    together and easily identifiable.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    cards = list(cards)
    if not cards:
        raise ValueError("No cards to print.")

    template = cards[0].template
    cols, rows, card_w_mm, card_h_mm = _grid_counts(template)
    per_page = cols * rows

    fronts = [render_card_image(c, "front", request) for c in cards]
    backs = [render_card_image(c, "back", request) for c in cards]

    output = io.BytesIO()
    pdf = canvas.Canvas(output, pagesize=A4)

    def chunked(images, size):
        return [images[i:i + size] for i in range(0, len(images), size)]

    def _emit(images, mirror_row=False):
        _draw_page(pdf, images, cols, card_w_mm, card_h_mm, mirror_row=mirror_row)
        pdf.showPage()

    if mode == "combined":
        pair_cols, pair_rows, pair_w_mm, pair_h_mm, pair_cell_w_mm = _grid_counts_pair(template)
        pairs = list(zip(fronts, backs))
        for page in chunked(pairs, pair_cols * pair_rows):
            _draw_combined_page(pdf, page, pair_cols, pair_w_mm, pair_h_mm, pair_cell_w_mm)
            pdf.showPage()
    elif mode == "front":
        for page in chunked(fronts, per_page):
            _emit(page, mirror_row=False)
    elif mode == "back":
        for page in chunked(backs, per_page):
            _emit(page, mirror_row=back_order == "mirrored")
    else:  # duplex — fronts then the mirrored backs of the same cards, so each
        # physical sheet prints with cards aligned back-to-back over the top edge.
        front_pages = chunked(fronts, per_page)
        back_pages = chunked(backs, per_page)
        for i, page in enumerate(front_pages):
            _emit(page, mirror_row=False)
            if i < len(back_pages):
                _emit(back_pages[i], mirror_row=back_order == "mirrored")

    pdf.save()
    output.seek(0)
    return output


def digital_card_bytes(card, side="front", request=None):
    """Render one side of a card and return JPEG bytes."""
    img = render_card_image(card, side, request)
    return _image_bytes(img)


def combined_card_image(card, request=None):
    """
    Front and back of a card side-by-side on one labelled image, so that
    bulk downloads keep each student's two sides together and identifiable.
    """
    front = render_front(card, request)
    back = render_back(card, request)
    scale = 620 / max(front.size)
    fw = max(1, int(round(front.width * scale)))
    fh = max(1, int(round(front.height * scale)))
    front = front.resize((fw, fh), Image.Resampling.LANCZOS)
    back = back.resize((fw, fh), Image.Resampling.LANCZOS)

    pad = 26
    gutter = 30
    label_h = 44
    cap_h = 48
    canvas = Image.new(
        "RGB",
        (fw * 2 + pad * 2 + gutter, label_h + fh + cap_h),
        (18, 20, 26),
    )
    draw = ImageDraw.Draw(canvas)
    accent = (232, 197, 71)
    text = (232, 233, 237)
    draw.text((pad, label_h - 30), "FRONT", font=_font("bold", 25), fill=accent)
    draw.text((pad + fw + gutter, label_h - 30), "BACK", font=_font("bold", 25), fill=accent)
    canvas.paste(front, (pad, label_h))
    canvas.paste(back, (pad + fw + gutter, label_h))
    draw.line([pad, label_h + fh + 2, pad + fw * 2 + gutter, label_h + fh + 2],
              fill=(42, 45, 56), width=2)
    caption = f"{card.card_number} — {card.student.get_full_name()}"
    draw.text((pad, label_h + fh + 14), caption, font=_font("bold", 22), fill=text)
    return canvas


def combined_card_bytes(card, request=None):
    return _image_bytes(combined_card_image(card, request))