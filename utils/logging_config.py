import logging
import logging.handlers
import sys
import re
from pathlib import Path
from datetime import datetime
from typing import Optional

LOGGER_NAMESPACE = "soulsync"

# ── secrets never reach a log line ───────────────────────────────────────────
# An HTTP error's text is the URL that failed, and for most providers the key
# rides in the query string: a TMDB 404 wrote `?api_key=<the key>` into app.log
# with its traceback (Nov 13 2026), and logs are what people paste into an
# issue. Formatters below run every finished line, traceback included, through
# `redact`. Values only; the parameter name stays so the line still reads.
_SECRET_PARAMS = (
    r"api[_-]?key|apikey|api[_-]?sig|request[_-]?sig|signature|"
    r"access[_-]?token|refresh[_-]?token|auth[_-]?token|user[_-]?auth[_-]?token|id[_-]?token|"
    r"token|x-plex-token|client[_-]?secret|secret|password|passwd|pwd|"
    r"session[_-]?key|code[_-]?verifier"
)
_SECRET_QUERY_RE = re.compile(
    r"(?i)([?&;](?:%s)=)[^&\s\"'<>#)\]]+" % _SECRET_PARAMS)
# names that are a credential on one API and an ordinary word on another
# (SoulSync's own `?key=now_playing` names a list): only a key-shaped value
_SHORT_NAME_QUERY_RE = re.compile(r"(?i)([?&;](?:key|sk|sig)=)[A-Za-z0-9._~%+/-]{16,}")
# subsonic (navidrome) carries the password or its salted hash as p / t / s
_SUBSONIC_RE = re.compile(r"(?i)([?&](?:p|t|s)=)[^&\s\"'<>#)\]]+")
# an Authorization header as text or as a logged dict, and a bare bearer token
_AUTHORIZATION_RE = re.compile(
    r"(?i)(authorization['\"]?\s*[:=]\s*['\"]?(?:(?:bearer|basic|token|mediabrowser)\s+)?)"
    r"[^\s'\",}]+")
_BEARER_RE = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{20,}")
_TOKEN_EQUALS_RE = re.compile(r"(?i)(token\"?\s*=\s*\"?)[A-Za-z0-9._~+/%-]{8,}")
# a logged dict of settings or request params: {'api_key': '...'}
_SECRET_FIELD_RE = re.compile(
    r"(?i)(['\"](?:%s)['\"]\s*:\s*['\"])[^'\"]+" % _SECRET_PARAMS)
_URL_PASSWORD_RE = re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://[^/\s:@\"']+:)[^@\s/\"']+(@)")
REDACTED = "***"


def redact(text: str) -> str:
    """``text`` with credential values replaced by ``***``."""
    if not text:
        return text
    if "=" in text:
        text = _SECRET_QUERY_RE.sub(r"\1" + REDACTED, text)
        text = _SHORT_NAME_QUERY_RE.sub(r"\1" + REDACTED, text)
        if "/rest/" in text:
            text = _SUBSONIC_RE.sub(r"\1" + REDACTED, text)
    lowered = text.lower()
    # token= first: `Authorization: MediaBrowser Token="..."` carries it there
    if "token" in lowered:
        text = _TOKEN_EQUALS_RE.sub(r"\1" + REDACTED, text)
    if "authorization" in lowered:
        text = _AUTHORIZATION_RE.sub(r"\1" + REDACTED, text)
    if "bearer " in lowered:
        text = _BEARER_RE.sub(r"\1" + REDACTED, text)
    if "':" in text or '":' in text:
        text = _SECRET_FIELD_RE.sub(r"\1" + REDACTED, text)
    if "://" in text and "@" in text:
        text = _URL_PASSWORD_RE.sub(r"\1" + REDACTED + r"\2", text)
    return text


