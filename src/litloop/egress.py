"""Whether publishers accept this computer's exit as the school's, learned while downloading.

Elsevier's API answers a full-text request from an exit outside the
institution's registered IP ranges with 403 "Requestor configuration settings
insufficient". Publishers register the same ranges, so after that answer
opening a publisher's site directly in a browser cannot get a subscription
PDF either (and Wiley-style sites stop the automated browser at a Cloudflare
check). Those papers go to the school's route (WebVPN / CARSI / EZProxy).
The answer is kept for an hour: the network can change (VPN on, campus Wi-Fi).
"""

from __future__ import annotations

import time

_REFUSED_AT = 0.0
TTL_SECONDS = 3600


def mark_refused() -> None:
    """A publisher said this exit is not the institution's."""
    global _REFUSED_AT
    _REFUSED_AT = time.time()


def refused() -> bool:
    return time.time() - _REFUSED_AT < TTL_SECONDS


def reset() -> None:
    global _REFUSED_AT
    _REFUSED_AT = 0.0
