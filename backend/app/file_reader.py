import io
from dataclasses import dataclass

from app.model import Image
from app.storage import Storage

# About ten thousand tokens of text. Every part but the last has exactly this many characters.
PART_CHARS = 40_000
# Larger pictures are scaled down, so one image cannot exceed what a provider accepts.
MAX_EDGE = 2048

NOT_OPENED = "That file could not be opened. It may be damaged or locked with a password."
NO_BYTES = "The bytes of that file are missing."
NO_TEXT = "This PDF has no text in it. It may be scanned pages, which cannot be read as text."

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_TEXT = {"text/plain", "text/markdown", "text/csv"}
_IMAGES = {"image/jpeg", "image/png", "image/webp", "image/heic"}
_PIL_FORMATS = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}


class ReadRefused(Exception):
    """A sentence for the model: why the file was not opened."""

    def __init__(self, message: str, code: str = "unreadable") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Opened:
    text: str
    part: int
    parts: int
    images: tuple[Image, ...] = ()
    note: str | None = None


def open_stored(storage: Storage, path: str, content_type: str, part: int = 1) -> Opened:
    if part < 1:
        raise ReadRefused("A part number starts at 1.", "invalid")
    try:
        data = storage.read(path)
    except FileNotFoundError:
        raise ReadRefused(NO_BYTES) from None
    try:
        if content_type in _IMAGES:
            if part != 1:
                raise ReadRefused("An image has one part.", "invalid")
            return Opened("", 1, 1, (_image(data, content_type),))
        note: str | None = None
        if content_type in _TEXT:
            text = data.decode("utf-8-sig", errors="replace")
        elif content_type == _XLSX:
            text = _spreadsheet(data)
        elif content_type == "application/pdf":
            text = _pdf(data)
            if text == "":
                note = NO_TEXT
        else:
            raise ReadRefused("That file type cannot be read.")
    except ReadRefused:
        raise
    except Exception:
        raise ReadRefused(NOT_OPENED) from None
    parts = max(1, -(-len(text) // PART_CHARS))
    if part > parts:
        noun = "part" if parts == 1 else "parts"
        raise ReadRefused(f"That file has {parts} {noun}.", "invalid")
    return Opened(text[(part - 1) * PART_CHARS : part * PART_CHARS], part, parts, note=note)


def _spreadsheet(data: bytes) -> str:
    from openpyxl import load_workbook

    book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        sheets: list[str] = []
        for sheet in book.worksheets:
            lines = [f"Sheet: {sheet.title}"]
            for row in sheet.iter_rows(values_only=True):
                cells = ["" if cell is None else str(cell) for cell in row]
                if any(cells):
                    lines.append("\t".join(cells).rstrip("\t"))
            sheets.append("\n".join(lines))
        return "\n\n".join(sheets)
    finally:
        book.close()


def _pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted and not reader.decrypt(""):
        raise ReadRefused(NOT_OPENED)
    pages = [(page.extract_text() or "").strip() for page in reader.pages]
    if not any(pages):
        return ""
    return "\n\n".join(f"Page {number}\n{text}" for number, text in enumerate(pages, start=1))


def _image(data: bytes, content_type: str) -> Image:
    import pillow_heif
    from PIL import Image as PilImage
    from PIL import ImageOps

    pillow_heif.register_heif_opener()
    picture = PilImage.open(io.BytesIO(data))
    picture.load()
    heic = content_type == "image/heic"
    if not heic and max(picture.size) <= MAX_EDGE:
        return Image(content_type, data)
    picture = ImageOps.exif_transpose(picture)
    picture.thumbnail((MAX_EDGE, MAX_EDGE))
    out = io.BytesIO()
    if heic:
        # Providers do not accept HEIC, so it goes out as JPEG.
        picture.convert("RGB").save(out, format="JPEG", quality=90)
        return Image("image/jpeg", out.getvalue())
    format_name = _PIL_FORMATS[content_type]
    if format_name == "JPEG":
        picture = picture.convert("RGB")
    picture.save(out, format=format_name)
    return Image(content_type, out.getvalue())
