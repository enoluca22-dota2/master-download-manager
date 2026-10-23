import json

from ytd.settings_store import SettingsStore


def test_defaults_applied_when_no_file(tmp_path):
    store = SettingsStore(path=str(tmp_path / "settings.json"))
    assert store.get("max_concurrent_downloads") == 3
    assert store.get("api_token")  # generated automatically


def test_settings_persist_across_instances(tmp_path):
    path = str(tmp_path / "settings.json")
    store1 = SettingsStore(path=path)
    store1.set("max_concurrent_downloads", 5)
    token = store1.get("api_token")

    store2 = SettingsStore(path=path)
    assert store2.get("max_concurrent_downloads") == 5
    assert store2.get("api_token") == token  # token is stable, not regenerated on each load


def test_unknown_keys_on_disk_are_preserved_and_merged_with_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"download_dir": str(tmp_path)}), encoding="utf-8")
    store = SettingsStore(path=str(path))
    assert store.get("download_dir") == str(tmp_path)
    assert store.get("max_retries") == 3  # filled in from defaults


def test_auto_generated_token_survives_restart_without_other_changes(tmp_path):
    # Regression test: the token used to only be written to disk as a side
    # effect of some other settings change, so a restart with no other
    # change silently minted a new token and broke every paired extension.
    path = str(tmp_path / "settings.json")
    store1 = SettingsStore(path=path)
    token = store1.get("api_token")

    store2 = SettingsStore(path=path)  # simulates an app restart, no .set() calls in between
    assert store2.get("api_token") == token


def test_regenerate_token_changes_value(tmp_path):
    store = SettingsStore(path=str(tmp_path / "settings.json"))
    old = store.get("api_token")
    new = store.regenerate_token()
    assert new != old
    assert store.get("api_token") == new


def test_corrupt_settings_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not valid json", encoding="utf-8")
    store = SettingsStore(path=str(path))
    assert store.get("max_concurrent_downloads") == 3


def test_settings_file_with_bom_is_read(tmp_path):
    # Notepad / PowerShell 5 write UTF-8 with a byte-order mark
    path = tmp_path / "settings.json"
    path.write_bytes(b"\xef\xbb\xbf" + b'{"api_port": 47931, "api_token": "keepme"}')
    store = SettingsStore(path=str(path))
    assert store.get("api_port") == 47931
    assert store.get("api_token") == "keepme"


def test_corrupt_settings_are_backed_up_before_defaults_are_saved(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"api_token": "abc", "download_dir": "D:\\x"', encoding="utf-8")  # truncated JSON
    SettingsStore(path=str(path))
    backups = list(tmp_path.glob("settings.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8").startswith('{"api_token": "abc"')
