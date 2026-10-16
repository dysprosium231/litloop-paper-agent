import pytest

from litloop.identifiers import canonical_doi


def test_canonical_doi_none_and_empty():
    assert canonical_doi(None) == ""
    assert canonical_doi("") == ""
    assert canonical_doi("   ") == ""


def test_canonical_doi_prefixes():
    # https://doi.org/
    assert canonical_doi("https://doi.org/10.1016/j.actamat.2021.116657") == "10.1016/j.actamat.2021.116657"
    # http://doi.org/
    assert canonical_doi("http://doi.org/10.1016/j.actamat.2021.116657") == "10.1016/j.actamat.2021.116657"
    # https://dx.doi.org/
    assert canonical_doi("https://dx.doi.org/10.1016/j.actamat.2021.116657") == "10.1016/j.actamat.2021.116657"
    # http://dx.doi.org/
    assert canonical_doi("http://dx.doi.org/10.1016/j.actamat.2021.116657") == "10.1016/j.actamat.2021.116657"
    # doi.org/
    assert canonical_doi("doi.org/10.1016/j.actamat.2021.116657") == "10.1016/j.actamat.2021.116657"
    # doi:
    assert canonical_doi("doi:10.1016/j.actamat.2021.116657") == "10.1016/j.actamat.2021.116657"
    assert canonical_doi("DOI: 10.1016/j.actamat.2021.116657") == "10.1016/j.actamat.2021.116657"
    # Case-insensitive prefixes
    assert canonical_doi("HTTPS://DOI.ORG/10.1016/j.actamat.2021.116657") == "10.1016/j.actamat.2021.116657"
    assert canonical_doi("HTTP://DX.DOI.ORG/10.1016/j.actamat.2021.116657") == "10.1016/j.actamat.2021.116657"
    assert canonical_doi("DOI.ORG/10.1016/j.actamat.2021.116657") == "10.1016/j.actamat.2021.116657"


def test_canonical_doi_case():
    assert canonical_doi("10.1002/ADMA.202000001") == "10.1002/adma.202000001"
    assert canonical_doi("HTTPS://DOI.ORG/10.1002/ADMA.202000001") == "10.1002/adma.202000001"


def test_canonical_doi_url_encoding():
    # URL-decoded (%2F -> /)
    assert canonical_doi("10.1000%2F182") == "10.1000/182"
    assert canonical_doi("https://doi.org/10.1000%2f182") == "10.1000/182"
    assert canonical_doi("doi:10.1000%2FABC") == "10.1000/abc"


def test_canonical_doi_whitespace():
    assert canonical_doi("   10.1016/j.actamat.2021.116657 \n\t") == "10.1016/j.actamat.2021.116657"
    assert canonical_doi("  https://doi.org/10.1016/j.actamat.2021.116657  ") == "10.1016/j.actamat.2021.116657"
