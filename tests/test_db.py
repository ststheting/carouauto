from carouauto.db import SeenStore


def test_first_run_seeds_silently_and_reports_no_new_ids(tmp_path):
    store = SeenStore(str(tmp_path / "test.sqlite3"))

    new_ids = store.diff_and_update("speediance", ["111", "222"])

    assert new_ids == []


def test_second_run_reports_only_genuinely_new_ids(tmp_path):
    store = SeenStore(str(tmp_path / "test.sqlite3"))
    store.diff_and_update("speediance", ["111", "222"])

    new_ids = store.diff_and_update("speediance", ["111", "222", "333"])

    assert new_ids == ["333"]


def test_searches_are_tracked_independently(tmp_path):
    store = SeenStore(str(tmp_path / "test.sqlite3"))
    store.diff_and_update("speediance", ["111"])

    new_ids = store.diff_and_update("nintendo-switch", ["111"])

    assert new_ids == []  # first run for THIS search, seeded silently


def test_state_persists_across_store_instances(tmp_path):
    db_path = str(tmp_path / "test.sqlite3")
    store_one = SeenStore(db_path)
    store_one.diff_and_update("speediance", ["111"])
    store_one.close()

    store_two = SeenStore(db_path)
    new_ids = store_two.diff_and_update("speediance", ["111", "222"])

    assert new_ids == ["222"]
