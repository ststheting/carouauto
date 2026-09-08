import logging

from carouauto.main import configure_logging


def test_configure_logging_suppresses_httpx_and_httpcore_info_logs():
    # httpx logs every request's full URL at INFO by default, which embeds
    # the Telegram bot token — regression test for that leak.
    logging.getLogger("httpx").setLevel(logging.NOTSET)
    logging.getLogger("httpcore").setLevel(logging.NOTSET)

    configure_logging()

    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING
