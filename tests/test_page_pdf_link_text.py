"""find_page_pdf_link also finds a "View PDF" button whose link has no PDF pattern."""

from __future__ import annotations

import pytest

from litloop.browser_pdf import find_page_pdf_link


@pytest.fixture(scope="module")
def page():
    from litloop import browser_backend as bb
    try:
        b = bb.launch(headless=True)
    except Exception as e:  # no browser on this machine
        pytest.skip(f"no browser: {e}")
    pg = b.new_context().new_page()
    yield pg
    b.close()


def test_view_pdf_button(page):
    page.set_content('<a href="/reader/abc">Recommended</a><a href="https://x.test/reader/S22?token=1">View PDF</a>')
    assert find_page_pdf_link(page, max_wait_s=1) == "https://x.test/reader/S22?token=1"


def test_pdfft_link_still_first(page):
    page.set_content('<a href="https://x.test/pii/S1/pdfft?md5=1">View PDF</a><a href="https://x.test/other">Download PDF</a>')
    assert find_page_pdf_link(page, max_wait_s=1) == "https://x.test/pii/S1/pdfft?md5=1"


def test_no_pdf_link(page):
    page.set_content('<a href="https://x.test/a">Abstract</a><a href="https://x.test/b">pdfs of other papers</a>')
    assert find_page_pdf_link(page, max_wait_s=1) is None
