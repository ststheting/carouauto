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


def test_start_blocks_service_workers(monkeypatch):
    # Carousell's service worker lives in the persistent profile and serves
    # page requests; Playwright's driver keeps that worker's network traffic
    # at the context level, so it survives page.close() and grew the driver's
    # heap ~85MB per polling round until it crashed every ~2 hours.
    pw = _FakePlaywright()
    monkeypatch.setattr(poller, "async_playwright", lambda: _FakeStarter(pw))

    asyncio.run(poller.Poller("/tmp/profile", headless=True).start())

    assert pw.chromium.launch_kwargs["service_workers"] == "block"
    assert pw.chromium.launch_kwargs["user_data_dir"] == "/tmp/profile"
