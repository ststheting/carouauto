from carouauto.db import SeenStore


def test_first_run_seeds_silently_and_reports_no_new_ids(tmp_path):
    store = SeenStore(str(tmp_path / "test.sqlite3"))

    new_ids = store.diff_and_update(1, ["111", "222"])

    assert new_ids == []


def test_second_run_reports_only_genuinely_new_ids(tmp_path):
    store = SeenStore(str(tmp_path / "test.sqlite3"))
    store.diff_and_update(1, ["111", "222"])

    new_ids = store.diff_and_update(1, ["111", "222", "333"])

    assert new_ids == ["333"]


def test_searches_are_tracked_independently(tmp_path):
    store = SeenStore(str(tmp_path / "test.sqlite3"))
    store.diff_and_update(1, ["111"])

    new_ids = store.diff_and_update(2, ["111"])

    assert new_ids == []  # first run for THIS search_id, seeded silently


def test_state_persists_across_store_instances(tmp_path):
    db_path = str(tmp_path / "test.sqlite3")
    store_one = SeenStore(db_path)
    store_one.diff_and_update(1, ["111"])
    store_one.close()

    store_two = SeenStore(db_path)
    new_ids = store_two.diff_and_update(1, ["111", "222"])

    assert new_ids == ["222"]


def test_get_new_ids_returns_nothing_on_first_run(tmp_path):
    store = SeenStore(str(tmp_path / "test.sqlite3"))

    assert store.get_new_ids(1, ["111", "222"]) == []


def test_get_new_ids_does_not_mutate_state(tmp_path):
    store = SeenStore(str(tmp_path / "test.sqlite3"))
    store.mark_seen(1, ["111"])

    first = store.get_new_ids(1, ["111", "222", "333"])
    second = store.get_new_ids(1, ["111", "222", "333"])

    assert first == ["222", "333"]
    assert second == first


def test_mark_seen_persists_so_ids_are_no_longer_new(tmp_path):
    store = SeenStore(str(tmp_path / "test.sqlite3"))
    store.mark_seen(1, ["111"])
    assert store.get_new_ids(1, ["111", "222"]) == ["222"]

    store.mark_seen(1, ["111", "222"])

    assert store.get_new_ids(1, ["111", "222"]) == []


def test_mark_seen_ends_the_first_run_for_a_search(tmp_path):
    store = SeenStore(str(tmp_path / "test.sqlite3"))

    store.mark_seen(1, ["111"])

    assert store.get_new_ids(2, ["111"]) == []


def test_delete_all_for_search_removes_its_history(tmp_path):
    store = SeenStore(str(tmp_path / "test.sqlite3"))
    store.mark_seen(1, ["111", "222"])
    store.mark_seen(2, ["111"])

    store.delete_all_for_search(1)

    # search_id 1's history is gone, so its old ids look new again.
    assert store.get_new_ids(1, ["111"]) == []  # first run again, seeded silently
    # search_id 2 is untouched.
    assert store.get_new_ids(2, ["111", "333"]) == ["333"]
