"""Constants"""
from enum import Enum, auto
# For retrying connection after timeouts and errors

AUTHENTICATION_MAX_RETRIES: int = 3
MAX_RETRIES: int = 3
DOWNLOAD_MEDIA_CHUNK_SIZE: int = 1024*1024
ICLOUD_CHECK_SECONDS: int = 20
DEBOUNCE_SECONDS: int = 10
ICLOUD_REFRESH_SECONDS: int = 90
UPLOAD_WORKERS: int = 1
DOWNLOAD_WORKERS: int = 32

class ExitCode(Enum):
    """
    ExitCode definitions
    """
    EXIT_NORMAL: int = 0
    EXIT_FAILED_ALREADY_RUNNING: int = auto()
    EXIT_FAILED_CLICK_EXCEPTION: int = auto()
    EXIT_FAILED_CLICK_USAGE: int = auto()
    EXIT_FAILED_NOT_A_DIRECTORY: int = auto()
    EXIT_FAILED_MISSING_COMMAND: int = auto()
    EXIT_FAILED_LOGIN: int = auto()
    EXIT_FAILED_CLOUD_API: int = auto()
    EXIT_FAILED_2FA_REQUIRED: int = auto()
    EXIT_FAILED_SEND_2SA_CODE: int = auto()
    EXIT_FAILED_VERIFY_2SA_CODE: int = auto()
    EXIT_FAILED_VERIFY_2FA_CODE: int = auto()
