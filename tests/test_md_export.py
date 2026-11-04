"""Markdown export: PDF -> agent-ready .md via pymupdf4llm."""

from __future__ import annotations

from pathlib import Path

import pymupdf

from litloop.download.markdown import pdf_to_markdown


def _tiny_pdf(tmp_path: Path) -> Path:
    p = tmp_path / "sample.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Hello litloop markdown export test 2026")
    doc.save(str(p))
    doc.close()
    return p


def test_pdf_to_markdown_writes_sibling_md(tmp_path: Path):
    pdf = _tiny_pdf(tmp_path)
    out = pdf_to_markdown(pdf)
    assert Path(out).exists()
    assert Path(out).suffix == ".md"
    assert "litloop markdown export test" in Path(out).read_text(encoding="utf-8")


def test_pdf_to_markdown_text_only_mode(tmp_path: Path):
    pdf = _tiny_pdf(tmp_path)
    text = pdf_to_markdown(pdf, write=False)
    assert isinstance(text, str)
    assert "litloop markdown export test" in text


def test_clean_markdown_text_folds_sup_caret_diacritics():
    from litloop.download.markdown import clean_markdown_text

    cleaned = clean_markdown_text("A<sup>ˇ</sup>Z c<sup>ˇ</sup>")
    assert "<sup>" not in cleaned
    # NFC folds A + U+030C into the precomposed Ǎ (U+01CD)
    assert "\u01cd" in cleaned   # Ǎ
    assert "\u010d" in cleaned   # č


def test_clean_markdown_text_keeps_lone_sup_caret():
    from litloop.download.markdown import clean_markdown_text

    cleaned = clean_markdown_text("plain <sup>ˇ</sup>")
    assert "<sup>" not in cleaned
    assert "\u030c" in cleaned   # combining caron survives as a diacritic


def test_markdown_quality_scan_flags_replacement_chars():
    from litloop.download.markdown import markdown_quality_scan

    warnings = markdown_quality_scan("good text \ufffd broken")
    assert any("替换字符" in w for w in warnings)
    assert markdown_quality_scan("clean text") == []


def _fake_pymupdf4llm(monkeypatch, missing: str):
    """A pymupdf4llm whose layout path needs `missing`; text order always works."""
    import sys
    import types

    calls = {"layout": True}
    fake = types.ModuleType("pymupdf4llm")

    def to_markdown(path):
        if calls["layout"]:
            raise ModuleNotFoundError(f"No module named '{missing}'", name=missing)
        return "# text order\nbody\n"

    fake.to_markdown = to_markdown
    fake.use_layout = lambda yes: calls.__setitem__("layout", bool(yes))
    monkeypatch.setitem(sys.modules, "pymupdf4llm", fake)
    return calls


def test_a_missing_layout_component_falls_back_to_text_order(tmp_path, monkeypatch):
    from litloop.download.markdown import pdf_to_markdown_detailed

    calls = _fake_pymupdf4llm(monkeypatch, "onnxruntime")
    text, warnings = pdf_to_markdown_detailed(tmp_path / "x.pdf", write=False)
    assert text.startswith("# text order")
    assert calls["layout"] is False
    assert any("版面分析" in w for w in warnings)


def test_an_unrelated_missing_module_is_not_swallowed(tmp_path, monkeypatch):
    from litloop.download.markdown import pdf_to_markdown_detailed

    _fake_pymupdf4llm(monkeypatch, "somethingelse")
    try:
        pdf_to_markdown_detailed(tmp_path / "x.pdf", write=False)
    except ModuleNotFoundError as exc:
        assert exc.name == "somethingelse"
    else:  # pragma: no cover
        raise AssertionError("a missing unrelated module must not be treated as the layout part")
