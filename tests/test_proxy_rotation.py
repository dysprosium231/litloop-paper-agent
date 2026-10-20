"""Parsing the proxy pool setting (the rotation that used it went with publisher_batch, 2026-10-20)."""

import unittest

from litloop.config import parse_proxy_pool


class ParseProxyPoolTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(parse_proxy_pool(""), [])
        self.assertEqual(parse_proxy_pool(None), [])

    def test_single(self):
        self.assertEqual(parse_proxy_pool("socks5://1.2.3.4:1080"), ["socks5://1.2.3.4:1080"])

    def test_multiple_with_whitespace(self):
        result = parse_proxy_pool("  socks5://a:1080 , http://b:8080  ,socks5://c:1080 ")
        self.assertEqual(result, ["socks5://a:1080", "http://b:8080", "socks5://c:1080"])

    def test_deduplicate(self):
        result = parse_proxy_pool("socks5://a:1080,socks5://a:1080,http://b:8080")
        self.assertEqual(result, ["socks5://a:1080", "http://b:8080"])

    def test_empty_tokens_dropped(self):
        self.assertEqual(parse_proxy_pool(",,socks5://a:1080,,"), ["socks5://a:1080"])


def _tmp_run_dir():
    return Path(tempfile.mkdtemp())


if __name__ == "__main__":
    unittest.main()
