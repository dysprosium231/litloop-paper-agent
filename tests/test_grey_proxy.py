"""Grey proxy routing for Sci-Hub and LibGen, browser PAC config, and Taylor & Francis book URLs."""

import base64
import litloop.browser.backend as browser_backend
import litloop.network
import litloop.sources.instsci


def test_select_proxy_for_url_grey_routing(monkeypatch):
    monkeypatch.setattr(litloop.network, "_proxy_reachable", lambda url: True)
    config = {
        "scihub_via_proxy": True,
        "host_proxy_url": "http://127.0.0.1:10808",
        "scihub_domains": ["https://sci-hub.ru", "https://sci-hub.se"],
    }
    assert litloop.network.select_proxy_for_url("https://sci-hub.ru/10.1234/test", config) == "http://127.0.0.1:10808"
    assert litloop.network.select_proxy_for_url("https://sci-hub.se/10.1234/test", config) == "http://127.0.0.1:10808"
    assert litloop.network.select_proxy_for_url("https://libgen.li/item", config) == "http://127.0.0.1:10808"
    assert litloop.network.select_proxy_for_url("https://www.sciencedirect.com/", config) is None

    cfg_no_scihub = dict(config, scihub_via_proxy=False)
    assert litloop.network.select_proxy_for_url("https://sci-hub.ru/10.1234/test", cfg_no_scihub) is None

    monkeypatch.setattr(litloop.network, "_proxy_reachable", lambda url: False)
    assert litloop.network.select_proxy_for_url("https://sci-hub.ru/10.1234/test", config) is None


def test_grey_hosts():
    config = {
        "scihub_via_proxy": True,
        "host_proxy_url": "http://127.0.0.1:10808",
        "scihub_domains": ["https://sci-hub.ru", "https://sci-hub.se"],
    }
    hosts = litloop.network.grey_hosts(config)
    assert "sci-hub.ru" in hosts
    assert "libgen.li" in hosts
    assert "bban.top" in hosts


def test_browser_backend_direct_args_pac(monkeypatch):
    monkeypatch.setattr(litloop.network, "_proxy_reachable", lambda url: True)
    config = {
        "scihub_via_proxy": True,
        "host_proxy_url": "http://127.0.0.1:10808",
        "scihub_domains": ["https://sci-hub.ru", "https://sci-hub.se"],
    }
    args = browser_backend._direct_args(
        browser_backend.BACKEND_PATCHRIGHT, None, None, config
    )
    prefix = "--proxy-pac-url=data:application/x-ns-proxy-autoconfig;base64,"
    assert isinstance(args, list)
    assert len(args) == 1
    assert args[0].startswith(prefix)
    decoded = base64.b64decode(args[0][len(prefix):]).decode("utf-8")
    assert "sci-hub.ru" in decoded
    assert "127.0.0.1:10808" in decoded

    empty_args = browser_backend._direct_args(
        browser_backend.BACKEND_PATCHRIGHT, None, None, {}
    )
    assert "--no-proxy-server" in empty_args


def test_taylorfrancis_book_pdf_url():
    url1 = (
        "https://d.buaa.edu.cn/https/77726476706e69737468656265737421e7e056d23331715c711a8fbe995b203cb81e8525f7"
        "/books/edit/10.1201/9781315273242/ceramic"
    )
    assert (
        litloop.sources.instsci._taylorfrancis_book_pdf_url("10.1201/9781315273242", url1)
        == "https://api.taylorfrancis.com/content/books/edit/download?identifierName=doi&identifierValue=10.1201/9781315273242&type=googlepdf"
    )

    url2 = "https://www.taylorfrancis.com/books/mono/10.1201/x/y"
    assert (
        litloop.sources.instsci._taylorfrancis_book_pdf_url("10.1201/x", url2)
        == "https://api.taylorfrancis.com/content/books/mono/download?identifierName=doi&identifierValue=10.1201/x&type=googlepdf"
    )

    url3 = "https://www.taylorfrancis.com/books/9781000477986/chapters/10.1201/9781351109550-8"
    assert litloop.sources.instsci._taylorfrancis_book_pdf_url("10.1201/9781351109550-8", url3) is None


def test_a_taylor_francis_chapter_is_fetched_as_its_book():
    from litloop.sources.instsci import _taylorfrancis_chapter_book
    landing = ("https://d.buaa.edu.cn/https/77726476706e69737468656265737421e7e056d23331715c711a8fbe995b203cb81e8525f7"
               "/chapters/mono/10.1201/9781351109550-8/ceramic-materials-arthur-lyons")
    assert _taylorfrancis_chapter_book("10.1201/9781351109550-8", landing) == ("10.1201/9781351109550", "mono")
    assert _taylorfrancis_chapter_book("10.1201/9781351109550", landing) is None      # not a chapter DOI
    assert _taylorfrancis_chapter_book("10.1201/9781351109550-8", "https://example.org/x") is None
