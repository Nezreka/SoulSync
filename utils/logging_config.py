import logging
import sys
import re
from pathlib import Path
from datetime import datetime
from typing import Optional

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
            return super().format(record)
        except UnicodeEncodeError:
            # Strip emojis and try again for Windows compatibility
            record.getMessage = lambda: self.strip_emojis(record.msg % record.args if record.args else record.msg)
            return super().format(record)

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

def setup_logging(level: str = "INFO", log_file: Optional[str] = None) -> logging.Logger:
    log_level = getattr(logging, level.upper(), logging.INFO)
    
    logger = logging.getLogger("newmusic")
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
        
        file_handler = logging.FileHandler(log_path, encoding='utf-8')
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
    return logging.getLogger(f"newmusic.{name}")

main_logger = get_logger("main")