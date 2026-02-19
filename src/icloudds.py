"""
Start point for icloudds. Collect command line parameters using click.
Creates arrays of regexes from include/ignore files
Checks sanity of command line parameters
Creates instance of timeloop at global scope, so we can remove its logger later
"""
import os
import platform
import sys
import logging
from datetime import timedelta
import traceback
import importlib.metadata
import tempfile
from pathlib import Path
import click
from click import version_option
from watchdog.observers import Observer
from timeloop import Timeloop
from fasteners import InterProcessLock

import constants
from context import Context
from event.event_handler import EventHandler
from logger.logger import setup_logging, KeywordFilter, Logger

NAME: str = "icloudds"
logger: Logger = logging.getLogger(NAME)
timeloop: Timeloop = Timeloop()

def load_regexes(name: str) -> list[str] | None:
    """
    Load regexes from file and return as array of strings
    """
    if not Path.is_file(name):
        return []
    lines = []
    with open(file=name, encoding="utf-8") as f:
        for line in f.readlines():
            l = line.strip()
            if l.startswith('#'):
                continue
            if len(l):
                lines.append(l)

        return lines if lines else None

CONTEXT_SETTINGS: dict = {"help_option_names": ["-h", "--help"], "max_content_width": 120}
@click.command(help=NAME,
               context_settings=CONTEXT_SETTINGS,
               options_metavar="-d <directory> -u <apple-id> [options]",
               no_args_is_help=True)
@click.option("-d", "--directory",
              required=True,
              help="Local directory that should be used for download",
              type=click.Path(exists=True),
              metavar="<directory>")
@click.option("-u", "--username",
              required=True,
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
@click.option("--ignore-regexes",
              help="Ignore regular expressions",
              type=click.Path(exists=False),
              metavar="<filename>",
              default=".ignore-regexes.txt",
              show_default=True)
@click.option("--include-regexes",
              help="Include regular expressions",
              type=click.Path(exists=False),
              metavar="<filename>",
              default=".include-regexes.txt",
              show_default=True)
@click.option("--logging-config",
              help="JSON logging config filename (default: logging-config.json)",
              metavar="<filename>",
              default="logging-config.json",
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
@version_option(version=importlib.metadata.version(NAME))

# pylint: disable=too-many-branches, too-many-statements
def main(directory: str,
         username: str,
         password: str,
         cookie_directory: str,
         ignore_regexes: str,
         include_regexes: str,
         logging_config: str,
         icloud_check_period: int,
         icloud_refresh_period: int,
         debounce_period: int,
         max_workers: int
         ) -> int:
    """
    main
    """
    if platform.system() == "Darwin":
        try:
            click.confirm("Running on MacOS is not recommended, continue ?", abort=True)
        except click.exceptions.Abort:
            sys.exit(constants.ExitCode.EXIT_NORMAL.value)

    log_path = setup_logging(logging_config=Path(logging_config))
    logger.info("%s %s", NAME, importlib.metadata.version(NAME))
    if password is not None:
        KeywordFilter.add_keyword(password)
    if directory is None:
        logger.error("local directory is required")
        sys.exit(constants.ExitCode.EXIT_FAILED_MISSING_COMMAND.value)
    if not Path.is_dir(directory):
        logger.error("local directory %s does not exist or is not a directory", directory)
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
                            ignore_regexes=load_regexes(ignore_regexes),
                            include_regexes=load_regexes(include_regexes),
                            logging_config=logging_config,
                            log_path=log_path,
                            icloud_check_period=timedelta(seconds=icloud_check_period),
                            icloud_refresh_period=timedelta(seconds=icloud_refresh_period),
                            debounce_period=timedelta(seconds=debounce_period),
                            max_workers=max_workers,
                            timeloop=timeloop)

            if context.ignore_regexes:
                for p in context.ignore_regexes:
                    logger.info("ignore %s", p)
            else:
                logger.info("no ignore regexes")
            if context.include_regexes:
                for p in context.include_regexes:
                    logger.info("include %s", p)
            else:
                logger.info("no include regexes")
            event_handler = EventHandler(ctx=context)
            observer = Observer()
            observer.schedule(event_handler, path=directory, recursive=True)
            observer.start()
            event_handler.run()
            observer.join()
            logger.error("event handler thread ended unexpectedly")
        except Exception as e:
            logger.critical("exception in main thread: %s %s", e.__class__.__name__, e)
            logger.critical(traceback.format_exc())
        finally:
            lock.release()
            if lock_file.exists():
                lock_file.unlink()
            sys.exit(constants.ExitCode.EXIT_NORMAL.value)
    else:
        print(f"another instance of icloudds is running, check for {lock_file} file")
        sys.exit(constants.ExitCode.EXIT_FAILED_ALREADY_RUNNING.value)

    return constants.ExitCode.EXIT_NORMAL.value

if __name__ == "__main__":
    main()
