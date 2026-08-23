import os
import pathlib
import sys
import types

import pytest

# Ensure required environment variables for importing bot package
os.environ.setdefault("BOT_TOKEN", "TEST_BOT_TOKEN")
os.environ.setdefault("APP_ID", "12345")
os.environ.setdefault("API_HASH", "TEST_API_HASH")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("SUDO_USERS", "1")
os.environ.setdefault("SUPPORT_CHAT_LINK", "https://example.com/support")
os.environ.setdefault("G_DRIVE_CLIENT_ID", "dummy_client_id")
os.environ.setdefault("G_DRIVE_CLIENT_SECRET", "dummy_client_secret")
os.environ.setdefault("TOKEN_ENCRYPTION_KEY", "x" * 32)

# Provide lightweight stubs for heavy optional dependencies required during import
if "bot.helpers.gdrive_utils.credentials_manager" not in sys.modules:
    dummy_gdrive_utils = types.ModuleType("bot.helpers.gdrive_utils")
    sys.modules.setdefault("bot.helpers.gdrive_utils", dummy_gdrive_utils)

    credential_manager_module = types.ModuleType("bot.helpers.gdrive_utils.credentials_manager")

    class _DummyCredentialManager:
        def service_account_available(self) -> bool:  # pragma: no cover - trivial
            return False

    credential_manager_module.credential_manager = _DummyCredentialManager()
    sys.modules["bot.helpers.gdrive_utils.credentials_manager"] = credential_manager_module

if "bot.helpers.sql_helper" not in sys.modules:
    sql_helper_module = types.ModuleType("bot.helpers.sql_helper")

    class _DummyDriveDB:
        @staticmethod
        def is_authorized(user_id: int) -> bool:  # pragma: no cover - trivial
            return False

    sql_helper_module.gDriveDB = _DummyDriveDB()
    sys.modules["bot.helpers.sql_helper"] = sql_helper_module

# Ensure the project root is on sys.path for module imports
PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bot.helpers.utils import (  # noqa: E402  pylint: disable=wrong-import-position
    extract_filename_from_url,
    format_bytes,
    format_elapsed_eta,
    format_seconds,
    format_speed,
    humanbytes,
    render_progress_bar,
)


@pytest.mark.parametrize(
    "size, expected",
    [
        (None, ""),
        (0, "0 B"),
        (-1024, "0 B"),
        (1, "1.00 B"),
        (1024, "1.00 KB"),
        (1024 ** 2 * 5, "5.00 MB"),
    ],
)
def test_format_bytes(size, expected):
    assert format_bytes(size) == expected


@pytest.mark.parametrize(
    "current, total, width, expected",
    [
        (0, 0, 10, "[░░░░░░░░░░]"),
        (50, 100, 10, "[█████░░░░░]"),
        (150, 100, 10, "[██████████]"),
    ],
)
def test_render_progress_bar(current, total, width, expected):
    assert render_progress_bar(current, total, width) == expected


@pytest.mark.parametrize(
    "seconds, expected",
    [
        (0, "00:00"),
        (59, "00:59"),
        (60, "01:00"),
        (3600, "01:00:00"),
        (3661, "01:01:01"),
        (-5, "00:00"),
    ],
)
def test_format_seconds(seconds, expected):
    assert format_seconds(seconds) == expected


@pytest.mark.parametrize(
    "elapsed, current, total, expected",
    [
        (0, 0, 0, ("00:00", "--:--")),
        (10, 50, 100, ("00:10", "00:10")),
        (5, 0, 100, ("00:05", "--:--")),
        (5, 50, 0, ("00:05", "--:--")),
        (5, 50, 50, ("00:05", "00:00")),
    ],
)
def test_format_elapsed_eta(elapsed, current, total, expected):
    assert format_elapsed_eta(elapsed, current, total) == expected


@pytest.mark.parametrize(
    "speed, expected",
    [
        (0, "0 B/s"),
        (-10, "0 B/s"),
        (1024, "1.00 KB/s"),
    ],
)
def test_format_speed(speed, expected):
    assert format_speed(speed) == expected


@pytest.mark.parametrize(
    "url, default, expected",
    [
        ("", "file", "file"),
        ("https://example.com/path/to/data.txt", "file", "data.txt"),
        ("https://example.com/download?filename=report.pdf", "file", "download"),
        (
            "https://example.com/download?file=archive.zip&name=ignored",
            "file",
            "download",
        ),
        ("https://example.com/path/%E4%BE%8B%E5%AD%90.txt", "file", "例子.txt"),
        ("https://example.com/path/bad%0Aname.txt", "file", "badname.txt"),
        # Path traversal attack attempts - should be sanitized
        ("https://example.com?filename=../../../etc/passwd", "file", "etcpasswd"),
        ("https://example.com?filename=..%2F..%2Ftmp%2Fowned", "file", "tmpowned"),
        ("https://example.com?filename=/etc/passwd", "file", "passwd"),
        ("https://example.com?filename=..\\..\\windows\\system32\\config", "file", "windows_system32_config"),
        ("https://example.com?filename=....//....//etc/passwd", "file", "etcpasswd"),
        # Absolute paths should extract basename only
        ("https://example.com?filename=/tmp/malicious.txt", "file", "malicious.txt"),
        ("https://example.com?filename=C:\\Windows\\malicious.exe", "file", "malicious.exe"),
        # Empty or invalid filenames after sanitization
        ("https://example.com?filename=..", "file", "file"),
        ("https://example.com?filename=.", "file", "file"),
        ("https://example.com?filename=...", "file", "file"),
        # Control characters and null bytes
        ("https://example.com?filename=test%00.txt", "file", "test.txt"),
        ("https://example.com?filename=test%01%02%03.txt", "file", "test.txt"),
    ],
)
def test_extract_filename_from_url(url, default, expected):
    assert extract_filename_from_url(url, default) == expected


@pytest.mark.parametrize(
    "size, expected",
    [
        (0, ""),
        (None, ""),
        (1024, "1.00 KB"),
    ],
)
def test_humanbytes(size, expected):
    assert humanbytes(size) == expected
