"""Which links are worth fetching as a PDF."""

from litloop.pdf_utils import is_plausible_pdf_url


def test_taylor_francis_book_download_is_a_pdf_link():
    assert is_plausible_pdf_url("https://api.taylorfrancis.com/content/books/mono/download"
                                "?identifierName=doi&identifierValue=10.1201/9781003075837&type=googlepdf")


def test_a_plain_download_page_is_not():
    assert not is_plausible_pdf_url("https://example.org/download?id=3")
    assert not is_plausible_pdf_url("https://example.org/journals/abc/download?type=pdf")
