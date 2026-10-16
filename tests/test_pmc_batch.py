"""PMC's PDF pages are used for single downloads only (PMC forbids other bulk retrieval)."""

from litloop.sources import _build_free_sources


def _labels(config):
    return [label for _, label in _build_free_sources("10.1093/nar/gks1195", config)]


def test_pmc_is_tried_for_a_single_download():
    assert "PMC" in _labels({"scihub_enabled": False})


def test_pmc_is_skipped_in_a_batch():
    labels = _labels({"scihub_enabled": False, "_batch": True})
    assert "PMC" not in labels
    assert "EuropePMC" in labels
