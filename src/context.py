"""
Context class to hold command-line params
"""
from pathlib import Path
from dataclasses import dataclass
from datetime import timedelta

from event.icloud_event import TimedEvent

@dataclass
class Context:
    """
    Context class holds command-line parameters
    """
    directory: Path
    username: str
    password: str
    cookie_directory: str
    ignore_regexes: list[str]
    include_regexes: list[str]
    logging_config: str
    log_path: Path
    icloud_check_period: timedelta
    icloud_refresh_period: timedelta
    debounce_period: timedelta
    max_workers: int
    timeloop: any
    jobs_disabled: TimedEvent
