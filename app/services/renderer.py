"""PDF rendering with ReportLab (canvas API: text is drawn, never parsed as markup)."""

import io
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

ASSETS = Path(__file__).resolve().parent.parent / "assets"
_FONT_DIRS = [ASSETS / "fonts", Path("/usr/share/fonts/truetype/dejavu")]
_fonts: tuple[str, str] | None = None


@dataclass(frozen=True)
class CertificateData:
    recipient_name: str
    course: str
    grade: str | None
    event_name: str
    issuer_name: str
    issued_on: date
    verification_code: str
    verify_url: str


class CertificateRenderer(Protocol):
    def render(self, data: CertificateData) -> bytes: ...


def _load_fonts() -> tuple[str, str]:
    """Prefer DejaVu (full Unicode). Falls back to built-in Helvetica (Latin only)."""
    global _fonts
    if _fonts:
        return _fonts
    for directory in _FONT_DIRS:
        regular, bold = directory / "DejaVuSans.ttf", directory / "DejaVuSans-Bold.ttf"
        if regular.is_file() and bold.is_file():
            pdfmetrics.registerFont(TTFont("Cert", str(regular)))
            pdfmetrics.registerFont(TTFont("Cert-Bold", str(bold)))
            _fonts = ("Cert", "Cert-Bold")
            return _fonts
    _fonts = ("Helvetica", "Helvetica-Bold")
    return _fonts


def _fit(text: str, font: str, size: float, max_width: float, min_size: float = 12) -> float:
    while size > min_size and pdfmetrics.stringWidth(text, font, size) > max_width:
        size -= 1
    return size


def _shorten(text: str, font: str, size: float, max_width: float) -> str:
    if pdfmetrics.stringWidth(text, font, size) <= max_width:
        return text
    while text and pdfmetrics.stringWidth(text + "…", font, size) > max_width:
        text = text[:-1]
    return text + "…"


class ReportLabRenderer:
    PAGE = landscape(A4)

    def render(self, data: CertificateData) -> bytes:
        regular, bold = _load_fonts()
        width, height = self.PAGE
        cx = width / 2
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=self.PAGE, invariant=1)  # invariant: reproducible bytes
        c.setTitle(f"Certificate - {data.recipient_name}"[:120])
        c.setAuthor(data.issuer_name[:120])

        # Border
        c.setStrokeColor(HexColor("#1f3a5f"))
        c.setLineWidth(6)
        c.rect(24, 24, width - 48, height - 48)
        c.setStrokeColor(HexColor("#c9a227"))
        c.setLineWidth(1.5)
        c.rect(36, 36, width - 72, height - 72)

        max_w = width - 160
        c.setFillColor(HexColor("#1f3a5f"))
        c.setFont(bold, 38)
        c.drawCentredString(cx, height - 120, "Certificate of Completion")

        c.setFillColor(HexColor("#444444"))
        c.setFont(regular, 15)
        c.drawCentredString(cx, height - 170, "This certificate is proudly presented to")

        name_size = _fit(data.recipient_name, bold, 40, max_w, 14)
        name = _shorten(data.recipient_name, bold, name_size, max_w)
        c.setFillColor(HexColor("#000000"))
        c.setFont(bold, name_size)
        c.drawCentredString(cx, height - 235, name)
        c.setStrokeColor(HexColor("#c9a227"))
        c.setLineWidth(1)
        c.line(cx - 220, height - 248, cx + 220, height - 248)

        c.setFillColor(HexColor("#444444"))
        c.setFont(regular, 15)
        c.drawCentredString(cx, height - 285, "for successfully completing")

        course = _shorten(data.course, bold, _fit(data.course, bold, 26, max_w, 14), max_w)
        c.setFillColor(HexColor("#1f3a5f"))
        c.setFont(bold, _fit(data.course, bold, 26, max_w, 14))
        c.drawCentredString(cx, height - 320, course)

        event = _shorten(data.event_name, regular, 16, max_w)
        c.setFillColor(HexColor("#444444"))
        c.setFont(regular, 16)
        c.drawCentredString(cx, height - 350, event)

        if data.grade:
            c.setFont(regular, 14)
            c.drawCentredString(cx, height - 378, f"Grade: {data.grade}")

        # Signature block
        c.setStrokeColor(HexColor("#888888"))
        c.line(110, 120, 310, 120)
        c.setFont(regular, 12)
        c.drawCentredString(210, 102, _shorten(data.issuer_name, regular, 12, 220))
        c.setFont(regular, 10)
        c.drawCentredString(210, 88, "Issued by")

        c.setFont(regular, 12)
        c.drawCentredString(cx + 40, 120, data.issued_on.strftime("%d %B %Y"))
        c.line(cx - 60, 114, cx + 140, 114)
        c.setFont(regular, 10)
        c.drawCentredString(cx + 40, 100, "Date of issue")

        # Verification footer + QR
        c.setFillColor(HexColor("#666666"))
        c.setFont(regular, 8)
        c.drawString(60, 58, f"Verification code: {data.verification_code}")
        c.drawString(60, 47, _shorten(data.verify_url, regular, 8, 460))

        size = 70
        widget = qr.QrCodeWidget(data.verify_url)
        x0, y0, x1, y1 = widget.getBounds()
        drawing = Drawing(size, size, transform=[size / (x1 - x0), 0, 0, size / (y1 - y0), 0, 0])
        drawing.add(widget)
        renderPDF.draw(drawing, c, width - 60 - size, 56)

        c.showPage()
        c.save()
        return buf.getvalue()


def get_renderer() -> CertificateRenderer:
    return ReportLabRenderer()
