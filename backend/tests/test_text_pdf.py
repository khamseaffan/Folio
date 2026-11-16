from app.text_pdf import make_pdf


def test_the_bytes_are_a_pdf_and_hold_the_text():
    data = make_pdf("# Weekly notes\n\nFirst line of the body.\n- a bullet")

    assert data.startswith(b"%PDF")
    assert data.rstrip().endswith(b"%%EOF")
    assert b"First line of the body." in data
    assert b"Weekly notes" in data


def test_a_long_text_runs_onto_more_pages():
    short = make_pdf("one line")
    long = make_pdf("\n".join(f"line {number}" for number in range(300)))

    assert long.count(b"/Type /Page\n") > short.count(b"/Type /Page\n")


def test_characters_the_font_cannot_draw_do_not_fail():
    data = make_pdf("Price 5 € and 你好 and café")

    assert data.startswith(b"%PDF")
    assert "café".encode("latin-1") in data


def test_an_empty_text_is_still_a_valid_pdf():
    assert make_pdf("").startswith(b"%PDF")
