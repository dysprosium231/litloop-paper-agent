"""Re-export the publisher batch downloader from the root package (one implementation; merged 2026-10-18)."""

from ..publisher_batch import *  # noqa: F401,F403
from ..publisher_batch import DownloadResult, PaperRecord, PublisherBatchDownloader, _is_school_login_host  # noqa: F401
