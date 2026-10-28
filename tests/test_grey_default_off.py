"""Grey sources (Sci-Hub, SciBban, LibGen) stay off unless the user turned them on (CHARTER)."""

from types import SimpleNamespace

from typer.testing import CliRunner

from litloop import sources
from litloop.sources import scihub


def _names(config):
    return {name for _, name in sources._build_free_sources("10.1/x", config)}


def test_a_config_without_the_switch_has_no_grey_source(monkeypatch):
    monkeypatch.setattr(scihub, "too_new", lambda doi, config: False)
    monkeypatch.setattr(scihub, "recently_absent", lambda doi: False)
    grey = {"Sci-Hub", "SciBban", "LibGen"}
    assert not grey & _names({})  # used to count a missing key as on
    assert not grey & _names({"download_strategy": "scihub_only"})
    assert not grey & _names({"scihub_enabled": False, "download_strategy": "grey_only"})
    assert grey <= _names({"scihub_enabled": True})


def test_a_grey_strategy_alone_does_not_turn_sci_hub_on_in_batch(tmp_path, monkeypatch):
    from litloop.cli import main as cli_main

    calls = []
    monkeypatch.setattr("litloop.config.load_config",
                        lambda: {"download_strategy": "scihub_first", "scihub_enabled": False})
    monkeypatch.setattr(sources, "batch_download", lambda *a, **k: calls.append(k) or [])

    class Fetcher:
        def __init__(self, config):
            pass

        def fetch_with_result(self, doi):
            return SimpleNamespace(to_dict=lambda: {"doi": doi, "success": False})

    monkeypatch.setattr("litloop.download.fetcher.PaperFetcher", Fetcher)
    monkeypatch.setattr("litloop.institutional.config_adapter.ConfigAdapter.load", staticmethod(lambda: {}))
    listing = tmp_path / "dois.txt"
    listing.write_text("10.1016/j.ceramint.2024.08.002\n", encoding="utf-8")
    r = CliRunner().invoke(cli_main.app, ["batch", str(listing), "--no-lanes", "--output", str(tmp_path)])
    assert "No DOIs" not in r.output  # the command reached the engine choice
    assert not any(k.get("scihub_enabled") for k in calls)  # the user's "off" wins over the strategy
    monkeypatch.setattr("litloop.config.load_config",
                        lambda: {"download_strategy": "scihub_first", "scihub_enabled": True})
    CliRunner().invoke(cli_main.app, ["batch", str(listing), "--no-lanes", "--output", str(tmp_path)])
    assert calls and calls[-1].get("scihub_enabled")  # allowed and asked for: the racing engine runs
