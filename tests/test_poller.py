import asyncio

from carouauto import poller


class _FakeChromium:
    def __init__(self):
        self.launch_kwargs = None

    async def launch_persistent_context(self, **kwargs):
        self.launch_kwargs = kwargs
        return object()


class _FakePlaywright:
    def __init__(self):
        self.chromium = _FakeChromium()


class _FakeStarter:
    def __init__(self, pw):
        self._pw = pw

    async def start(self):
        return self._pw


def _launch_kwargs(monkeypatch) -> dict:
    pw = _FakePlaywright()
    monkeypatch.setattr(poller, "async_playwright", lambda: _FakeStarter(pw))
    asyncio.run(poller.Poller("/tmp/profile", headless=True).start())
    return pw.chromium.launch_kwargs


def test_start_launches_the_persistent_profile(monkeypatch):
    kwargs = _launch_kwargs(monkeypatch)
    assert kwargs["user_data_dir"] == "/tmp/profile"
    assert kwargs["channel"] == "chrome"


def test_start_blocks_clevertap_hosts_at_dns(monkeypatch):
    # CleverTap's SDK fires ~2,300 never-answered requests per Carousell page
    # load, and the Playwright driver retains closed pages with all their
    # requests — that grew the driver heap ~85MB per round until it crashed.
    rules = [a for a in _launch_kwargs(monkeypatch)["args"] if a.startswith("--host-resolver-rules=")]
    assert len(rules) == 1
    for host in poller.BLOCKED_HOSTS:
        assert f"MAP {host} ~NOTFOUND" in rules[0]
    assert "d2r1yp2w7bby2u.cloudfront.net" in poller.BLOCKED_HOSTS
    assert "*.clevertap-prod.com" in poller.BLOCKED_HOSTS
