"""Event handling module for iCloud directory synchronization.

This module provides the EventHandler class which monitors file system events
and synchronizes changes between local and iCloud storage in a bi-directional manner.
"""
from pathlib import Path
import logging
from logging import Logger
from typing import Type, Callable
from threading import Lock
from time import time, sleep, monotonic
import shutil
from datetime import datetime
from queue import Queue, Empty
from concurrent.futures import ThreadPoolExecutor, Future, as_completed

from watchdog.events import FileSystemEventHandler
from watchdog.events import (
    FileSystemEvent,
    FileCreatedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileDeletedEvent,
    DirCreatedEvent,
    DirModifiedEvent,
    DirDeletedEvent,
    DirMovedEvent
)
from timeloop import Timeloop

from context import Context
from model.base_tree import BaseTree
from model.icloud_tree import ICloudTree
from model.local_tree import LocalTree
from model.file_info import (
    LocalFileInfo,
    LocalFolderInfo,
    ICloudFileInfo,
    ICloudFolderInfo
)
# pylint: disable=unused-import
from model.action_result import (
    ActionResult,
    MkDir,
    Delete,
    Upload,
    Rename,
    Move,
    Download,
    Nil)
from model.thread_safe import ThreadSafeSet

from event.icloud_event import (
    ICloudFolderModifiedEvent,
    ICDSSystemEvent,
    ICDSFileCreatedEvent,
    ICDSFileModifiedEvent,
    ICDSFileMovedEvent,
    ICDSFileDeletedEvent,
    ICDSFolderCreatedEvent,
    ICDSFolderModifiedEvent,
    ICDSFolderMovedEvent,
    ICDSFolderDeletedEvent)
from event.icloud_event import QueuedEvent

logger: Logger = logging.getLogger(__name__)

