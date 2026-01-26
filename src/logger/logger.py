import os
import atexit
import datetime as dt
import json
import logging
import logging.config
import pathlib
import sys
import threading
import traceback
from typing import override
from context import Context
import constants as constants

def setup_logging(logging_config: str) -> str:
    config_file = pathlib.Path(logging_config)
    try:
        with open(config_file) as f_in:
            config = json.load(f_in)
    except FileNotFoundError as e:
        print(f"Logging config file {config_file} not found")
        sys.exit(constants.ExitCode.EXIT_CLICK_USAGE.value)

    for _, handler in config['handlers'].items():
        file = handler.get('filename', None)
        if file:
            folder_path = os.path.normpath(os.path.dirname(file))
            os.makedirs(folder_path, exist_ok=True)
        pass    

    logging.config.dictConfig(config)
    queue_handler = logging.getHandlerByName("queue_handler")
    if queue_handler is not None:
        queue_handler.listener.start()
        atexit.register(queue_handler.listener.stop)

    sys.excepthook = handle_unhandled_exception
    threading.excepthook = handle_thread_exception
    logging.getLogger().info("logging configured")

    return folder_path

def handle_unhandled_exception(exc_type, exc_value, exc_traceback):
    """
    Handler for unhandled exceptions that will write to the logs.
    """
    # Check if it's a KeyboardInterrupt and call the default hook if it is
    # This allows the program to exit normally with Ctrl+C
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return

    # Log the exception with the traceback
    # Using logger.exception() is a shortcut that automatically adds exc_info
    logger = logging.getLogger("unhandled")
    func = None
    if logger:
        func = logger.critical
    else:
        func = print
    func("**** Unhandled exception occurred ****", exc_info=(exc_type, exc_value, exc_traceback))

def handle_thread_exception(args):
    """
    Custom exception hook to handle uncaught exceptions in threads.
    """
    logger = logging.getLogger("unhandled")
    func = None
    if logger:
        func = logger.critical
    else:
        func = print

    func(f"**** Exception caught in thread: {args.thread.name} ****")
    func(f"Exception type: {args.exc_type.__name__}")
    func(f"Exception value: {args.exc_value}")
    func(traceback.format_exc())

LOG_RECORD_BUILTIN_ATTRS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
    "taskName",
}


class MyJSONFormatter(logging.Formatter):
    def __init__(
        self,
        *,
        fmt_keys: dict[str, str] | None = None,
    ):
        super().__init__()
        self.fmt_keys = fmt_keys if fmt_keys is not None else {}

    @override
    def format(self, record: logging.LogRecord) -> str:
        message = self._prepare_log_dict(record)
        return json.dumps(message, default=str)

    def _prepare_log_dict(self, record: logging.LogRecord):
        always_fields = {
            "message": record.getMessage(),
            "timestamp": dt.datetime.fromtimestamp(
                record.created, tz=dt.timezone.utc
            ).isoformat(),
        }
        if record.exc_info is not None:
            always_fields["exc_info"] = self.formatException(record.exc_info)

        if record.stack_info is not None:
            always_fields["stack_info"] = self.formatStack(record.stack_info)

        message = {
            key: msg_val
            if (msg_val := always_fields.pop(val, None)) is not None
            else getattr(record, val)
            for key, val in self.fmt_keys.items()
        }
        message.update(always_fields)

        for key, val in record.__dict__.items():
            if key not in LOG_RECORD_BUILTIN_ATTRS:
                message[key] = val

        return message


class NonErrorFilter(logging.Filter):
    @override
    def filter(self, record: logging.LogRecord) -> bool | logging.LogRecord:
        return record.levelno <= logging.INFO

class KeywordFilter(logging.Filter):
    _keywords = []
    def __init__(self, name="KeywordFilter"):
        super().__init__(name)
    
    @override
    def filter(self, record) -> bool | logging.LogRecord:
        message = record.getMessage()
        for keyword in self._keywords:
            if keyword in message:
                record.msg = message.replace(keyword, "*" * len(keyword))
                record.args = []
                break

        return True
    
    @classmethod
    def add_keyword(cls, keyword: str) -> None:
        cls._keywords.append(keyword)

    @classmethod
    def add_keywords(cls, keywords: list[str]) -> None:
        cls._keywords.extend(keywords)