"""Paper and FetchResult: the two shapes agents see."""

from __future__ import annotations

import json

from litloop.models import FetchResult, NextAction, Paper, _content_quality


def _paper(**kwargs) -> Paper:
    base = {"doi": "10.1016/j.ceramint.2021.06.118", "title": "SiC ceramics"}
    base.update(kwargs)
    return Paper(**base)


def test_a_paper_survives_a_json_round_trip():
    paper = _paper(authors=["Li, X.", "Wang, Y."], journal="Ceramics", year=2021,
                   abstract="A", full_text="B", figures=["fig1"], references=["ref"], source="open_access")
    again = Paper.from_json(paper.to_json())
    assert again == paper
    assert Paper.from_json(paper.to_dict()) == paper


def test_unknown_keys_are_dropped_when_reading_json():
    paper = Paper.from_json({"doi": "10.1/x", "nonsense": 1, "title": "T"})
    assert paper.doi == "10.1/x" and not hasattr(paper, "nonsense")


def test_markdown_shows_the_fields_it_has_and_skips_the_rest():
    paper = _paper(authors=["Li, X."], journal="Ceramics", year=2021, abstract="摘要", figures=["f1"],
                   references=["r1"], pdf_path="C:/papers/x.pdf")
    text = paper.to_markdown(include_pdf_path=True)
    assert "# SiC ceramics" in text
    assert "**Authors:** Li, X." in text
    assert "**Year:** 2021" in text
    assert "**PDF saved to:** C:/papers/x.pdf" in text
    assert "**Figure 1:** f1" in text
    assert "- r1" in text

    plain = Paper().to_markdown()
    assert plain.startswith("# Untitled")


def test_plain_text_is_title_abstract_and_body_only():
    assert _paper(abstract="摘要", full_text="正文").to_text() == "SiC ceramics\n\n摘要\n\n正文"
    assert Paper().to_text() == ""


def test_content_quality_names_what_we_actually_got():
    assert _content_quality(_paper(full_text="x")) == "short_text"
    assert _content_quality(_paper(pdf_path="x.pdf")) == "pdf_only"
    assert _content_quality(_paper(abstract="x")) == "abstract_only"
    assert _content_quality(_paper(title="x")) == "metadata_only"
    assert _content_quality(Paper()) == "none"


def test_long_full_text_is_a_clean_success():
    result = FetchResult.from_paper(_paper(full_text="x" * 1000))
    assert (result.status, result.quality, result.next_action) == ("success", "full_text", None)


def test_a_pdf_without_text_asks_for_an_inspection():
    result = FetchResult.from_paper(_paper(pdf_path="C:/papers/x.pdf"))
    assert result.status == "blocked" and result.reason == "pdf_extraction_failed"
    assert result.next_action.kind == "inspect_pdf"
    assert "C:/papers/x.pdf" in result.next_action.message


def test_without_a_school_the_advice_is_to_configure_one():
    result = FetchResult.from_paper(Paper(doi="10.1/x"))
    assert result.status == "config_needed" and result.reason == "institution_not_configured"
    assert result.next_action.command == "litloop config set instsci_school YOUR_SCHOOL"


def test_with_a_school_the_advice_is_to_log_in():
    result = FetchResult.from_paper(Paper(doi="10.1/x"), institution_configured=True)
    assert result.status == "auth_required" and result.reason == "institution_login_required"
    assert "10.1/x" in result.next_action.command


def test_partial_text_points_at_the_right_next_step():
    without_school = FetchResult.from_paper(_paper(abstract="x", full_text="short"))
    assert without_school.status == "partial" and without_school.reason == "insufficient_full_text"
    assert without_school.next_action.kind == "configure_institution"

    with_school = FetchResult.from_paper(_paper(abstract="x"), institution_configured=True, identifier="10.9/z")
    assert with_school.next_action.kind == "login"
    assert "10.9/z" in with_school.next_action.command


def test_the_result_serializes_attempts_and_the_next_action():
    result = FetchResult.from_paper(Paper(doi="10.1/x"))
    result.attempts = [{"stage": "openalex", "status": "miss"}, {"stage": "publisher", "status": "blocked", "reason": "paywall"}]
    data = result.to_dict()
    assert data["next_action"]["kind"] == "configure_institution"
    assert data["attempts"][1]["reason"] == "paywall"
    assert json.loads(result.to_json())["status"] == "config_needed"

    markdown = result.to_markdown()
    assert "**Status:** config_needed" in markdown
    assert "- openalex: miss" in markdown
    assert "- publisher: blocked (paywall)" in markdown


def test_plain_text_for_a_blocked_result_carries_the_next_step():
    result = FetchResult(status="config_needed", quality="none", paper=Paper(title="T"),
                         reason="institution_not_configured", next_action=NextAction(kind="k", message="去设置", command="litloop x"))
    assert result.to_text() == "T\n\nNext action: 去设置\n\nCommand: litloop x"

    done = FetchResult(status="success", quality="full_text", paper=Paper(title="T", full_text="B"))
    assert done.to_text() == "T\n\nB"
