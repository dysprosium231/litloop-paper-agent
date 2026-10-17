"""Deep search: rough estimate of the 高 papers still unread, from the yield curve."""

from litloop import deep_search


def _state(highs, size=30, unread=600):
    served, rated, n = [], {}, 0
    for h in highs:
        batch = []
        for i in range(size):
            k = f"p{n}"
            n += 1
            batch.append(k)
            rated[k] = "高" if i < h else "低"
        served.append(batch)
    pool = {k: {} for b in served for k in b} | {f"u{i}": {} for i in range(unread)}
    return {"served": served, "pool": pool, "seeds": [], "batch_size": size, "cap": 200}, rated


def test_falling_yield_gives_next_batch_and_total():
    est = deep_search._remaining_estimate(*_state([12, 8, 5, 3]))
    assert est["decay"] < 0.95 and 0 < est["next_batch"] < 3
    assert 1 < est["remaining"] < 10  # a geometric tail of a fast-falling curve


def test_flat_or_rising_yield_gives_no_total():
    est = deep_search._remaining_estimate(*_state([3, 4, 4, 6]))
    assert "remaining" not in est and est["next_batch"] > 0


def test_needs_three_rated_batches():
    assert deep_search._remaining_estimate(*_state([10, 5])) is None


def test_half_rated_batches_do_not_count():
    st, rated = _state([12, 8, 5, 3])
    for k in st["served"][-1][:20]:
        rated.pop(k)
    assert deep_search._remaining_estimate(st, rated)["batches"] == 3


def test_stop_reason_mentions_the_estimate():
    st, rated = _state([12, 9, 7, 5, 0])
    stop = deep_search._stop(st, rated)
    assert stop["stop_suggested"] and "粗略外推" in stop["stop_reason"]
    assert stop["remaining_estimate"]["remaining"] >= 0
