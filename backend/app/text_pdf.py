from fpdf import FPDF

_BODY = 11
_HEADINGS = {1: 18, 2: 15, 3: 13}


def make_pdf(text: str) -> bytes:
    """A PDF of plain or Markdown-like text. Lines starting with # become headings."""
    pdf = FPDF()
    # Uncompressed, so the text stays readable inside the file for any tool that scans it.
    pdf.set_compression(False)
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        # The built-in fonts draw Latin-1 only. Anything else becomes a question mark.
        line = raw.expandtabs(4).encode("latin-1", "replace").decode("latin-1")
        stripped = line.lstrip("#")
        level = len(line) - len(stripped)
        if 1 <= level <= 6 and stripped.startswith(" "):
            pdf.set_font("Helvetica", "B", _HEADINGS.get(level, _BODY + 1))
            line = stripped.strip()
        else:
            pdf.set_font("Helvetica", "", _BODY)
        if line == "":
            pdf.ln(_BODY * 0.5)
            continue
        pdf.multi_cell(0, 6, line, new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())
