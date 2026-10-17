"""The school's routes run one after another, no-login first; phase 1 opens every publisher's site."""

from pathlib import Path

from litloop import sources


def test_order_follows_the_settings_and_skips_routes_switched_off():
    cfg = {"vpnsci_enabled": True, "carsi_enabled": True, "carsi_idp_name": "北京航空航天大学"}
    assert [label for _, label in sources._build_institutional_sources("10.1/x", cfg)] == [
        "WebVPN-HTTP", "WebVPN", "CARSI"]
    cfg["institutional_order"] = ["carsi", "webvpn_http"]
    assert [label for _, label in sources._build_institutional_sources("10.1/x", cfg)] == ["CARSI", "WebVPN-HTTP"]
    assert sources._build_institutional_sources("10.1/x", {}) == []


def test_routes_are_tried_in_turn_and_the_first_pdf_ends_it(tmp_path: Path, monkeypatch):
    tried = []

    def route(name, ok):
        def fn(doi, out, cfg):
            tried.append(name)
            if ok:
                out.write_bytes(b"%PDF-1.4 " + b"x" * 20000)
                return {"success": True, "file": str(out), "source": name}
            return None
        return fn
    monkeypatch.setattr(sources, "_try_source", lambda fn, doi, out, cfg, label, **kw: fn(doi, out, cfg))
    steps = [(route("a", False), "WebVPN-HTTP"), (route("b", True), "WebVPN"), (route("c", True), "CARSI")]
    out = tmp_path / "paper.pdf"
    r = sources._run_institutional_sequence(steps, "10.1/x", tmp_path, out, {})
    assert tried == ["a", "b"]                       # CARSI never opened
    assert r["source"] == "b" and Path(r["file"]) == out and out.exists()


def test_a_publisher_without_a_browser_lane_gets_the_generic_one():
    labels = {label for _, label in sources._build_free_sources("10.1073/pnas.123", {"scihub_enabled": False})}
    assert "GenericBrowser" in labels                                     # PNAS: subscription, no lane
    labels = {label for _, label in sources._build_free_sources("10.1016/j.x.2020.1", {"scihub_enabled": False})}
    assert "GenericBrowser" not in labels and "ElsevierBrowser" in labels
    labels = {label for _, label in sources._build_free_sources("10.3390/x1", {"scihub_enabled": False})}
    assert "GenericBrowser" not in labels                                 # MDPI is open


def test_scihub_is_not_a_browser_lane_for_the_late_wait():
    assert sources._is_browser_lane("ElsevierBrowser") and sources._is_browser_lane("CARSI")
    assert not sources._is_browser_lane("Sci-Hub") and not sources._is_browser_lane("Unpaywall")


def test_elsevier_api_full_text_can_be_off_or_learned_off(tmp_path, monkeypatch):
    from litloop import config
    from litloop.sources import elsevier_api
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    cfg = {"elsevier_api_key": "k", "scihub_enabled": False}
    lanes = lambda c: {label for _, label in sources._build_free_sources("10.1016/j.x.2020.1", c)}  # noqa: E731
    assert elsevier_api.fulltext_enabled(cfg) and "ElsevierAPI" in lanes(cfg)
    assert "ElsevierAPI" not in lanes({**cfg, "elsevier_api_fulltext": False})   # the setting
    elsevier_api.mark_no_fulltext("k")                                          # the API said 403
    assert not elsevier_api.fulltext_enabled(cfg) and "ElsevierAPI" not in lanes(cfg)
    assert elsevier_api.fulltext_enabled({**cfg, "elsevier_api_key": "another key"})
    assert elsevier_api.fetch_pdf("10.1016/j.x.2020.1", "k", config=cfg) is None  # never asked
    assert "k" not in (tmp_path / "elsevier_no_fulltext.json").read_text(encoding="utf-8").split('"')
