import math
import os
import re
from urllib.parse import parse_qs, unquote, urlparse

from pyrogram import filters
from pyrogram.errors import FloodWait

from bot import DEFAULT_AUTH_MODE, LOGGER, SUDO_USERS
from bot.helpers.gdrive_utils.credentials_manager import credential_manager
from bot.helpers.sql_helper import gDriveDB


def _is_authorized_user(_, __, message) -> bool:
    user = getattr(message, "from_user", None)
    user_id = getattr(user, "id", None)
    if user_id is None:
        return False
    # SUDO 用户始终允许通过高权限过滤器，具体权限和授权状态在各自命令内再做细粒度检查。
    if user_id in SUDO_USERS:
        return True
    try:
        if gDriveDB.is_authorized(user_id):
            return True
    except Exception as exc:
        LOGGER.error("Authorization lookup failed for user %s: %s", user_id, exc)
    if DEFAULT_AUTH_MODE == "service_account" and credential_manager.service_account_available():
        return True
    return False


class CustomFilters:
    auth_users = filters.create(_is_authorized_user)


def get_floodwait_seconds(exc: FloodWait) -> int:
    value = getattr(exc, "value", None)
    if value is None:
        value = getattr(exc, "x", 0)
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def format_bytes(size: int) -> str:
    if size is None:
        return ""
    size = float(size)
    if size <= 0:
        return "0 B"
    power = min(int(math.log(size, 1024)), 5)
    normalized = size / math.pow(1024, power)
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    return f"{normalized:.2f} {units[power]}"


def render_progress_bar(current: int, total: int, width: int = 14) -> str:
    if total and total > 0:
        filled = min(width, int(width * (current / total)))
    else:
        filled = 0
    empty = width - filled
    return f"[{'█' * filled}{'░' * empty}]"


def format_seconds(seconds: float) -> str:
    total_seconds = max(int(seconds), 0)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_elapsed_eta(elapsed: float, current: int, total: int) -> tuple[str, str]:
    elapsed_text = format_seconds(elapsed)
    if not total or total <= 0 or not current or elapsed <= 0:
        return elapsed_text, "--:--"
    remaining = max(total - current, 0)
    speed = current / elapsed if elapsed else 0
    if speed <= 0:
        return elapsed_text, "--:--"
    eta = remaining / speed
    return elapsed_text, format_seconds(eta)


def format_speed(speed: float) -> str:
    if speed <= 0:
        return "0 B/s"
    return f"{format_bytes(speed)}/s"


def extract_filename_from_url(url: str, default: str = "file") -> str:
    """
    Extract a safe filename from a URL.
    
    This function extracts a filename from either the URL path or query parameters,
    and sanitizes it to prevent path traversal attacks.
    
    Args:
        url: The URL to extract the filename from
        default: Default filename if extraction fails
        
    Returns:
        A sanitized filename safe for use in file operations
    """
    if not url:
        return default
    parsed = urlparse(url)
    candidates = []
    if parsed.path:
        candidates.append(os.path.basename(parsed.path))
    query = parse_qs(parsed.query)
    for key in ("filename", "file", "name"):
        if key in query and query[key]:
            candidates.append(query[key][-1])
    for candidate in candidates:
        decoded = unquote(candidate).strip()
        if decoded and not decoded.endswith('/'):
            # Remove control characters
            sanitized = re.sub(r"[\n\r]", "", decoded)
            # Sanitize to prevent path traversal
            sanitized = _sanitize_filename(sanitized)
            if sanitized:
                return sanitized
    return default


def _sanitize_filename(filename: str) -> str:
    """
    Sanitize a filename to prevent path traversal attacks.
    
    This function removes or replaces dangerous characters and patterns that could
    be used to escape the intended download directory, including:
    - Path separators (/ and \)
    - Parent directory references (..)
    - Absolute path indicators
    - Control characters
    
    Args:
        filename: The filename to sanitize
        
    Returns:
        A safe filename, or empty string if the input is invalid
    """
    if not filename:
        return ""
    
    # Reject absolute paths (Unix and Windows)
    if filename.startswith('/') or (len(filename) > 1 and filename[1] == ':'):
        # Extract just the basename for absolute paths
        filename = os.path.basename(filename)
    
    # Remove any remaining path separators and parent directory references
    # This handles cases like "../../file", "..\\file", etc.
    filename = filename.replace('..', '')
    filename = filename.replace('/', '_')
    filename = filename.replace('\\', '_')
    
    # Remove any null bytes and other control characters
    filename = filename.replace('\x00', '')
    filename = re.sub(r'[\x00-\x1f\x7f]', '', filename)
    
    # Strip leading/trailing whitespace and dots (which can be problematic on some filesystems)
    filename = filename.strip('. \t')
    
    # Ensure the filename is not empty after sanitization
    if not filename or filename in ('.', '..'):
        return ""
    
    return filename


def humanbytes(size: int) -> str:
    if not size:
        return ""
    return format_bytes(size)