class SafeFormatter(logging.Formatter):
    """Formatter that handles Unicode characters safely on Windows"""
    
    @staticmethod
    def strip_emojis(text):
        """Remove emoji characters from text for Windows compatibility"""
        # Remove emoji characters but keep other Unicode
        emoji_pattern = re.compile("["
            u"\U0001F600-\U0001F64F"  # emoticons
            u"\U0001F300-\U0001F5FF"  # symbols & pictographs
            u"\U0001F680-\U0001F6FF"  # transport & map symbols
            u"\U0001F1E0-\U0001F1FF"  # flags (iOS)
            u"\U00002702-\U000027B0"
            u"\U000024C2-\U0001F251"
            "]+", flags=re.UNICODE)
        return emoji_pattern.sub('', text)
    
    def format(self, record):
        # Try to format with emojis first, fall back to stripped version
        try:
            line = super().format(record)
        except UnicodeEncodeError:
            # Strip emojis and try again for Windows compatibility
            record.getMessage = lambda: self.strip_emojis(record.msg % record.args if record.args else record.msg)
            line = super().format(record)
        try:
            return redact(line)
        except Exception:  # noqa: BLE001 - a log line is never lost to its own cleanup
            return line

class ColoredFormatter(SafeFormatter):
    COLORS = {
        'DEBUG': '\033[94m',
        'INFO': '\033[92m',
        'WARNING': '\033[93m',
        'ERROR': '\033[91m',
        'CRITICAL': '\033[95m',
        'RESET': '\033[0m'
    }
    
    def format(self, record):
        log_color = self.COLORS.get(record.levelname, self.COLORS['RESET'])
        reset_color = self.COLORS['RESET']
        
        record.levelname = f"{log_color}{record.levelname}{reset_color}"
        return super().format(record)

def app_log_path(configured: Optional[str] = None) -> str:
    """Where app.log goes: SOULSYNC_LOG_FILE when set, else the configured path.

    the test suite sets it. without it every test that imported web_server
    logged into the real logs/app.log, and from WSL it rotated the file the
    windows server had open, so the live log filled with test errors
    ("no such table: metadata") nobody could tell from real ones."""
    import os
    return os.environ.get("SOULSYNC_LOG_FILE") or configured or "logs/app.log"


def setup_logging(level: str = "INFO", log_file: Optional[str] = None) -> logging.Logger:
    log_level = getattr(logging, level.upper(), logging.INFO)
    
    logger = logging.getLogger(LOGGER_NAMESPACE)
    logger.setLevel(log_level)
    
    if logger.handlers:
        logger.handlers.clear()
    
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    # Force UTF-8 encoding for Windows compatibility with Unicode characters
    if hasattr(console_handler.stream, 'reconfigure'):
        console_handler.stream.reconfigure(encoding='utf-8', errors='replace')
    
    console_formatter = ColoredFormatter(
        fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)
    
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        
        file_handler = logging.handlers.RotatingFileHandler(
            log_path, encoding='utf-8', maxBytes=10*1024*1024, backupCount=3
        )
        file_handler.setLevel(log_level)
        
        file_formatter = SafeFormatter(
            fmt='%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)
    
    logger.info(f"Logging initialized with level: {level}")
    return logger

def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAMESPACE}.{name}")

def set_log_level(level: str) -> bool:
    """Dynamically change the log level for all loggers without restart"""
    try:
        log_level = getattr(logging, level.upper(), logging.INFO)

        # Get the root "soulsync" logger
        root_logger = logging.getLogger(LOGGER_NAMESPACE)
        root_logger.setLevel(log_level)

        # Update all handlers
        for handler in root_logger.handlers:
            handler.setLevel(log_level)

        root_logger.info(f"Log level changed to: {level.upper()}")
        return True
    except Exception as e:
        logging.getLogger(LOGGER_NAMESPACE).error(f"Error setting log level: {e}")
        return False

def get_current_log_level() -> str:
    """Get the current log level"""
    root_logger = logging.getLogger(LOGGER_NAMESPACE)
    return logging.getLevelName(root_logger.level)

main_logger = get_logger("main")
