import io

import pytest
from app.file_reader import PART_CHARS, ReadRefused, open_stored
from app.storage import FakeStorage
from openpyxl import Workbook
from PIL import Image as PilImage

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _stored(data: bytes) -> tuple[FakeStorage, str]:
    storage = FakeStorage()
    storage.put("accounts/a/files/f/x", data)
    return storage, "accounts/a/files/f/x"


def _pdf(pages: list[str]) -> bytes:
    # A minimal hand-written PDF, one text line per page.
    objects: list[bytes] = []
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(len(pages)))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for index, text in enumerate(pages):
        content = f"BT /F1 12 Tf 72 700 Td ({text}) Tj ET".encode()
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {5 + 2 * index} 0 R "
            f"/Resources << /Font << /F1 3 0 R >> >> >>".encode()
        )
        objects.append(
            b"<< /Length "
            + str(len(content)).encode()
            + b" >>\nstream\n"
            + content
            + b"\nendstream"
        )
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    )
    return out.getvalue()


def _png(size: tuple[int, int] = (8, 8)) -> bytes:
    out = io.BytesIO()
    PilImage.new("RGB", size, "red").save(out, format="PNG")
    return out.getvalue()


def test_part_size_is_about_forty_thousand() -> None:
    assert PART_CHARS == 40_000


@pytest.mark.parametrize("kind", ["text/plain", "text/markdown", "text/csv"])
def test_text_types_are_read_as_text(kind: str) -> None:
    storage, path = _stored(b"a,b\n1,2\n")
    opened = open_stored(storage, path, kind)
    assert (opened.text, opened.part, opened.parts, opened.images) == ("a,b\n1,2\n", 1, 1, ())


def test_text_with_a_byte_order_mark_and_bad_bytes_still_reads() -> None:
    storage, path = _stored(b"\xef\xbb\xbfhello \xff there")
    opened = open_stored(storage, path, "text/plain")
    assert opened.text.startswith("hello ")
    assert opened.text.endswith(" there")


def test_a_long_text_is_read_in_constant_parts() -> None:
    body = "x" * (PART_CHARS * 2 + 5)
    storage, path = _stored(body.encode())
    first = open_stored(storage, path, "text/plain", 1)
    second = open_stored(storage, path, "text/plain", 2)
    third = open_stored(storage, path, "text/plain", 3)
    assert (first.parts, second.parts, third.parts) == (3, 3, 3)
    assert [len(first.text), len(second.text), len(third.text)] == [PART_CHARS, PART_CHARS, 5]
    assert first.text + second.text + third.text == body


def test_a_part_past_the_end_is_refused_with_the_count() -> None:
    storage, path = _stored(b"short")
    with pytest.raises(ReadRefused) as caught:
        open_stored(storage, path, "text/plain", 2)
    assert "1 part" in str(caught.value)
    with pytest.raises(ReadRefused):
        open_stored(storage, path, "text/plain", 0)


def test_xlsx_is_read_as_cells_per_sheet() -> None:
    book = Workbook()
    first = book.active
    first.title = "Budget"
    first.append(["item", "cost"])
    first.append(["tea", 3.5])
    second = book.create_sheet("Empty")
    second.append([None, None])
    out = io.BytesIO()
    book.save(out)
    storage, path = _stored(out.getvalue())
    opened = open_stored(storage, path, XLSX)
    assert "Sheet: Budget" in opened.text
    assert "item\tcost" in opened.text
    assert "tea\t3.5" in opened.text
    assert "Sheet: Empty" in opened.text


def test_pdf_is_read_as_text_per_page() -> None:
    storage, path = _stored(_pdf(["First page words", "Second page words"]))
    opened = open_stored(storage, path, "application/pdf")
    assert "Page 1" in opened.text and "First page words" in opened.text
    assert "Page 2" in opened.text and "Second page words" in opened.text
    assert opened.text.index("First") < opened.text.index("Second")


def test_a_pdf_without_text_says_so() -> None:
    storage, path = _stored(_pdf([""]))
    opened = open_stored(storage, path, "application/pdf")
    assert opened.note is not None and "no text" in opened.note.lower()


def test_a_broken_file_is_refused_in_plain_words() -> None:
    storage, path = _stored(b"not a pdf at all")
    with pytest.raises(ReadRefused) as caught:
        open_stored(storage, path, "application/pdf")
    assert "could not be opened" in str(caught.value)


def test_missing_bytes_are_refused() -> None:
    with pytest.raises(ReadRefused):
        open_stored(FakeStorage(), "accounts/a/files/f/gone", "text/plain")


@pytest.mark.parametrize("kind", ["image/png", "image/jpeg", "image/webp"])
def test_an_image_comes_back_as_an_image(kind: str) -> None:
    out = io.BytesIO()
    PilImage.new("RGB", (8, 8), "blue").save(out, format=kind.split("/")[1].upper())
    storage, path = _stored(out.getvalue())
    opened = open_stored(storage, path, kind)
    assert opened.text == ""
    assert (opened.part, opened.parts) == (1, 1)
    assert len(opened.images) == 1
    assert opened.images[0].media_type == kind
    assert opened.images[0].data == out.getvalue()


def test_heic_is_converted_to_jpeg() -> None:
    import pillow_heif

    pillow_heif.register_heif_opener()
    out = io.BytesIO()
    PilImage.new("RGB", (16, 16), "green").save(out, format="HEIF")
    storage, path = _stored(out.getvalue())
    opened = open_stored(storage, path, "image/heic")
    image = opened.images[0]
    assert image.media_type == "image/jpeg"
    assert image.data[:3] == b"\xff\xd8\xff"
    assert PilImage.open(io.BytesIO(image.data)).size == (16, 16)


def test_a_very_large_image_is_scaled_down() -> None:
    storage, path = _stored(_png((5000, 100)))
    opened = open_stored(storage, path, "image/png")
    image = PilImage.open(io.BytesIO(opened.images[0].data))
    assert max(image.size) == 2048


def test_a_broken_image_is_refused() -> None:
    storage, path = _stored(b"nope")
    with pytest.raises(ReadRefused):
        open_stored(storage, path, "image/heic")
