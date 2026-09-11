from carouauto.subscriptions import SubscriptionStore


def test_seed_admin_makes_a_new_chat_id_an_admin(tmp_path):
    store = SubscriptionStore(str(tmp_path / "t.sqlite3"))

    store.seed_admin(111)

    assert store.is_admin(111) is True
    assert store.is_active(111) is True


def test_seed_admin_is_idempotent(tmp_path):
    store = SubscriptionStore(str(tmp_path / "t.sqlite3"))

    store.seed_admin(111)
    store.seed_admin(111)

    assert store.is_admin(111) is True


def test_seed_admin_recovers_a_revoked_admin(tmp_path):
    store = SubscriptionStore(str(tmp_path / "t.sqlite3"))
    store.register(111)
    store.revoke(111)
    assert store.is_active(111) is False

    store.seed_admin(111)

    assert store.is_admin(111) is True
    assert store.is_active(111) is True


def test_register_new_user_succeeds_once(tmp_path):
    store = SubscriptionStore(str(tmp_path / "t.sqlite3"))

    assert store.register(222) is True
    assert store.is_active(222) is True
    assert store.is_admin(222) is False
    assert store.register(222) is False  # already registered


def test_unregistered_user_is_not_active(tmp_path):
    store = SubscriptionStore(str(tmp_path / "t.sqlite3"))

    assert store.is_active(999) is False
    assert store.is_admin(999) is False


def test_revoke_deactivates_a_user_but_allows_re_registration(tmp_path):
    store = SubscriptionStore(str(tmp_path / "t.sqlite3"))
    store.register(222)

    assert store.revoke(222) is True
    assert store.is_active(222) is False

    assert store.revoke(999) is False  # never existed

    assert store.register(222) is True  # re-registering un-revokes
    assert store.is_active(222) is True


def test_add_search_then_get_and_list(tmp_path):
    store = SubscriptionStore(str(tmp_path / "t.sqlite3"))
    store.register(222)

    search_id = store.add_search(222, "speediance", "https://example.com/s", 100.0, 500.0)

    assert search_id is not None
    found = store.get_search(222, "speediance")
    assert found.search_id == search_id
    assert found.chat_id == 222
    assert found.name == "speediance"
    assert found.url == "https://example.com/s"
    assert found.min_price == 100.0
    assert found.max_price == 500.0
    assert found.exclude_keywords is None
    assert found.condition_filter is None
    assert found.paused is False
    assert found.hide_bumped is True  # new searches hide bumped/stale listings by default
    assert store.list_searches(222) == [found]


def test_add_search_rejects_duplicate_name_for_same_user(tmp_path):
    store = SubscriptionStore(str(tmp_path / "t.sqlite3"))
    store.register(222)
    store.add_search(222, "speediance", "https://example.com/s")

    assert store.add_search(222, "speediance", "https://example.com/other") is None


def test_two_users_can_use_the_same_search_name(tmp_path):
    store = SubscriptionStore(str(tmp_path / "t.sqlite3"))
    store.register(222)
    store.register(333)

    id_a = store.add_search(222, "speediance", "https://example.com/s")
    id_b = store.add_search(333, "speediance", "https://example.com/s")

    assert id_a != id_b
    assert store.get_search(222, "speediance").search_id == id_a
    assert store.get_search(333, "speediance").search_id == id_b


def test_remove_search_deletes_it_and_returns_its_id(tmp_path):
    store = SubscriptionStore(str(tmp_path / "t.sqlite3"))
    store.register(222)
    search_id = store.add_search(222, "speediance", "https://example.com/s")

    assert store.remove_search(222, "speediance") == search_id
    assert store.get_search(222, "speediance") is None
    assert store.remove_search(222, "speediance") is None  # already gone


