import os
import click
from click import version_option
import logging
from dataclasses import dataclass
from datetime import timedelta
from logger.logger import setup_logging, KeywordFilter
from watchdog.observers import Observer

import constants
from context import Context
from event.event_handler import EventHandler

logger = logging.getLogger("icloudds")  # __name__ is a common choice

CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])
@click.command(context_settings=CONTEXT_SETTINGS, options_metavar="<options>", no_args_is_help=True)
@click.option("-d", "--directory",      help="Local directory that should be used for download", type=click.Path(exists=True), metavar="<directory>")
@click.option("-u", "--username",       help="Your iCloud username or email address", metavar="<username>")
@click.option("-p", "--password",       help="Your iCloud password (default: use pyicloud keyring or prompt for password)", metavar="<password>")
@click.option("--cookie-directory",     help="Directory to store cookies for authentication (default: ~/.pyicloud)", metavar="</cookie/directory>", default="~/.pyicloud")
@click.option("--ignore-icloud",        help="Ignore iCloud Drive files/folders filename", type=click.Path(exists=False), metavar="<filename>", default=".ignore-icloud.txt")
@click.option("--ignore-local",         help="Ignore Local files/folders filename", type=click.Path(exists=False), metavar="<filename>", default=".ignore-local.txt")
@click.option("--include-icloud",       help="Include iCloud Drive files/folders filename", type=click.Path(exists=False), metavar="<filename>", default=".include-icloud.txt")
@click.option("--include-local",        help="Include Local files/folders filename", type=click.Path(exists=False), metavar="<filename>", default=".include-icloud.txt")
@click.option("--retry-period",         help="Period in seconds to retry failed events", type=click.IntRange(min=constants.RETRY_SECONDS), metavar="<seconds>", default=constants.RETRY_SECONDS)
@click.option("--icloud-check-period",  help="Period in seconds to look for iCloud changes", type=click.IntRange(min=constants.ICLOUD_CHECK_SECONDS), metavar="<seconds>", default=constants.ICLOUD_CHECK_SECONDS)
@click.option("--icloud-refresh-period", help="Period in seconds to perform full iCloud refresh", type=click.IntRange(min=constants.ICLOUD_REFRESH_SECONDS), metavar="<seconds>", default=constants.ICLOUD_REFRESH_SECONDS)
@version_option(package_name='icloudds')

def main(directory: str, username: str, password: str, cookie_directory: str,
         ignore_icloud: str, ignore_local: str,
         include_icloud: str, include_local: str,
         retry_period: int, icloud_check_period: int, icloud_refresh_period: int
         ):

    setup_logging()
    if password is not None:
        KeywordFilter.add_keyword(password)

    if directory is None:
        logger.error("Local directory is required")
        quit()
    if not os.path.isdir(directory):
        logger.error(f"Local directory {directory} does not exist or is not a directory")
        quit()
    if username is None:
        logger.error("iCloud username is required")
        quit()

    ignore_icloud  = [line.strip() for line in open(ignore_icloud).readlines()  if not line.startswith('#')] if ignore_icloud and os.path.isfile(ignore_icloud) else []
    ignore_local   = [line.strip() for line in open(ignore_local).readlines()   if not line.startswith('#')] if ignore_local and os.path.isfile(ignore_local) else []
    include_icloud = [line.strip() for line in open(include_icloud).readlines() if not line.startswith('#')] if include_icloud and os.path.isfile(include_icloud) else []
    include_local  = [line.strip() for line in open(include_local).readlines()  if not line.startswith('#')] if include_local and os.path.isfile(include_local) else []

    context = Context(directory=directory,
                      username=username,
                      password=password,
                      cookie_directory=cookie_directory,
                      ignore_local=ignore_local,
                      ignore_icloud=ignore_icloud,
                      include_local=include_local,
                      include_icloud=include_icloud,
                      retry_period=timedelta(seconds=retry_period),
                      icloud_check_period=timedelta(seconds=icloud_check_period),
                      icloud_refresh_period=timedelta(seconds=icloud_refresh_period))
    
    event_handler = EventHandler(ctx=context)
    observer = Observer()
    observer.schedule(event_handler, path=event_handler._absolute_directory, recursive=True)
    observer.start()
    event_handler.run()
    observer.join()

if __name__ == "__main__":
    main()