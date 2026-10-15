"""Render authored fictional agreement packets without reading application data or model outputs."""

import json
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    BaseDocTemplate,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parents[1]
STYLES = getSampleStyleSheet()
STYLES.add(
    ParagraphStyle(
        name="PacketTitle",
        fontName="Helvetica-Bold",
        fontSize=22,
        leading=26,
        textColor=colors.HexColor("#173a46"),
        spaceAfter=16,
    )
)
STYLES.add(
    ParagraphStyle(
        name="Section",
        fontName="Helvetica-Bold",
        fontSize=15,
        leading=19,
        spaceBefore=12,
        spaceAfter=10,
        textColor=colors.HexColor("#173a46"),
    )
)
STYLES.add(
    ParagraphStyle(
        name="Clause",
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=15,
        spaceBefore=11,
        spaceAfter=7,
    )
)
STYLES.add(
    ParagraphStyle(
        name="Copy",
        fontName="Helvetica",
        fontSize=10.5,
        leading=15,
        spaceAfter=9,
        alignment=TA_LEFT,
    )
)
STYLES.add(
    ParagraphStyle(
        name="Label",
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=11,
        spaceAfter=10,
        textColor=colors.HexColor("#466870"),
    )
)


def draw_page(canvas: Canvas, doc: BaseDocTemplate) -> None:
    """Stamp only synthetic metadata and visible provenance onto every page."""
    canvas.setAuthor("LegalDoc Extraction")
    canvas.setSubject(
        "Entirely fictional PDF fixture for a document extraction demonstration"
    )
    canvas.setCreator("LegalDoc Extraction synthetic fixture generator")
    canvas.setStrokeColor(colors.HexColor("#b8cccf"))
    canvas.line(48, 43, A4[0] - 48, 43)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#466870"))
    canvas.drawString(
        48, 29, "SYNTHETIC DEMONSTRATION  /  No real agreement, parties, or obligations"
    )
    canvas.drawRightString(A4[0] - 48, 29, f"{doc.page}")


def render(source: Path) -> Path:
    """Honor authored page breaks so the fixture always exercises a split parent clause."""
    packet = json.loads(source.read_text())
    output = ROOT / "examples" / "pdfs" / f"{source.stem}.pdf"
    output.parent.mkdir(parents=True, exist_ok=True)
    story = []
    for page_index, page in enumerate(packet["pages"]):
        if page_index:
            story.append(PageBreak())
        for block in page:
            if block["kind"] == "table":
                rows = [
                    [Paragraph(escape(cell), STYLES["Copy"]) for cell in row]
                    for row in block["rows"]
                ]
                table = Table(rows, colWidths=[120, 155, 224], repeatRows=1)
                table.setStyle(
                    TableStyle(
                        [
                            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e7eff0")),
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#b8cccf")),
                            ("LEFTPADDING", (0, 0), (-1, -1), 8),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                            ("TOPPADDING", (0, 0), (-1, -1), 8),
                        ]
                    )
                )
                story.extend([table, Spacer(1, 10)])
            else:
                story.append(Paragraph(escape(block["text"]), STYLES[block["kind"]]))
    document = SimpleDocTemplate(
        str(output),
        pagesize=A4,
        rightMargin=48,
        leftMargin=48,
        topMargin=40,
        bottomMargin=60,
        title=packet["title"],
        invariant=1,
    )
    document.build(story, onFirstPage=draw_page, onLaterPages=draw_page)
    return output


def main() -> None:
    """Regenerate only the public PDFs from versioned, human-readable fictional sources."""
    for source in sorted((ROOT / "examples" / "sources").glob("*.json")):
        print(render(source).relative_to(ROOT))


if __name__ == "__main__":
    main()