class EventHandler(FileSystemEventHandler):
    """
        The EventHandler operates as a bi-directional sync engine with the following key components:

        1. EVENT QUEUE & COALESCING:
        - Filesystem events are queued via on_created/on_deleted/on_modified/on_moved handlers
        - Events are coalesced per path to combine multiple rapid changes into single operations
        - Processing is triggered when no new events arrive for a timeout period (10 seconds)

        2. THREAD POOLS:
        - _limited_threadpool: Single worker for sequential iCloud operations
            (upload, delete, rename) to respect iCloud Drive's lack of concurrent
            modification support
        - _unlimited_threadpool: Multiple workers for parallel downloads from iCloud
        - _pending_futures: Tracks in-flight operations, blocking sync until completion

        3. BACKGROUND REFRESH MECHANISM:
        - Periodically checks if iCloud Drive has changed (_is_icloud_dirty)
            via root/trash comparison
        - Triggers full refresh of iCloud tree when changes detected
        - Applies refresh changes (downloads, deletes, folder creation) atomically
            when no events pending
        - Prevents refresh application during active event processing via _refresh_lock

        4. SYNCHRONIZATION STRATEGY:
        - Initial sync: _sync_local_to_icloud → _sync_icloud → _sync_common → delete trash
        - Ongoing sync: Event-driven for local changes, background refresh for iCloud changes
        - Conflict resolution: Local changes newer than iCloud uploaded,
            iCloud changes newer downloaded
        - Suppressed paths: Local deletions and refresh-applied changes exclude watchdog events

        5. EXCEPTION HANDLING & RETRIES:
        - There is little to go wrong with file system events that cannot be anticipated.
        - All iCloud interaction is over the network and things can break there. Each operation
        - with iCloud is submitted on a thread with a return value in the future that contains
        - context to retry (or not)

        MAIN LOOP (run method):
        1. Initial local and iCloud refresh + initial sync
        2. Start background jobs (dirty check, retry, refresh)
        3. Loop: collect events → wait for timeout → dispatch coalesced events → process futures
        4. When no pending futures/events: apply queued background refresh (if available)

    """
    FS_ICDS_map: dict[FileSystemEvent, ICDSSystemEvent] = {
            FileCreatedEvent: ICDSFileCreatedEvent,
            FileModifiedEvent: ICDSFileModifiedEvent,
            FileMovedEvent: ICDSFileMovedEvent,
            FileDeletedEvent: ICDSFileDeletedEvent,
            DirCreatedEvent: ICDSFolderCreatedEvent,
            DirModifiedEvent: ICDSFolderModifiedEvent,
            DirMovedEvent: ICDSFolderMovedEvent,
            DirDeletedEvent: ICDSFolderDeletedEvent,
    }

    def __init__(self, ctx: Context) -> EventHandler:
        """Initialize the EventHandler with a Context object."""
        # Don't use RegexMatchingEventHandler, it's use of strings for paths make using
        # regexes difficult to manage across platforms. Just use the base
        # FileSystemEventHandler and we can discard events with more flexibility
        super().__init__()
        self.ctx: Context = ctx
        self._local: LocalTree = LocalTree(ctx=ctx)
        self._icloud: ICloudTree = ICloudTree(ctx=ctx)
        self._refresh: ICloudTree = None
        self._icloud_dirty: bool = False
        self._refresh_lock: Lock = Lock()
        self._timeloop: Timeloop = ctx.timeloop
        self._latest_refresh_time: datetime = datetime.now()
        self._refresh_is_running: bool = False
        self._event_queue: Queue = Queue()
        self._refresh_queue: Queue = Queue()
        self._suppressed_paths: ThreadSafeSet = ThreadSafeSet()
        self._limited_threadpool: ThreadPoolExecutor = ThreadPoolExecutor(
            max_workers=1)
        self._unlimited_threadpool: ThreadPoolExecutor = ThreadPoolExecutor(
            max_workers=ctx.max_workers)
        self._pending_futures: ThreadSafeSet = ThreadSafeSet()
        self._event_table: dict[ICDSSystemEvent, Callable] = {
            ICDSFileCreatedEvent: self._handle_file_created,
            ICDSFileModifiedEvent: self._handle_file_modified,
            ICDSFileMovedEvent: self._handle_file_moved,
            ICDSFileDeletedEvent: self._handle_file_deleted,
            ICDSFolderCreatedEvent: self._handle_folder_created,
            ICDSFolderModifiedEvent: self._handle_folder_modified,
            ICDSFolderMovedEvent: self._handle_folder_moved,
            ICDSFolderDeletedEvent: self._handle_folder_deleted,
            ICloudFolderModifiedEvent: self._handle_icloud_folder_modified
        }

    def run(self) -> None:
        """
        main processing loop for event handling
        """
        # we do not want this to end ... catch any exception and sleep
        # for a minute. Network errors are most likely and they should
        # be transient.
        while True:
            try:
                event_collector: list[QueuedEvent] = []
                # Initial refresh of local and iCloud trees, perform initial sync
                logger.info("performing initial refresh of Local...")
                self._local.refresh()
                logger.info("performing initial refresh of iCloud Drive...")
                if self._icloud.refresh():
                    self._dump_state(local=self._local, icloud=self._icloud)
                    self._sync_local_to_icloud()
                    self._sync_icloud(self._local, self._icloud)
                    self._sync_common(self._local, self._icloud)
                    self._delete_icloud_trash_items()
                    logger.info("initial sync complete")

                # Register periodic jobs and start timeloop
                self._timeloop.job(interval=self.ctx.icloud_check_period)(
                    self._is_icloud_dirty)
                self._timeloop.job(interval=self.ctx.icloud_refresh_period)(
                    self._refresh_icloud)
                self._timeloop.start()

                logger.info("waiting for events to happen...")
                while True:
                    # Collect FS events until empty for a period to debouce events
                    self._collect_events_until_empty(
                        events=event_collector,
                        name="eventQ",
                        queue=self._event_queue,
                        empty_timeout=self.ctx.debounce_period.total_seconds())

                    # When the background refresh is not running, we can process events
                    with self._refresh_lock, self._pending_futures:

                        # Dispatch collected events, collated by path
                        self._dispatch_events(event_collector=event_collector, name="eventQ")

                        # Process any pending futures from event handling
                        while self._pending_futures:
                            self._process_pending_futures()

                        # since there are no pending futures, we can clear
                        self._suppressed_paths.clear()

                        # collect and dispath ICloudFolderModifiedEvents
                        self._collect_events_until_empty(
                            events=event_collector,
                            name="refreshQ",
                            queue=self._refresh_queue,
                            empty_timeout=0)

                        self._dispatch_events(event_collector=event_collector, name="refreshQ")

                        # If we have a refresh, try to apply it now
                        if self._refresh:
                            if not (self._pending_futures or self._event_queue.qsize()):
                                self._dump_state(local=self._local,
                                                icloud=self._icloud)
                                logger.debug(
                                    "no pending futures or events, applying refresh")
                                self._apply_icloud_refresh() # may generate futures
                                self._dump_state(
                                    local=self._local, icloud=self._icloud, refresh=self._refresh)
                                self._icloud: ICloudTree = self._refresh
                            else:
                                logger.info(
                                    "icloud refresh discarded due to pending futures or events")
                            self._refresh: ICloudTree = None
            except Exception as e:
                logger.debug("caught exception %e in EventHandler.run()", e)
                logger.debug("sleeping for a minute...")
                sleep(60)


    def _refresh_icloud(self, force: bool=False) -> None:
        """
        Called by timeloop periodically to refresh iCloud Drive tree if the refresh
        period has elapsed. Does not run if a forced refresh was recently performed.
        """
        if self._refresh_is_running:
            return
        if (not force
            and (datetime.now() - self._latest_refresh_time) < self.ctx.icloud_refresh_period):
            logger.debug("skipping icloud refresh, period not elapsed")
        else:
            while self._pending_futures.unsafe_len() or self._event_queue.qsize() > 0:
                logger.debug(
                    "icloud refresh waiting on %d pending futures and %d events to quiesce",
                    self._pending_futures.unsafe_len(),
                    self._event_queue.qsize())
                sleep(self.ctx.debounce_period.total_seconds()+5)

            with self._refresh_lock, self._pending_futures:
                # We have the locks
                logger.debug("refreshing iCloud...")
                refresh: ICloudTree = ICloudTree(ctx=self.ctx)
                start = datetime.now()
                if refresh.refresh():
                    logger.debug(
                        "refresh took %.2fs and is consistent",
                        (datetime.now() - start).total_seconds())
                    self._refresh: ICloudTree = refresh
                    self._icloud_dirty: bool = False
                else:
                    logger.warning(
                        "refresh took %.2fs but is inconsistent",
                        (datetime.now() - start).total_seconds())
                self._latest_refresh_time: datetime = datetime.now()
        self._refresh_is_running: bool = False

    def _is_icloud_dirty(self) -> None:
        """
        Called by timeloop periodically to check if iCloud Drive has changed since the last refresh.
        Calls _refresh_icloud if changes are detected.
        """
        if self._icloud_dirty or len(self._pending_futures) > 0 or self._event_queue.qsize() > 0:
            return
        self._icloud_dirty: bool = self._icloud.is_dirty()
        if self._icloud_dirty:
            logger.info("iCloud Drive changes detected...")
            self._refresh_icloud(force=True)

    def _collect_events_until_empty(self,
                                    events: list[QueuedEvent],
                                    name: str,
                                    queue: Queue,
                                    empty_timeout=10.0,
                                    poll_timeout=0.5) -> None:
        """
        Collect events from a queue until it has been empty for `empty_timeout` seconds.

        :param q: queue.Queue instance
        :param empty_timeout: seconds the queue must remain empty before stopping
        :param poll_timeout: timeout for each q.get() call
        :return: list of collected events
        """
        empty_since: float = None

        while True:
            try:
                qe: QueuedEvent = queue.get(timeout=poll_timeout)
            except Empty:
                # Queue is currently empty
                now: float = monotonic()
                if empty_since is None:
                    empty_since = now
                elif now - empty_since >= empty_timeout:
                    break
            else:
                # Got an item, reset empty timer
                empty_since: float = None
                logger.debug("%s: dequeue event %s", name, qe)
                events.append(qe)
                queue.task_done()

    def _dispatch_events(self, event_collector: list[QueuedEvent], name: str) -> None:
        """
        Dispatches coalesced events from the event collector.
        Events are processed in order of arrival time. Most will be
        file system events like created/modified/deleted/moved,
        but can also include iCloudFolderModifiedEvent events
        """
        if not event_collector:
            return
        events: list[QueuedEvent] = self._coalesce_events(event_collector)
        logger.debug("dispatching %d coalesced events...", len(events))
        for qe in events:
            event: ICDSSystemEvent = qe.event
            logger.debug("%s dispatching event: %s", name, event)
            self._suppressed_paths.add(event.src_path)
            self._suppressed_paths.add(event.dest_path)
            self._event_table.get(type(event),
                                  lambda e: logger.warning("%s unhandled event %s", name, e))(event)
        event_collector.clear()

    def _process_pending_futures(self) -> None:
        """
        Wait for pending futures to complete, processing any new futures
        that are created as a result. Process ActionResults as they complete.
        """
        if not self._pending_futures:
            return
        logger.debug("Processing %d pending futures...", len(self._pending_futures))
        for _ in set(self._pending_futures):
            done, self._pending_futures = as_completed(
                self._pending_futures), ThreadSafeSet()
            for future in done:
                result = future.result()
                if isinstance(result, list) and all(isinstance(f, Future) for f in result):
                    self._pending_futures.update(result)
                else:
                    self._handle_action_result(result)

    def _handle_action_result(self,
                              result: ActionResult) -> None:
        """
        Handle the result of an action (upload, download, delete, rename, etc).
        In the case of failure, re-submit the action.
        """
        if result is None:
            return
        if not result.success:
            logger.error("%s %s", result, result.exception)
            if result.fn is not None:
                retry = result.args[-1]
                if retry > 0:
                    logger.debug("retrying %s %s retries left", result, retry)
                    if isinstance(result, Download):
                        self._pending_futures.add(
                            self._unlimited_threadpool.submit(result.fn, *result.args))
                    else:
                        self._pending_futures.add(
                            self._limited_threadpool.submit(result.fn, *result.args))
                else:
                    logger.error("%s has exhausted all retries, giving up", result)
        else:
            if not isinstance(result, Nil):
                logger.info("%s", result)
            if isinstance(result, (Upload, Rename, Move)):
                self._enqueue(
                    event=ICloudFolderModifiedEvent(src_path=result.path.parent),
                    name="refreshQ",
                    queue=self._refresh_queue)
                if isinstance(result, Move):
                    self._enqueue(
                        name="refreshQ",
                        event=ICloudFolderModifiedEvent(src_path=result.dest_path.parent),
                        queue=self._refresh_queue)

    def _apply_icloud_refresh(self) -> None:
        """
        Apply the queued iCloud refresh to the local tree.
        """
        renamed: int = 0
        downloaded: int = 0
        uploaded: int = 0
        updated_downloaded: int = 0
        deleted: int = 0

        renamed = self._apply_renames(self._icloud, self._refresh)
        downloaded, folders_created = self._sync_icloud(
            self._icloud, self._refresh)
        uploaded, updated_downloaded = self._sync_common(
            self._icloud, self._refresh)
        downloaded += updated_downloaded
        deleted_paths = self._icloud.keys() - self._refresh.keys()

        for path in deleted_paths:
            deleted += self._delete_local(Path(path))

        if any((uploaded, downloaded, deleted, folders_created, renamed)):
            logger.info("icloud refresh applied, %d uploaded, "
                "%d downloaded, %d deleted, %d folders created, "
                "%d files/folders renamed",
                uploaded, downloaded, deleted, folders_created, renamed)
        else:
            logger.info("icloud refresh, no changes")

    def _apply_renames(self, these: ICloudTree, those: ICloudTree) -> int:
        """
        Synchronize renames between existing ICloudTree and the refreshed ICloudTree
        by matching files via their docwsids. For each file/folder identified by the
        same docwsid, if the paths differ between the two trees, perform a rename key
        operation on the existing ICloudTree to reflect the new path. For renamed folders, also
        updates all child paths accordingly. Returns the total count of items renamed.
        """
        these_docwsids: dict[str, str] = these.docwsids()
        those_docwsids: dict[str, str] = those.docwsids()

        folder_renames: tuple[str, str] = []
        file_renames: tuple[str, str] = []
        for docwsid in these_docwsids.keys() & those_docwsids.keys():
            if those.get(those_docwsids[docwsid]).name != these.get(these_docwsids[docwsid]).name:
                if isinstance(those.get(those_docwsids[docwsid]), ICloudFolderInfo):
                    folder_renames.append((docwsid, those_docwsids[docwsid]))
                else:
                    file_renames.append((docwsid, those_docwsids[docwsid]))

        # sort by shortest path (least number of folders)
        folder_renames.sort(key=lambda x: x[1].as_posix().count('/'))
        file_renames.sort(key=lambda x: x[1].as_posix().count('/'))

        renames: int = 0
        for docwsid, new_path in folder_renames + file_renames: # rename folders first
            new_path = Path(new_path)
            old_path = Path(these_docwsids[docwsid])
            renames += 1
            self._suppressed_paths.add(Path(old_path))
            self._suppressed_paths.add(Path(new_path))
            try:
                old_full_path = self.ctx.directory.joinpath(old_path)
                new_full_path = self.ctx.directory.joinpath(new_path)
                old_full_path.rename(new_full_path)
                logger.info("rename %s to %s", old_path, new_path)
            except FileNotFoundError:                           # we already renamed the parent
                old_path = new_path.parent.joinpath(old_path.name)
                old_full_path = self.ctx.directory.joinpath(old_path)
                new_full_path = self.ctx.directory.joinpath(new_path)
                old_full_path.rename(new_full_path)
                logger.info("rename %s to %s", old_path, new_path)

            these.re_key(old_path, new_path)

        return renames

    def _sync_icloud(self, these: LocalTree | ICloudTree, those: ICloudTree) -> tuple[int, int]:
        """
        Sync files from iCloud Drive to Local or from Refresh to Local.
        """
        left: str = "Local" if isinstance(these, LocalTree) else "iCloud"
        right: str = "Refresh" if left == "iCloud" else "iCloud"
        downloaded_count: int = 0
        folder_created_count: int = 0
        for path in those.keys() - these.keys():
            if self._local.ignore(path):
                continue
            path: Path = Path(path)
            cfi = those.get(path)
            self._suppressed_paths.add(path)
            if isinstance(cfi, ICloudFolderInfo):
                if not self.ctx.directory.joinpath(path).exists():
                    logger.debug("%s %s is missing locally, creating folders...", right, path)
                    self.ctx.directory.joinpath(path).mkdir(parents=True, exist_ok=True)
                    folder_created_count += 1
            else:
                logger.debug("%s %s is missing locally, downloading to Local...", right, path)
                self._pending_futures.add(self._unlimited_threadpool.submit(
                    self._icloud.download, path, cfi, self._local.add))
                downloaded_count += 1

        return downloaded_count, folder_created_count

    def _sync_common(self, these: LocalTree | ICloudTree, those: ICloudTree) -> tuple[int, int]:
        """
        Sync files common to both trees, resolving differences based on modified time.        
        """
        left: str = "Local" if isinstance(these, LocalTree) else "iCloud"
        right: str = "Refresh" if left == "iCloud" else "iCloud"
        downloaded_count: int = 0
        uploaded_count: int = 0
        for path in set(these.files()) & set(those.files()):
            path = Path(path)
            left_fi: ICloudFileInfo = these.get(path)
            right_fi: ICloudFileInfo = those.get(path)

            if left_fi.modified_time != right_fi.modified_time:
                logger.debug("different time in both: %s %s: %s | %s: %s",
                             path, left, left_fi, right, right_fi)
                # upload if the left file instance is newer and is a local file
                # ignore if left is an iCloudFileInfo (the refresh missed an update)
                if left_fi.modified_time > right_fi.modified_time:
                    logger.debug("%s is newer for %s, uploading to iCloud Drive...", left, path)
                    self._handle_file_modified(
                        ICDSFileModifiedEvent(src_path=path))
                    uploaded_count += 1
                elif left_fi.modified_time < right_fi.modified_time:
                    logger.debug("%s is newer for %s, downloading to Local...", right, path)
                    self._suppressed_paths.add(path)
                    self._pending_futures.add(self._unlimited_threadpool.submit(
                        those.download, path, right_fi, self._local.add))
                    downloaded_count += 1
            else:
                if left_fi.size != right_fi.size:
                    logger.debug("Different size in both: %s %s: %s | %s: %s",
                             path, left, left_fi, right, right_fi)
        return (uploaded_count, downloaded_count)

    def _sync_local_to_icloud(self) -> int:
        """
        Sync files from Local to iCloud Drive that are only present locally.
        """
        uploaded_count: int = 0
        for path in self._local.keys() - self._icloud.keys():
            if self._icloud.ignore(path):
                continue
            logger.debug("only in local: %s %s", path, self._local.get(path))
            if isinstance(self._local.get(path), LocalFolderInfo):
                self._handle_folder_created(event=ICDSFolderCreatedEvent(src_path=Path(path)))
            else:
                self._handle_file_modified(event=ICDSFileModifiedEvent(src_path=Path(path)))
                uploaded_count += 1
        return uploaded_count

    def _delete_icloud_trash_items(self) -> int:
        """
        Delete local files that correspond to items in iCloud Drive trash.
        """
        deleted_count = 0
        for name in self._icloud.keys(root=False):
            if name == BaseTree.ROOT_FOLDER_NAME:
                continue
            path = Path(self._icloud.get(name, root=False).node.data.get("restorePath"))
            if path:
                self._suppressed_paths.add(path)
                deleted: int = self._delete_local(path)
                lfi = self._local.get(path, None)
                if lfi and isinstance(lfi, LocalFileInfo):
                    deleted_count += deleted
        return deleted_count

    def _delete_local(self, path: Path) -> int:
        """
        Delete a local file or folder at the given path.
        """
        deleted: int = 0
        lfi: LocalFolderInfo | LocalFileInfo = self._local.get(path, None)
        if lfi is not None:
            self._suppressed_paths.add(path)
            fs_object_path: Path = self.ctx.directory.joinpath(path)
            if isinstance(lfi, LocalFolderInfo):
                logger.info("deleting local folder %s", path)
                shutil.rmtree(fs_object_path, ignore_errors=True, onexc=None)
            elif isinstance(lfi, LocalFileInfo):
                logger.info("deleting local file %s", path)
                if fs_object_path.is_file():
                    fs_object_path.unlink()
                    deleted = 1
            self._local.pop(path=path)
        return deleted

    def _handle_file_created(self, event: ICDSFileCreatedEvent) -> None:
        """
        Handle a file created event by treating it as a file modified event.
        """
        self._handle_file_modified(event=event)

    def _handle_file_modified(self, event: ICDSFileModifiedEvent) -> None:
        """
        Handle a file modified/created event by uploading the file to iCloud Drive
        if it is new or newer than the iCloud Drive version.
        """
        lfi: LocalFileInfo = self._local.add(path=event.src_path)
        if lfi is None:
            logger.warning("Local file %s disappeared after file modified", event.src_path)
            return

        parent_path: Path = event.src_path.parent
        parent: ICloudFolderInfo = self._icloud.get(parent_path, None)
        cfi: ICloudFileInfo = self._icloud.get(event.src_path, None)

        if cfi is not None:
            if (lfi.modified_time > cfi.modified_time) and lfi.size > 0:
                logger.debug("Local file %s modified/created, iCloud Drive file is outdated",
                             event.src_path)
            else:
                if (lfi.modified_time > cfi.modified_time) and lfi.size == 0:
                    logger.debug("local file %s is newer, but has size 0, skipping upload...",
                                 event.src_path)
                else:
                    logger.debug("iCloud Drive file %s is up to date, skipping upload...",
                                 event.src_path)
                return

        if parent is None:
            logger.debug("local file %s modified/created, creating folders for %s...",
                         event.src_path, parent_path)
            self._handle_folder_created(event=ICDSFolderCreatedEvent(src_path=parent_path))

        cfi = self._icloud.get(event.src_path, None)
        if cfi is None or (lfi.modified_time > cfi.modified_time) and lfi.size > 0:
            logger.debug("local file %s modified/created, uploading to iCloud Drive...",
                            event.src_path)
            self._pending_futures.add(
                self._limited_threadpool.submit(
                    self._icloud.upload,
                    event.src_path,
                    lfi
                )
            )

    def _handle_file_moved(self, event: ICDSFileMovedEvent | ICDSFolderMovedEvent) -> None:
        """
        Handle a file moved/renamed event by renaming the file in iCloud Drive
        if the parent folder is the same, otherwise treat as delete + create.
        """
        # Re-key to the new path, including contained paths
        self._local.re_key(event.src_path, event.dest_path)
        dfi: ICloudFileInfo | ICloudFolderInfo = self._icloud.get(
            event.dest_path, None)

        # Folder already exists or has been renamed already
        if dfi is not None and event.is_directory:
            return

        cfi: ICloudFileInfo | ICloudFolderInfo = self._icloud.get(
            event.src_path, None)

        parent_path: Path = event.src_path.parent
        dest_parent_path: Path = event.dest_path.parent

        logger.debug("local %s %s renamed to %s",
                     'folder' if event.is_directory else 'file',
                     event.src_path,
                     event.dest_path
                     )
        # The the source and destination parents are the same, this is a rename
        if parent_path == dest_parent_path:
            if cfi is not None:
                logger.debug("iCloud Drive renaming %s %s to %s, as parent is the same",
                    'folder' if event.is_directory else 'file',
                    event.src_path,
                    event.dest_path)
                # do this synchronously as there may be other renames coming in the
                # immediate future and we need to preserve order. Also we need to submit
                # the folder refresh request here.
                self._icloud.rename(event.src_path, event.dest_path)
                self._enqueue(
                        event=ICloudFolderModifiedEvent(src_path=event.src_path),
                        name="refreshQ",
                        queue=self._refresh_queue)
            else:
                self._handle_file_modified(event=ICDSFileCreatedEvent(src_path=event.dest_path))
        # The source and destinare parents are different, so this is a true move
        else:
            logger.debug("iCloud Drive moving %s %s to %s as parent is different",
                         'folder' if event.is_directory else 'file',
                         event.src_path,
                         event.dest_path)
            # do this synchronously as there may be other renames coming in the
            # immediate future and we need to preserve order. Also we need to submit
            # the folder refresh request here.
            self._icloud.move(event.src_path, event.dest_path)
            self._enqueue(
                    event=ICloudFolderModifiedEvent(src_path=event.src_path),
                    name="refreshQ",
                    queue=self._refresh_queue)
            self._enqueue(
                    event=ICloudFolderModifiedEvent(src_path=event.dest_path),
                    name="refreshQ",
                    queue=self._refresh_queue)

    def _handle_file_deleted(self, event: ICDSFileDeletedEvent | ICDSFolderDeletedEvent) -> None:
        """
        Handle a file deleted event by deleting the file/folder from iCloud Drive.
        """
        if event.src_path.exists():
            logger.warning("local file/folder %s reappeared after file delete", event.src_path)
            return

        self._local.pop(event.src_path)
        parent_path: Path = event.src_path.parent
        parent: ICloudFolderInfo = self._icloud.get(parent_path, None)

        cfi: ICloudFileInfo | ICloudFolderInfo = self._icloud.get(event.src_path, None)
        if parent is not None and cfi is not None:
            # Delete the file from iCloud Drive
            logger.debug("local %s %s deleted, deleting iCloud Drive item",
                            'folder' if event.is_directory else 'file',
                            event.src_path)
            self._pending_futures.add(self._limited_threadpool.submit(
                self._icloud.delete, event.src_path, cfi))

    def _handle_folder_created(self, event: ICDSFolderCreatedEvent) -> None:
        """Handle a directory created event by creating folders in iCloud Drive if needed."""
        if self._local.add(event.src_path) is None:
            logger.warning("local folder %s disappeared after file moved", event.src_path)
            return

        parent_path: Path = event.src_path.parent
        parent: ICloudFolderInfo = self._icloud.get(parent_path, None)
        cfi: ICloudFolderInfo = self._icloud.get(event.src_path, None)

        if parent is None or cfi is None:
            # Create parent folders as needed
            self._pending_futures.add(self._limited_threadpool.submit(
                self._icloud.create_icloud_folders, event.src_path))
        else:
            logger.debug("iCloud Drive folder %s already exists, skipping creation...",
                         event.src_path)

    def _handle_folder_modified(self, _event: ICDSFolderModifiedEvent) -> None:
        """
        Ignore directory modified events as they do not affect iCloud Drive.
        """
        raise RuntimeError(f"unexpected event {_event} received")

    def _handle_folder_moved(self, event: ICDSFolderMovedEvent) -> None:
        """
        Handle a directory moved/renamed event by treating it as a file moved event.
        """
        self._handle_file_moved(event=event)

    def _handle_folder_deleted(self, event: ICDSFolderDeletedEvent) -> None:
        """
        Handle a directory deleted event by deleting the folder from iCloud Drive.
        """
        self._handle_file_deleted(event=event)
        self._local.prune(path=event.src_path)

    def _handle_icloud_folder_modified(self, event: ICloudFolderModifiedEvent) -> None:
        """
        Handle iCloud folder modified event by processing the folder for changes. Not recursive.
        """
        self._pending_futures.add(
            self._unlimited_threadpool.submit(
                self._icloud.process_folder,
                root=True,
                path=event.src_path,
                ignore=True,
                recursive=False,
                executor=self._unlimited_threadpool
            )
        )

    def _coalesce_events(self, events: list[QueuedEvent]) -> list[QueuedEvent]:
        """
        Coalesce events per path while preserving global time order.
        """
        by_path: dict[str, list[QueuedEvent]] = {}
        for ev in events:
            by_path.setdefault(ev.event.src_path, []).append(ev)
        coalesced: list[QueuedEvent] = []

        for _, evs in by_path.items():
            evs.sort(key=lambda e: e.timestamp)
            final: QueuedEvent = evs[-1]
            # Deletion overrides everything
            if any(isinstance(e.event,
                (ICDSFileDeletedEvent,
                ICDSFolderDeletedEvent)) for e in evs
                ):
                try:
                    final = next(
                        e for e in reversed(evs)
                        if isinstance(e.event,
                                    (ICDSFileDeletedEvent,
                                    ICDSFolderDeletedEvent)))
                except StopIteration:
                    pass
            # Moving overrides everything, also
            elif any(isinstance(e.event,
                (ICDSFileMovedEvent,
                 ICDSFolderMovedEvent)) for e in evs
                ):
                try:
                    final = next(
                        e for e in reversed(evs)
                        if isinstance(e.event,
                                    (ICDSFileMovedEvent,
                                    ICDSFolderModifiedEvent)))
                except StopIteration:
                    pass
            # Created + modified → created
            elif isinstance(evs[0].event,
                            (ICDSFileCreatedEvent,
                             ICDSFolderCreatedEvent)):
                final = evs[0]
            if final is None:
                pass
            if final:
                coalesced.append(final)

        coalesced = self._conflate_folder_events(coalesced, ICDSFolderDeletedEvent)
        coalesced = self._conflate_folder_events(coalesced, ICDSFolderMovedEvent)
        coalesced.sort(key=lambda e: e.timestamp)

        return coalesced
    # pylint: disable=too-many-branches
    def _conflate_folder_events(self,
                                events: list[QueuedEvent],
                                cls: Type[ICDSFolderDeletedEvent | ICDSFolderMovedEvent]
                                ) -> list[QueuedEvent]:
        """
        Filter redundant events when directory moves occur. Removes nested directory move events
        (subdirectories moved as part of a parent directory move) and file-level events that are
        implicitly covered by a directory move (e.g., files created/deleted/moved within a
        moved directory). This prevents duplicate or conflicting operations on the same paths.
        """
        folder_evs = [qe for qe in events if isinstance(qe.event, cls)]
        if not folder_evs:
            return events

        filtered: list[QueuedEvent] = []
        for qe in events:
            # Drop nested ICDSFolderDeletedEvent/ICDSFolderMovedEvent (strict subdirectories only)
            if isinstance(qe.event, cls):
                src_path: Path = qe.event.src_path
                is_nested: bool = False
                for other_qe in folder_evs:
                    if qe is other_qe:
                        continue
                    other_src:Path = Path(other_qe.event.src_path)
                    # If src_path is inside other_src and not equal, it's nested
                    if src_path != other_src:
                        if src_path.is_relative_to(other_src):
                            is_nested = True
                        break
                if is_nested:
                    continue

            # Drop file events covered by a ICDSFolderDeletedEvent/ICDSFolderMovedEvent
            if isinstance(qe.event,
                          (ICDSFileMovedEvent,
                           ICDSFileCreatedEvent,
                           ICDSFileDeletedEvent)):
                src_path:Path = Path(qe.event.src_path)
                covered: bool = False
                for dm_qe in folder_evs:
                    dm_src:Path = dm_qe.event.src_path
                    if src_path == dm_src:
                        covered = True
                        break
                    if src_path.is_relative_to(dm_src):
                        covered = True
                        break
                if covered:
                    continue

            filtered.append(qe)

        return filtered

    def _map_event(self, event: FileSystemEvent) -> ICDSSystemEvent:
        """
        Map watchdog FileSystemEvents to our modified ICDSSystemEvents
        """
        return EventHandler.FS_ICDS_map[type(event)](event, None, self.ctx.directory)

    def _enqueue(self, name: str, event: ICDSSystemEvent, queue: Queue) -> None:
        """
        Enqueue a filesystem event for processing unless it is suppressed or should be ignored.
        """
        # Drop events that are in our suppressed path list
        if event.src_path in self._suppressed_paths:
            logger.debug("%s not enqueuing event (path suppressed): %s", name, event.src_path)
            return
        # We don't care about these events
        if self._local.ignore(event.src_path) or self._icloud.ignore(event.src_path):
            logger.debug("%s not enqueuing event (ignored src_path): %s", name, event.src_path)
            return
        # We don't care about things moving our of our universe
        if event.dest_path:
            if (self._local.ignore(event.dest_path)
                or self._icloud.ignore(event.dest_path)):
                logger.debug("%s not enqueuing event (ignored dest_path): %s",
                             name,
                             event.dest_path)
                return
        logger.debug("%s enqueueing: %s", name, event)
        qe: QueuedEvent = QueuedEvent(
            timestamp=time(),
            event=event)
        queue.put(qe)

    def on_created(self, event) -> None:
        """Handle filesystem created event callback from watchdog."""
        self._enqueue(event=self._map_event(event=event), name="eventQ",
                            queue=self._event_queue)

    def on_deleted(self, event) -> None:
        """Handle filesystem deleted event callback from watchdog."""
        self._enqueue(event=self._map_event(event=event), name="eventQ",
                            queue=self._event_queue)

    def on_modified(self, event) -> None:
        """Handle filesystem modified event callback from watchdog."""
        if isinstance(event, DirModifiedEvent):
            return
        self._enqueue(event=self._map_event(event=event), name="eventQ",
                            queue=self._event_queue)

    def on_moved(self, event) -> None:
        """Handle filesystem moved event callback from watchdog."""
        self._enqueue(event=self._map_event(event=event), name="eventQ",
                            queue=self._event_queue)

    def _dump_state(self, local: LocalTree, icloud: ICloudTree, refresh: ICloudTree = None) -> None:
        """
        Dump the current state of the local and iCloud trees to log files for debugging.
        """
        filename = "_before.log"
        if refresh is not None:
            filename = "_after.log"

        for tree, name in [(local, "local"), (icloud, "icloud"), (refresh, "refresh")]:
            if tree is not None:
                with open(
                    self.ctx.log_path.joinpath("icloudds_"+name+filename),
                    'w',
                    encoding="utf-8") as f:
                    sorted_dict = dict(sorted(tree.items()))
                    for k, v in sorted_dict.items():
                        f.write(f"{k}: {v!r}\n")
