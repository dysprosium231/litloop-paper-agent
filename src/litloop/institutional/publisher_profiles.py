"""Re-export the publisher profiles from the root package (one implementation; merged 2026-10-18)."""

from ..publisher_profiles import *  # noqa: F401,F403
from ..publisher_profiles import _PROFILE_ALIASES, PublisherProfile, get_publisher_profile, list_publisher_profiles  # noqa: F401
