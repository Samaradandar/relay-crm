# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Proposal PDF rendering: WeasyPrint (A4, company branding) when it is installed, else a plain
text-as-PDF fallback so the pipeline still runs in an environment without native WeasyPrint
dependencies (Pango/Cairo). Chosen at call time, like the email backend."""

from typing import Protocol


class PdfRenderer(Protocol):
    def render(self, html: str) -> bytes: ...


class WeasyPrintRenderer:
    def render(self, html: str) -> bytes:
        from weasyprint import HTML  # heavy native deps; imported lazily

        return bytes(HTML(string=html).write_pdf())


class PlainTextFallbackRenderer:
    """No WeasyPrint available: write a minimal, valid single-page PDF with the HTML stripped
    to text. Good enough to store and attach; not styled."""

    def render(self, html: str) -> bytes:
        import re

        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        lines = [text[i : i + 90] for i in range(0, len(text), 90)] or [""]
        stream = "BT /F1 11 Tf 50 740 Td 14 TL\n"
        for line in lines[:50]:
            safe = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
            stream += f"({safe}) Tj T*\n"
        stream += "ET"
        objects = [
            "<< /Type /Catalog /Pages 2 0 R >>",
            "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> "
            "/MediaBox [0 0 595 842] /Contents 5 0 R >>",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        ]
        parts = ["%PDF-1.4\n"]
        offsets = [0]
        body = parts[0]
        for i, obj in enumerate(objects, start=1):
            offsets.append(len(body))
            body += f"{i} 0 obj\n{obj}\nendobj\n"
        xref_offset = len(body)
        body += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
        for off in offsets[1:]:
            body += f"{off:010d} 00000 n \n"
        body += (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF"
        )
        return body.encode("latin-1", errors="replace")


def get_renderer() -> PdfRenderer:
    try:
        import weasyprint  # noqa: F401

        return WeasyPrintRenderer()
    except ImportError:
        return PlainTextFallbackRenderer()


_override: PdfRenderer | None = None


def set_renderer(renderer: PdfRenderer | None) -> None:
    global _override
    _override = renderer


def render_pdf(html: str) -> bytes:
    return (_override or get_renderer()).render(html)