def test_set_price_exclude_condition_and_paused(tmp_path):
    store = SubscriptionStore(str(tmp_path / "t.sqlite3"))
    store.register(222)
    store.add_search(222, "speediance", "https://example.com/s")

    assert store.set_price_filter(222, "speediance", 50.0, 200.0) is True
    assert store.set_exclude_keywords(222, "speediance", "case,box only") is True
    assert store.set_condition_filter(222, "speediance", "Brand new") is True
    assert store.set_paused(222, "speediance", True) is True
    assert store.set_hide_bumped(222, "speediance", False) is True  # opting back into seeing them

    found = store.get_search(222, "speediance")
    assert (found.min_price, found.max_price) == (50.0, 200.0)
    assert found.exclude_keywords == "case,box only"
    assert found.condition_filter == "Brand new"
    assert found.paused is True
    assert found.hide_bumped is False

    assert store.set_price_filter(222, "does-not-exist", 1.0, 2.0) is False
    assert store.set_hide_bumped(222, "does-not-exist", True) is False


def test_existing_db_missing_hide_bumped_column_is_migrated_in_place(tmp_path):
    import sqlite3

    db_path = str(tmp_path / "old.sqlite3")
    # Simulate a DB created before hide_bumped existed.
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE users (
            chat_id INTEGER PRIMARY KEY,
            registered_at TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0,
            revoked INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE user_searches (
            search_id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL REFERENCES users(chat_id),
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            min_price REAL,
            max_price REAL,
            exclude_keywords TEXT,
            condition_filter TEXT,
            paused INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            UNIQUE(chat_id, name)
        );
        """
    )
    conn.execute(
        "INSERT INTO users (chat_id, registered_at, is_admin, revoked) VALUES (1, 'now', 1, 0)"
    )
    conn.execute(
        "INSERT INTO user_searches (chat_id, name, url, created_at) VALUES (1, 'old-search', 'https://example.com', 'now')"
    )
    conn.commit()
    conn.close()

    store = SubscriptionStore(db_path)

    found = store.get_search(1, "old-search")
    assert found is not None
    assert found.hide_bumped is True  # newly-added column takes the ADD COLUMN's own default


def test_existing_db_with_a_retired_max_age_days_column_is_migrated_in_place(tmp_path):
    import sqlite3

    db_path = str(tmp_path / "old.sqlite3")
    # Simulate a DB from right after max_age_days shipped (since retired).
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE users (
            chat_id INTEGER PRIMARY KEY,
            registered_at TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0,
            revoked INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE user_searches (
            search_id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL REFERENCES users(chat_id),
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            min_price REAL,
            max_price REAL,
            exclude_keywords TEXT,
            condition_filter TEXT,
            paused INTEGER NOT NULL DEFAULT 0,
            hide_bumped INTEGER NOT NULL DEFAULT 0,
            max_age_days REAL,
            created_at TEXT NOT NULL,
            UNIQUE(chat_id, name)
        );
        """
    )
    conn.execute(
        "INSERT INTO users (chat_id, registered_at, is_admin, revoked) VALUES (1, 'now', 1, 0)"
    )
    conn.execute(
        "INSERT INTO user_searches (chat_id, name, url, hide_bumped, max_age_days, created_at) "
        "VALUES (1, 'old-search', 'https://example.com', 1, 30.0, 'now')"
    )
    conn.commit()
    conn.close()

    store = SubscriptionStore(db_path)

    found = store.get_search(1, "old-search")
    assert found is not None
    assert found.hide_bumped is True  # pre-existing value preserved, not clobbered
    columns = {row[1] for row in store._conn.execute("PRAGMA table_info(user_searches)")}
    assert "max_age_days" not in columns


def test_list_active_subscriptions_excludes_paused_and_revoked(tmp_path):
    store = SubscriptionStore(str(tmp_path / "t.sqlite3"))
    store.register(222)
    store.register(333)
    active_id = store.add_search(222, "active", "https://example.com/a")
    store.add_search(222, "paused", "https://example.com/b")
    store.set_paused(222, "paused", True)
    store.add_search(333, "revoked-users", "https://example.com/c")
    store.revoke(333)

    subs = store.list_active_subscriptions()

    assert [s.search_id for s in subs] == [active_id]
