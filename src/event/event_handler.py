import os
import logging
from threading import Lock
import time
import shutil
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, Future, wait, as_completed

from watchdog.events import RegexMatchingEventHandler
from watchdog.events import FileSystemEvent, FileCreatedEvent, FileModifiedEvent, FileMovedEvent, FileDeletedEvent, DirCreatedEvent, DirModifiedEvent, DirDeletedEvent, DirMovedEvent
from queue import Queue, Empty
from pyicloud.services.drive import DriveNode
from timeloop import Timeloop

from context import Context
from model.base_tree import BaseTree
from model.icloud_tree import iCloudTree
from model.local_tree import LocalTree
from model.file_info import FolderInfo, LocalFileInfo, LocalFolderInfo, iCloudFileInfo, iCloudFolderInfo
from model.action_result import MkDir, Delete, Upload, Rename, Download, Nil
from event.icloud_event import iCloudFolderModifiedEvent
from model.thread_safe import ThreadSafeList
from event.icloud_event import QueuedEvent

logger = logging.getLogger(__name__)  # __name__ is a common choice

class EventHandler(RegexMatchingEventHandler):
    """
        The EventHandler operates as a bi-directional sync engine with the following key components:
        
        1. EVENT QUEUE & COALESCING:
        - Filesystem events are queued via on_created/on_deleted/on_modified/on_moved handlers
        - Events are coalesced per path to combine multiple rapid changes into single operations
        - Processing is triggered when no new events arrive for a timeout period (10 seconds)
        
        2. THREAD POOLS:
        - _limited_threadpool: Single worker for sequential iCloud operations (upload, delete, rename)
            to respect iCloud Drive's lack of concurrent modification support
        - _unlimited_threadpool: Multiple workers for parallel downloads from iCloud
        - _pending_futures: Tracks in-flight operations, blocking sync until completion
        
        3. BACKGROUND REFRESH MECHANISM:
        - Periodically checks if iCloud Drive has changed (_is_icloud_dirty) via root/trash comparison
        - Triggers full refresh of iCloud tree when changes detected
        - Applies refresh changes (downloads, deletes, folder creation) atomically when no events pending
        - Prevents refresh application during active event processing via _refresh_lock
        
        4. SYNCHRONIZATION STRATEGY:
        - Initial sync: _sync_local_to_icloud → _sync_icloud → _sync_common → delete trash
        - Ongoing sync: Event-driven for local changes, background refresh for iCloud changes
        - Conflict resolution: Local changes newer than iCloud uploaded, iCloud changes newer downloaded
        - Suppressed paths: Local deletions and refresh-applied changes exclude watchdog events
        
        5. EXCEPTION HANDLING & RETRIES:
        - Failed operations stored in _exception_events
        - Periodically retried via _retry_exception_events job (configurable interval)
        - Network/auth errors trigger iCloud tree exception handling
        
        MAIN LOOP (run method):
        1. Initial local and iCloud refresh + initial sync
        2. Start background jobs (dirty check, retry, refresh)
        3. Loop: collect events → wait for timeout → dispatch coalesced events → process futures
        4. When no pending futures/events: apply queued background refresh (if available)

    """
    def __init__(self, ctx: Context):
        self.ctx: Context = ctx
        self._absolute_directory: str = os.path.realpath(os.path.normpath(ctx.directory))
        self._local: LocalTree = LocalTree(ctx=ctx)
        self._icloud: iCloudTree = iCloudTree(ctx=ctx)
        self._refresh: iCloudTree = None
        for s in self._local.ignores_patterns:
            logger.debug(f"ignore local: {s}")
        for s in self._local.includes_list:
            logger.debug(f"include local: {s}")
        for s in self._icloud.ignores_patterns:
            logger.debug(f"ignore icloud: {s}")
        for s in self._icloud.includes_list:
            logger.debug(f"include icloud: {s}")

        super().__init__(regexes=None, ignore_regexes=self._local.ignores_patterns, ignore_directories=False, case_sensitive=False)
        self._icloud_dirty: bool = False
        self._refresh_lock: Lock = Lock()
        self._timeloop: Timeloop = ctx.timeloop
        self._latest_refresh_time: datetime = datetime.now()
        self._event_queue: Queue = Queue()
        self._exception_events: ThreadSafeList = ThreadSafeList()
        self._suppressed_paths: ThreadSafeList = ThreadSafeList()
        self._limited_threadpool: ThreadPoolExecutor = ThreadPoolExecutor(max_workers=1)
        self._unlimited_threadpool: ThreadPoolExecutor = ThreadPoolExecutor(max_workers=ctx.max_workers)
        self._pending_futures: set = set()
        self._event_table = {
            FileCreatedEvent: self._handle_file_created_event,
            FileModifiedEvent: self._handle_file_modified_event,
            FileMovedEvent: self._handle_file_moved_event,
            FileDeletedEvent: self._handle_file_deleted_event,
            DirCreatedEvent: self._handle_dir_created_event,
            DirModifiedEvent: self._handle_dir_modified_event,
            DirMovedEvent: self._handle_dir_moved_event,
            DirDeletedEvent: self._handle_dir_deleted_event,
            iCloudFolderModifiedEvent: self._handle_icloud_folder_modified_event
        }

    def run(self) -> None:
        event_collector: list[QueuedEvent] = []

        # Initial refresh of local and iCloud trees, perform initial sync
        logger.info("Performing initial refresh of Local...")
        self._local.refresh()
        logger.info("Performing initial refresh of iCloud Drive...")
        if self._icloud.refresh():
            self._dump_state(local=self._local, icloud=self._icloud)
            self._sync_local_to_icloud()
            self._sync_icloud(self._local, self._icloud)
            self._sync_common(self._local, self._icloud)
            self._delete_icloud_trash_items()
            logger.info("Initial sync complete")

        # Register periodic jobs and start timeloop
        self._timeloop.job(interval=self.ctx.icloud_check_period)(self._is_icloud_dirty)
        self._timeloop.job(interval=self.ctx.retry_period)(self._retry_exception_events)
        self._timeloop.job(interval=self.ctx.icloud_refresh_period)(self._refresh_icloud)
        self._timeloop.start()

        logger.info("Waiting for events to happen...")
        while True:
            try:
                while True:
                    # Collect events until timeout
                    event: QueuedEvent = self._event_queue.get(block=True, timeout=10)
                    event_collector.append(event)
            except Empty:
                # When the background refresh is not running, we can process events
                with self._refresh_lock:
                    # Dispatch collected events, collated by path
                    self._dispatch_events(event_collector=event_collector)
                    # Process any pending futures from event handling
                    self._process_pending_futures()
                    if not self._pending_futures:
                        # No pending futures, we can clear suppressed paths
                        self._suppressed_paths.clear()
                        # If a refresh is pending, apply it now
                        if self._refresh:
                            if not (self._pending_futures or self._event_queue.qsize()):
                                self._dump_state(local=self._local, icloud=self._icloud)
                                logger.debug("No pending futures or events, proceeding with applying refresh")
                                uploaded, downloaded, deleted, folders_created = self._apply_icloud_refresh()
                                if any([uploaded, downloaded, deleted, folders_created]):
                                    logger.info(f"Background refresh applied, {uploaded} uploaded, {downloaded} downloaded, {deleted} deleted, {folders_created} folders created")
                                else:
                                    logger.debug(f"Background refresh, no changes")
                                self._dump_state(local=self._local, icloud=self._icloud, refresh=self._refresh)
                                self._icloud = self._refresh
                            else:
                                logger.debug(f"Background refresh discarded due to pending futures or events")
                            self._refresh = None

    def _refresh_icloud(self, force=False):
        """
        Called by timeloop periodically to refresh iCloud Drive tree if the refresh period has elapsed.
        Does not run if a forced refresh was recently performed.
        """
        if not force and (datetime.now() - self._latest_refresh_time) < self.ctx.icloud_refresh_period:
            logger.debug("skipping icloud refresh, period not elapsed")
            return
        with self._refresh_lock:
            logger.debug("refreshing iCloud...")
            refresh = iCloudTree(ctx=self.ctx)
            if refresh.refresh():
                logger.debug("refresh is consistent")
                self._refresh = refresh
                self._icloud_dirty = False
                self._latest_refresh_time = datetime.now()
            else:
                logger.warning("refresh is inconsistent")
    def _is_icloud_dirty(self):
        """
        Called by timeloop periodically to check if iCloud Drive has changed since the last refresh.
        Calls _refresh_icloud if changes are detected.
        """
        if self._icloud_dirty:
            return
        with self._refresh_lock:
            logger.debug("checking if icloud is dirty...")
            self._icloud_dirty = self._icloud.root_has_changed() or self._icloud.trash_has_changed()
        if self._icloud_dirty:
            logger.info("iCloud Drive changes detected...")
            self._refresh_icloud(force=True)

    def _dispatch_events(self, event_collector):
        """
        Dispatches coalesced events from the event collector. These are processed in order of arrival time. Most will be
        file system events like created/modified/deleted/moved, but can also include iCloudFolderModifiedEvent events
        """
        if not event_collector:
            return
        events: list[QueuedEvent] = self._coalesce_events(event_collector)
        logger.debug(f"Dispatching {len(events)} coalesced events...")
        for qe in events:
            event: FileSystemEvent = qe.event
            if self._local.ignore(event.src_path, event.is_directory) or self._icloud.ignore(event.src_path, event.is_directory):
                continue
            logger.debug(f"Dispatching event: {event}")
            self._event_table.get(type(event), lambda e: logger.warning(f"Unhandled event {e}"))(event)
        event_collector.clear()

    def _process_pending_futures(self):
        """
        Wait for pending futures to complete, processing any new futures that are created as a result.
        Process ActionResults as they complete.
        """
        if not self._pending_futures:
            return
        logger.debug(f"Processing {len(self._pending_futures)} pending futures...")
        for _ in set(self._pending_futures):
            done, self._pending_futures = as_completed(self._pending_futures), set()
            for future in done:
                result = future.result()
                if isinstance(result, list) and all(isinstance(f, Future) for f in result):
                    self._pending_futures.update(result)
                else:
                    self._handle_action_result(result)

    def _handle_action_result(self, result: Delete | Upload | Download | MkDir | Nil | None):
        """
        Handle the result of an action (upload, download, delete, rename). In the case of failure, re-submit the action.
        """
        if result is None:
            return
        if not result.success:
            logger.error(f"{result}: {result.exception}")
            if result.fn is not None:
                self._pending_futures.add(self._unlimited_threadpool.submit(result.fn, *result.args))
        else:
            if not isinstance(result, Nil):
                logger.info(f"{result}")
            if isinstance(result, (Upload, Download, Rename)):
                self._enqueue_event(iCloudFolderModifiedEvent(src_path=os.path.normpath(os.path.dirname(result.path))))

    def _apply_icloud_refresh(self) -> tuple[int, int, int, int]:
        """
        Apply the queued iCloud refresh to the local tree.
        """
        downloaded, folders_created = self._sync_icloud(self._icloud, self._refresh)
        uploaded, updated_downloaded = self._sync_common(self._icloud, self._refresh)
        deleted = self._icloud.root.keys() - self._refresh.root.keys()
        for path in deleted:
            self._delete_local(path)
        return (uploaded, downloaded + updated_downloaded, len(deleted), folders_created)

    def _sync_icloud(self, these: LocalTree | iCloudTree, those: iCloudTree) -> tuple[int, int]:
        """
        Sync files from iCloud Drive to Local or from Refresh to Local.
        """
        left = "Local" if isinstance(these, LocalTree) else "iCloud"
        right = "Refresh" if left == "iCloud" else "iCloud"
        downloaded_count = 0
        folder_created_count = 0
        in_icloud = those.root.keys() - these.root.keys()
        for path in in_icloud:
            if self._local.ignore(path, isinstance(those.root[path], iCloudFolderInfo)):
                continue
            self._suppressed_paths.append(path)
            cfi = those.root[path]
            try:
                if isinstance(cfi, iCloudFolderInfo):
                    if not os.path.exists(os.path.join(self._local.root_path, path)):
                        logger.debug(f"{right} {path} is missing locally, creating folders...")
                        os.makedirs(os.path.join(self._local.root_path, path), exist_ok=True)
                        folder_created_count += 1
                else:
                    logger.debug(f"{right} {path} is missing locally, downloading to Local...")
                    self._pending_futures.add(self._unlimited_threadpool.submit(self._icloud.download, path, cfi, self._local.add))
                    downloaded_count += 1
            except Exception as e:
                logger.error(f"iCloud Drive download failed for {path}: {e}")
                self._icloud.handle_drive_exception(e)
        return downloaded_count, folder_created_count

    def _sync_common(self, these: LocalTree | iCloudTree, those: iCloudTree) -> tuple[int, int]:
        """
        Sync files common to both trees, resolving differences based on modified time.        
        """
        left = "Local" if isinstance(these, LocalTree) else "iCloud"
        right = "Refresh" if left == "iCloud" else "iCloud"
        downloaded_count = 0
        uploaded_count = 0
        in_common = these.root.keys() & those.root.keys()
        for path in in_common:
            left_fi = these.root[path]
            right_fi = those.root[path]
            if isinstance(left_fi, FolderInfo) and isinstance(right_fi, FolderInfo):
                continue
            if left_fi.modified_time != right_fi.modified_time:
                logger.debug(f"Different time in both: {path} -> {left}: {left_fi} | {right}: {right_fi}")
                # upload if the left file instance is newer and is a local file
                # ignore if left is an iCloudFileInfo (the refresh missed an update)
                if left_fi.modified_time > right_fi.modified_time and isinstance(left_fi, LocalFileInfo):
                    logger.debug(f"{left} is newer for {path}, uploading to iCloud Drive...")
                    self._handle_file_modified_event(FileModifiedEvent(src_path=path))
                    uploaded_count += 1
                elif left_fi.modified_time < right_fi.modified_time:
                    logger.debug(f"{right} is newer for {path}, downloading to Local...")
                    self._suppressed_paths.append(path)
                    self._pending_futures.add(self._unlimited_threadpool.submit(those.download, path, right_fi, self._local.add))
                    downloaded_count += 1
            else:   
                if left_fi.size != right_fi.size:
                    logger.debug(f"Different size in both: {path} -> {left}: {left_fi} | {right}: {right_fi}")
        return (uploaded_count, downloaded_count)

    def _sync_local_to_icloud(self) -> int:
        """
        Sync files from Local to iCloud Drive that are only present locally.
        """
        only_in_local = self._local.root.keys() - self._icloud.root.keys()
        uploaded_count = 0
        for path in only_in_local:
            if self._icloud.ignore(path, isinstance(self._local.root[path], LocalFolderInfo)):
                continue
            logger.debug(f"Only in local: {path} -> {self._local.root[path]}")
            if isinstance(self._local.root[path], LocalFolderInfo):
                self._handle_dir_created_event(DirCreatedEvent(src_path=path))
            else:
                self._handle_file_modified_event(FileModifiedEvent(src_path=path))
                uploaded_count +=1
        return uploaded_count
    
    def _delete_icloud_trash_items(self) -> int:
        """
        Delete local files that correspond to items in iCloud Drive trash.
        """
        deleted_count = 0
        for name in self._icloud.trash:
            if name == BaseTree.ROOT_FOLDER_NAME:
                continue
            path = self._icloud.trash[name].node.data.get("restorePath")
            if path:
                self._delete_local(path)
                lfi = self._local.root.get(path, None)
                if lfi and isinstance(lfi, LocalFileInfo):
                    deleted_count += 1
        return deleted_count

    def _delete_local(self, path: str):
        """
        Delete a local file or folder at the given path.
        """
        self._suppressed_paths.append(path)
        lfi = self._local.root.get(path, None)
        if lfi is not None:
            fs_object_path = os.path.join(self._absolute_directory, path)
            if isinstance(lfi, LocalFolderInfo):
                logger.info(f"deleting local folder {path}")
                shutil.rmtree(fs_object_path, ignore_errors=True, onexc=None)
            elif isinstance(lfi, LocalFileInfo):
                logger.info(f"deleting local file {path}")
                if os.path.isfile(fs_object_path):
                    os.remove(fs_object_path)
            self._local.root.pop(path)

    """ Event Handlers """
    def _handle_icloud_folder_modified_event(self, event: iCloudFolderModifiedEvent) -> None:
        """
        Handle iCloud folder modified event by processing the folder for changes. Not recursive.
        """
        self._pending_futures.add(self._unlimited_threadpool.submit(self._icloud.process_folder, root=self._icloud.root, path=event.src_path, ignore=True, recursive=False, executor=self._unlimited_threadpool))
    
    def _handle_file_created_event(self, event: FileCreatedEvent) -> None:
        """
        Handle a file created event by treating it as a file modified event.
        """
        self._handle_file_modified_event(event)

    def _handle_file_modified_event(self, event: FileModifiedEvent) -> None:
        """
        Handle a file modified/created event by uploading the file to iCloud Drive if it is new or newer than the iCloud Drive version.
        """
        parent_path: str = os.path.normpath(os.path.dirname(event.src_path))
        lfi: LocalFileInfo = self._local.add(event.src_path)

        parent: iCloudFolderInfo = self._icloud.root.get(parent_path, None)
        cfi: iCloudFileInfo = self._icloud.root.get(event.src_path, None)

        if cfi is not None:
            if lfi.modified_time > cfi.modified_time and lfi.size > 0:
                logger.debug(f"Local file {event.src_path} modified/created, iCloud Drive file {event.src_path} is outdated")
                self._pending_futures.add(self._limited_threadpool.submit(self._icloud.delete, event.src_path, cfi))
            else:
                logger.debug(f"iCloud Drive file {event.src_path} is up to date, skipping upload...")
                return
        # cfi is None
        try:
            if parent is None:
                logger.debug(f"Local file {event.src_path} modified/created, creating folders for {parent_path}...")
                self._handle_dir_created_event(DirCreatedEvent(src_path=parent_path))
            lfi = self._local.root[event.src_path]
            cfi = self._icloud.root.get(event.src_path, None)
            if cfi is None or lfi.modified_time > cfi.modified_time and lfi.size > 0:
                if isinstance(lfi, LocalFileInfo):
                    logger.debug(f"Local file {event.src_path} modified/created, uploading to iCloud Drive...")
                    self._pending_futures.add(self._limited_threadpool.submit(self._icloud.upload, event.src_path, lfi))
        except Exception as e:
            logger.error(f"iCloud Drive upload failed for {event.src_path}: {e}")
            self._icloud.handle_drive_exception(e)
            self._exception_events.append(event)

    def _handle_file_moved_event(self, event: FileMovedEvent) -> None:
        """
        Handle a file moved/renamed event by renaming the file in iCloud Drive if the parent folder is the same,
        otherwise treat as delete + create.
        """
        parent_path: str = os.path.normpath(os.path.dirname(event.src_path))
        dest_parent_path: str = os.path.normpath(os.path.dirname(event.dest_path))
        cfi: iCloudFileInfo | iCloudFolderInfo= self._icloud.root.get(event.src_path, None)

        logger.debug(f"Local {'file' if isinstance(cfi, iCloudFileInfo) else 'folder'} {event.src_path} renamed to {event.dest_path}")
        if parent_path == dest_parent_path:
            logger.debug(f"iCloud Drive renaming {'file' if isinstance(cfi, iCloudFileInfo) else 'folder'} {event.src_path} to {event.dest_path}, as parent is the same")
            # Remove the file from the local tree
            self._local.root.pop(event.src_path)
            # Add the file back with the new name
            self._local.add(event.dest_path)
            self._pending_futures.add(self._limited_threadpool.submit(self._icloud.rename, event.src_path, event.dest_path))
            #self._enqueue_event(iCloudFolderModifiedEvent(src_path=parent_path))
        else:
            # Moving to a different folder is not supported by pyicloud, treat as delete + create
            logger.debug(f"iCloud Drive moving {'file' if isinstance(cfi, iCloudFileInfo) else 'folder'} {event.src_path} to {event.dest_path} as parent is different")
            if isinstance(cfi, iCloudFileInfo):
                de = FileDeletedEvent(src_path=event.src_path)
                ce = FileCreatedEvent(src_path=event.dest_path)
            else:
                de = DirDeletedEvent(src_path=event.src_path)
                ce = DirCreatedEvent(src_path=event.dest_path)
            self._handle_file_deleted_event(de)
            self._handle_file_created_event(ce)

    def _handle_file_deleted_event(self, event: FileDeletedEvent) -> None:
        """
        Handle a file deleted event by deleting the file/folder from iCloud Drive.
        """
        parent_path: str = os.path.normpath(os.path.dirname(event.src_path))
        parent: iCloudFolderInfo = self._icloud.root.get(parent_path, None)
        cfi: iCloudFileInfo | iCloudFolderInfo= self._icloud.root.get(event.src_path, None)
        if parent is not None and cfi is not None:
            try:
                # Delete the file from iCloud Drive
                logger.info(f"Local {'file' if isinstance(cfi, iCloudFileInfo) else 'folder'} {event.src_path} deleted, deleting iCloud Drive item")
                self._pending_futures.add(self._limited_threadpool.submit(self._icloud.delete, event.src_path, cfi))
                self._local.root.pop(event.src_path)
            except Exception as e:
                logger.error(f"iCloud Drive delete failed for {event.src_path}: {e}")
                self._icloud.handle_drive_exception(e)
                self._exception_events.append(event)

    def _handle_dir_created_event(self, event: DirCreatedEvent) -> None:
        parent_path: str = os.path.normpath(os.path.dirname(event.src_path))
        self._local.add(event.src_path)
        parent: iCloudFolderInfo = self._icloud.root.get(parent_path, None)
        cfi: iCloudFolderInfo = self._icloud.root.get(event.src_path, None)

        if parent is None or cfi is None:
            # Create parent folders as needed
            self._pending_futures.add(self._limited_threadpool.submit(self._icloud.create_icloud_folders, event.src_path))
        else:
            logger.debug(f"iCloud Drive folder {event.src_path} already exists, skipping creation...")

    def _handle_dir_moved_event(self, event: DirMovedEvent) -> None:
        """
        Handle a directory moved/renamed event by treating it as a file moved event.
        """
        self._handle_file_moved_event(event)
        return
        
    def _handle_dir_deleted_event(self, event: DirDeletedEvent) -> None:
        """
        Handle a directory deleted event by deleting the folder from iCloud Drive.
        """
        self._handle_file_deleted_event(event)
        return

    def _handle_dir_modified_event(self, event: DirModifiedEvent) -> None:
        """
        Ingnore directory modified events as they do not affect iCloud Drive.
        """
        return
    
    def _retry_exception_events(self) -> None:
        if self._exception_events:
            logger.debug(f"Reprocessing {len(self._exception_events)} events...")
            for event in self._exception_events:
                logger.debug(f"Reprocessing event: {event}")
                self._exception_events.remove(event)
                self._event_table.get(type(event), lambda e: logger.debug(f"Unhandled event {e}"))(event)

    def _dump_state(self, local: LocalTree, icloud: iCloudTree, refresh: iCloudTree=None):
        """
        Dump the current state of the local and iCloud trees to log files for debugging.
        """
        filename = "_before.log"
        if refresh is not None:
            filename = "_after.log"

        for tree, name in [(local, "local"), (icloud, "icloud"), (refresh, "refresh")]:
            if tree is not None:
                with open(os.path.join(self.ctx.log_path, "icloudds_"+name+filename), 'w') as f:
                    sorted_dict = dict(sorted(tree.root.items()))
                    for k, v in sorted_dict.items():
                        f.write(f"{k}: {v!r}\n")

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
            final = evs[-1]
            # Deletion overrides everything
            if any(e.event.event_type == "deleted" for e in evs):
                final = next(e for e in reversed(evs) if e.event.event_type == "deleted")
            # Created + modified → created
            elif evs[0].event.event_type == "created":
                final = evs[0]
            coalesced.append(final)

        # Preserve time order across files
        coalesced.sort(key=lambda e: e.timestamp)
        return coalesced
    
    def _modify_event(self, event: FileSystemEvent) -> FileSystemEvent:
        """
        Modify event paths to be relative to the monitored directory.
        """
        if hasattr(event, 'src_path') and event.src_path is not None and len(event.src_path) > 0:
            event.src_path = os.path.relpath(event.src_path, self._absolute_directory)
        if hasattr(event, 'dest_path') and event.dest_path is not None and len(event.dest_path) > 0:
            event.dest_path = os.path.relpath(event.dest_path, self._absolute_directory)
    
    def _enqueue_event(self, event: FileSystemEvent) -> None:
        """
        Enqueue a filesystem event for processing unless it is suppressed."""
        logger.debug(f"Enqueueing: {event}")
        if event.src_path in self._suppressed_paths:
            logger.debug(f"suppressed event {event.src_path}")
            return
        
        qe = QueuedEvent(
            timestamp=time.time(),
            event = event)
        self._event_queue.put(qe)
    
    """
    Watchdog Event Handlers.
    Modifity event paths to be relative to monitored directory, then enqueue the event for processing. 
    """
    def on_any_event(self, event):
        pass

    def on_created(self, event):
        self._modify_event(event)
        self._enqueue_event(event)

    def on_deleted(self, event):
        self._modify_event(event)
        self._enqueue_event(event)

    def on_modified(self, event):
        self._modify_event(event)
        self._enqueue_event(event)
        
    def on_moved(self, event):
        self._modify_event(event)
        self._enqueue_event(event)
