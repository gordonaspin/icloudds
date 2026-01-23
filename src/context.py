from dataclasses import dataclass
from datetime import timedelta

@dataclass
class Context:
    directory: str
    username: str
    password: str
    cookie_directory: str
    ignore_local: list[str]
    ignore_icloud: list[str]
    include_local: list[str]
    include_icloud: list[str]
    logging_config: str
    log_path: str
    retry_period: timedelta
    icloud_check_period: timedelta
    icloud_refresh_period: timedelta

