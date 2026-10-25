"""Source hygiene: copies of the text rules outside litloop.terms.text are reported."""

from litloop._source_hygiene import RULE_OWN_TEXT_RULE, check_source_hygiene, own_text_rule


def test_copies_of_text_rules_are_recognised():
    assert own_text_rule('key = re.sub(r"[^a-z0-9]+", "", title.lower())').startswith("title key")
    assert own_text_rule('re.sub(r"[\\-‐-―]", " ", s)').startswith("dash set")
    assert own_text_rule('doi = doi.replace("\\u2013", "-")').startswith("dash set")
    assert own_text_rule('_ELEMENT = re.compile(r"^[A-Z][a-z]?$")').startswith("element guess")
    assert own_text_rule('_STOP = set("the and of in for with by on to a an".split())').startswith("stop-word")
    assert own_text_rule('msg = "the file could not be read"') == ""
    assert own_text_rule("x = a - b") == ""


def test_only_terms_text_may_hold_them(tmp_path):
    rule = 'STOP = set("the and of in for with by on to a an".split())\n'
    (tmp_path / "terms").mkdir()
    (tmp_path / "terms" / "text.py").write_text(rule, encoding="utf-8")
    (tmp_path / "search.py").write_text(rule, encoding="utf-8")
    found = [p for p in check_source_hygiene(tmp_path) if p.rule == RULE_OWN_TEXT_RULE]
    assert [p.file for p in found] == ["search.py"]
