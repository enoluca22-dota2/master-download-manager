"""Errors, log redaction, filename/path safety, data migration."""

import logging
import os

import pytest
from yt_dlp.utils import DownloadError

from ytd import config
from ytd.errors import explain_error, is_transient_error
from ytd.files import is_executable_file, is_within, sanitize_filename, unique_path, validate_save_dir
from ytd.logging_setup import RedactingFilter, redact, register_secret


@pytest.mark.parametrize("message,reason_part,hint_part", [
    ("ERROR: [youtube] x: Requested format is not available", "requested format is unavailable", "Best available"),
    ("ERROR: Sign in to confirm you're not a bot", "not a bot", "cookies"),
    ("ERROR: Sign in to confirm your age", "age-restricted", "cookies"),
    ("ERROR: Private video. Sign in if you've been granted access", "private", "cookies"),
    ("ERROR: The uploader has not made this video available in your country", "region", "proxy"),
    ("ERROR: HTTP Error 403: Forbidden", "403", "fresh link"),
    ("ERROR: HTTP Error 429: Too Many Requests", "rate-limiting", "Wait"),
    ("ERROR: This video is DRM protected", "DRM", "does not bypass"),
    ("ERROR: Postprocessing: ffmpeg exited with code 1", "FFmpeg", "FFmpeg location"),
    ("x: 404 Client Error: Not Found for url: https://h/f.iso", "404", "fresh one"),
    ("x: 503 Server Error: Service Unavailable for url: https://h/f", "temporary", "retries"),
])
def test_explain_error_gives_reason_and_action(message, reason_part, hint_part):
    info = explain_error(DownloadError(message))
    assert reason_part.lower() in info.reason.lower()
    assert hint_part.lower() in info.suggestion.lower()
    assert message.split(":", 1)[1].strip()[:20] in info.technical
    text = info.as_text()
    assert text.startswith("Download failed") and "Reason:" in text and "Suggested action:" in text


def test_technical_details_are_redacted():
    info = explain_error(DownloadError("ERROR: fetching https://x/video?token=SECRET123&sig=abc failed"))
    assert "SECRET123" not in info.technical and "abc failed" not in info.technical.split("sig=")[1][:5] + "x"
    assert "[REDACTED]" in info.technical


def test_transient_classification():
    assert is_transient_error(explain_error(OSError("Connection reset by peer")).reason)
    assert is_transient_error(explain_error(DownloadError("HTTP Error 503: Service Unavailable")).reason)
    assert not is_transient_error(explain_error(DownloadError("Private video")).reason)


@pytest.mark.parametrize("raw,leak", [
    ("GET https://cdn/x.mp4?expire=1&signature=abcdef123&token=tok999", ["abcdef123", "tok999"]),
    ("Cookie: SID=topsecret; HSID=alsosecret", ["topsecret", "alsosecret"]),
    ("Authorization: Bearer eyJhbGciOi", ["eyJhbGciOi"]),
    ("proxy http://user:hunter2@10.0.0.1:8080 failed", ["hunter2"]),
    ("password=letmein retrying", ["letmein"]),
    ("X-MDM-Token: abcdefabcdefabcdef", ["abcdefabcdefabcdef"]),
])
def test_redaction_removes_secret_values_but_keeps_context(raw, leak):
    cleaned = redact(raw)
    for secret in leak:
        assert secret not in cleaned
    assert "[REDACTED]" in cleaned
    assert len(cleaned) > 10  # the line is kept, not dropped


def test_registered_secret_is_always_redacted():
    register_secret("my-api-token-0123456789")
    assert "my-api-token-0123456789" not in redact("token is my-api-token-0123456789 ok")


def test_logging_filter_rewrites_records():
    record = logging.LogRecord("ytd", logging.INFO, __file__, 1, "url %s", ("https://a/b?sig=SECRET",), None)
    RedactingFilter().filter(record)
    assert "SECRET" not in record.getMessage()


@pytest.mark.parametrize("name,expected", [
    ("CON", "_CON"), ("nul.txt", "_nul.txt"), ("COM1.mp4", "_COM1.mp4"), ("console.txt", "console.txt"),
    ("a/b\\c:d*e?f\"g<h>i|j", "abcdefghij"), ("   ...   ", "download"), ("trailing dot.", "trailing dot"),
])
def test_sanitize_filename_cases(name, expected):
    assert sanitize_filename(name) == expected


def test_sanitize_filename_keeps_extension_when_truncating():
    result = sanitize_filename("x" * 400 + ".iso", max_len=100)
    assert len(result) == 100 and result.endswith(".iso")


def test_unique_path_and_is_within(tmp_path):
    (tmp_path / "a.zip").write_text("1")
    assert unique_path(str(tmp_path), "a.zip").endswith("a (1).zip")
    assert is_within(str(tmp_path), str(tmp_path / "sub" / "f"))
    assert not is_within(str(tmp_path / "sub"), str(tmp_path / "sub" / ".." / ".." / "x"))


@pytest.mark.parametrize("path", ["relative\\dir", "", "\\\\server\\share\\x", "C:\\a\\..\\..\\Windows", "C:\\x\x00y"])
def test_validate_save_dir_rejects(path):
    with pytest.raises(ValueError):
        validate_save_dir(path)


def test_validate_save_dir_accepts_absolute(tmp_path):
    assert validate_save_dir(str(tmp_path)) == os.path.normpath(str(tmp_path))


def test_executable_detection():
    assert is_executable_file("setup.EXE") and is_executable_file("run.bat") and is_executable_file("x.msi")
    assert not is_executable_file("movie.mp4") and not is_executable_file("archive.zip")


def test_legacy_settings_are_migrated_once(tmp_path, monkeypatch):
    legacy = tmp_path / "old"
    legacy.mkdir()
    (legacy / "settings.json").write_text('{"api_token": "keepme"}')
    (legacy / "download_history.json").write_text("[]")
    monkeypatch.setattr(config, "_legacy_data_dirs", lambda: iter([str(legacy)]))
    data = tmp_path / "new"
    assert sorted(config.migrate_legacy_data(str(data))) == ["download_history.json", "settings.json"]
    assert (data / "settings.json").read_text() == '{"api_token": "keepme"}'
    (legacy / "settings.json").write_text('{"api_token": "other"}')
    assert config.migrate_legacy_data(str(data)) == []  # never overwrites
    assert (legacy / "settings.json").exists()  # originals untouched
