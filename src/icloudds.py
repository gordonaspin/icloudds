"""
Start point for icloudds. Collect command line parameters using click.
Creates arrays of regexes from include/ignore files
Checks sanity of command line parameters
Creates instance of timeloop at global scope, so we can remove its logger later
"""
import os
import sys
import logging
from datetime import timedelta
import importlib.metadata
import tempfile
from pathlib import Path
import click
from click import version_option
from watchdog.observers import Observer
import timeloop
from fasteners import InterProcessLock

import constants
from context import Context
from event.event_handler import EventHandler
from logger.logger import setup_logging, KeywordFilter

NAME = "icloudds"
logger = logging.getLogger(NAME)
tl = timeloop.Timeloop()

def load_regexes(name: str) -> list[str]:
    """
    Load regexes from file and return as array of strings
    """
    if not Path.is_file(name):
        return []
    with open(file=name, encoding="utf-8") as f:
        return [line.strip() for line in f.readlines() if not line.startswith("#")]

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"], "max_content_width": 120}
@click.command(context_settings=CONTEXT_SETTINGS, options_metavar="<options>", no_args_is_help=True)
@click.option("-d", "--directory",
              help="Local directory that should be used for download",
              type=click.Path(exists=True),
              metavar="<directory>")
@click.option("-u", "--username",
              help="Your iCloud username or email address",
              metavar="<username>")
@click.option("-p", "--password",
              help="Your iCloud password (default: use pyicloud keyring or prompt for password)",
              metavar="<password>")
@click.option("--cookie-directory",
              help="Directory to store cookies for authentication",
              metavar="<directory>",
              default="~/.pyicloud",
              show_default=True)
@click.option("--ignore-icloud",
              help="Ignore iCloud Drive files/folders filename",
              type=click.Path(exists=False),
              metavar="<filename>",
              default=".ignore-icloud.txt",
              show_default=True)
@click.option("--ignore-local",
              help="Ignore Local files/folders filename",
              type=click.Path(exists=False),
              metavar="<filename>",
              default=".ignore-local.txt",
              show_default=True)
@click.option("--include-icloud",
              help="Include iCloud Drive files/folders filename",
              type=click.Path(exists=False),
              metavar="<filename>",
              default=".include-icloud.txt",
              show_default=True)
@click.option("--include-local",
              help="Include Local files/folders filename",
              type=click.Path(exists=False),
              metavar="<filename>",
              default=".include-local.txt",
              show_default=True)
@click.option("--logging-config",
              help="JSON logging config filename (default: logging-config.json)",
              metavar="<filename>",
              default="logging-config.json",
              show_default=True)
@click.option("--retry-period",
              help="Period in seconds to retry failed events",
              type=click.IntRange(min=constants.RETRY_SECONDS),
              metavar="<seconds>",
              default=constants.RETRY_SECONDS,
              show_default=True)
@click.option("--icloud-check-period",
              help="Period in seconds to look for iCloud changes",
              type=click.IntRange(min=constants.ICLOUD_CHECK_SECONDS),
              metavar="<seconds>",
              default=constants.ICLOUD_CHECK_SECONDS,
              show_default=True)
@click.option("--icloud-refresh-period",
              help="Period in seconds to perform full iCloud refresh",
              type=click.IntRange(min=constants.ICLOUD_REFRESH_SECONDS),
              metavar="<seconds>",
              default=constants.ICLOUD_REFRESH_SECONDS,
              show_default=True)
@click.option("--debounce-period",
              help="Period in seconds to queue up filesystem events",
              type=click.IntRange(min=constants.DEBOUNCE_SECONDS),
              metavar="<seconds>",
              default=constants.DEBOUNCE_SECONDS,
              show_default=True)
@click.option("--max-workers",
              help="Maximum number of concurrent workers",
              type=click.IntRange(min=1),
              metavar="<workers>",
              default=os.cpu_count(),
              show_default=True)
@version_option(package_name='icloudds')

def main(directory: str,
         username: str,
         password: str,
         cookie_directory: str,
         ignore_icloud: str,
         ignore_local: str,
         include_icloud: str,
         include_local: str,
         logging_config: str,
         retry_period: int,
         icloud_check_period: int,
         icloud_refresh_period: int,
         debounce_period: int,
         max_workers: int
         ):
    """
    main
    """
    log_path = setup_logging(logging_config=Path(logging_config))
    logger.info("%s %s", NAME, importlib.metadata.version(NAME))
    if password is not None:
        KeywordFilter.add_keyword(password)
    if directory is None:
        logger.error("Local directory is required")
        sys.exit(constants.ExitCode.EXIT_FAILED_MISSING_COMMAND.value)
    if not Path.is_dir(directory):
        logger.error("Local directory %s does not exist or is not a directory", directory)
        sys.exit(constants.ExitCode.EXIT_FAILED_NOT_A_DIRECTORY.value)
    if username is None:
        logger.error("iCloud username is required")
        sys.exit(constants.ExitCode.EXIT_FAILED_MISSING_COMMAND.value)

    directory = Path.resolve(Path(directory))
    lock_file = Path(tempfile.gettempdir()).joinpath(tempfile.gettempdir(), "icloudds.lock")
    lock: InterProcessLock = InterProcessLock(lock_file)
    if lock.acquire(blocking=False):
        try:

            context = Context(directory=directory,
                            username=username,
                            password=password,
                            cookie_directory=cookie_directory,
                            ignore_local=load_regexes(ignore_local),
                            ignore_icloud=load_regexes(ignore_icloud),
                            include_local=load_regexes(include_local),
                            include_icloud=load_regexes(include_icloud),
                            logging_config=logging_config,
                            log_path=log_path,
                            retry_period=timedelta(seconds=retry_period),
                            icloud_check_period=timedelta(seconds=icloud_check_period),
                            icloud_refresh_period=timedelta(seconds=icloud_refresh_period),
                            debounce_period=timedelta(seconds=debounce_period),
                            max_workers=max_workers,
                            timeloop=tl)

            event_handler = EventHandler(ctx=context)
            observer = Observer()
            observer.schedule(event_handler, path=directory, recursive=True)
            observer.start()
            event_handler.run()
            observer.join()
        finally:
            lock.release()
            if lock_file.exists():
                lock_file.unlink()
            sys.exit(constants.ExitCode.EXIT_NORMAL.value)
    else:
        print(f"Another instance of icloudds is running. Check for {lock_file} file")
        sys.exit(constants.ExitCode.EXIT_FAILED_ALREADY_RUNNING.value)

if __name__ == "__main__":
    main()
