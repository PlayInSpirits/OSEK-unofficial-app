import io
import os
import re
from functools import lru_cache
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib.enums import TA_LEFT
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import BaseDocTemplate, Frame, PageBreak, PageTemplate, Paragraph


@lru_cache(maxsize=4)
def summary_pdf(title, text, warning=""):
    fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for name, filename in (("SummaryArial", "arial.ttf"), ("SummaryArialBold", "arialbd.ttf")):
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(fonts / filename)))
    pdfmetrics.registerFontFamily("SummaryArial", normal="SummaryArial", bold="SummaryArialBold")
    body = ParagraphStyle("Summary", fontName="SummaryArial", fontSize=17, leading=22,
                          spaceAfter=7, alignment=TA_LEFT)
    heading = ParagraphStyle("SummaryHeading", parent=body, fontName="SummaryArialBold",
                             fontSize=20, leading=25, keepWithNext=True)
    story = [Paragraph(escape(title), heading)]
    if warning:
        story.append(Paragraph(escape(warning), body))
    needs_break = False
    has_content = False
    for line in text.splitlines():
        line = line.strip()
        if not line or re.fullmatch(r"[-*_]{3,}", line):
            continue
        if line == "[НОВАЯ СТРАНИЦА]":
            needs_break = has_content
            continue
        if needs_break:
            story.append(PageBreak())
            needs_break = False
        is_heading = bool(re.match(r"^#{1,6}\s", line))
        line = re.sub(r"^#{1,6}\s+", "", line)
        line = re.sub(r"^[-*+]\s+", "• ", line)
        chunks = re.split(r"\*\*(.+?)\*\*", line)
        markup = "".join(f"<b>{escape(part)}</b>" if i % 2 else escape(part) for i, part in enumerate(chunks))
        story.append(Paragraph(markup, heading if is_heading else body))
        has_content = True
    output = io.BytesIO()
    margin = 0.5 * cm
    document = BaseDocTemplate(output, title=title, rightMargin=margin, leftMargin=margin,
                               topMargin=margin, bottomMargin=margin)
    frame = Frame(margin, margin, document.width, document.height,
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    document.addPageTemplates(PageTemplate(id="Summary", frames=[frame]))
    document.build(story)
    return output.getvalue()
