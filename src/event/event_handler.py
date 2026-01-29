import os
import logging
import time
import shutil
from concurrent.futures import ThreadPoolExecutor, Future, wait, as_completed

from datetime import datetime, timedelta
from watchdog.events import RegexMatchingEventHandler
from watchdog.events import FileSystemEvent, FileCreatedEvent, FileModifiedEvent, FileMovedEvent, FileDeletedEvent, DirCreatedEvent, DirModifiedEvent, DirDeletedEvent, DirMovedEvent
from queue import Queue, Empty
from dataclasses import dataclass
from pyicloud.services.drive import DriveNode

from context import Context
from model.base_tree import BaseTree
from model.icloud_tree import iCloudTree
from model.local_tree import LocalTree
from model.file_info import FolderInfo, LocalFileInfo, LocalFolderInfo, iCloudFileInfo, iCloudFolderInfo
from model.action_result import UploadActionResult, DownloadActionResult
from event.icloud_event import iCloudFolderModifiedEvent

logger = logging.getLogger(__name__)  # __name__ is a common choice

@dataclass
class QueuedEvent:
    timestamp: float
    event: FileSystemEvent

class EventHandler(RegexMatchingEventHandler):

    def __init__(self, ctx: Context):
        self.ctx = ctx
        self._absolute_directory = os.path.realpath(os.path.normpath(ctx.directory))
        self._local = LocalTree(ctx=ctx)
        for s in self._local.ignores_patterns:
            logger.info(f"ignore local: {s}")
        for s in self._local.includes_list:
            logger.info(f"include local: {s}")

        self._icloud = iCloudTree(ctx=ctx)
        for s in self._icloud.ignores_patterns:
            logger.info(f"ignore icloud: {s}")
        for s in self._icloud.includes_list:
            logger.info(f"include icloud: {s}")

        super().__init__(regexes=None, ignore_regexes=self._local.ignores_patterns, ignore_directories=False, case_sensitive=False)
        self._queue = Queue()
        self._exception_events = set()
        self._suppressed_paths = set()
        self._threadpool = ThreadPoolExecutor()
        self._upload_threadpool = ThreadPoolExecutor(max_workers=ctx.upload_workers)
        self._download_threadpool = ThreadPoolExecutor(max_workers=ctx.download_workers)
        self._pending_futures = set()
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
        icloud_check_period: timedelta = self.ctx.icloud_check_period
        retry_period: timedelta = self.ctx.retry_period
        icloud_refresh_period: timedelta = self.ctx.icloud_refresh_period
        retry_dt: datetime = datetime.now() - retry_period
        refresh_dt: datetime = datetime.now()
        refresh: iCloudTree = None
        refresh_future: Future = None
        root_has_changed: bool = False
        trash_has_changed: bool = False
        event_collector: list[QueuedEvent] = []

        self._local.refresh()
        if self._icloud.refresh():
            self._dump_state(local=self._local, icloud=self._icloud)
            self._sync_local_to_icloud()
            self._sync_icloud(self._local, self._icloud)
            self._sync_common(self._local, self._icloud)
            self._delete_icloud_trash_items()

        logger.info("Starting event handler main loop...")
        while True:
            try:
                while True:
                    event: QueuedEvent = self._queue.get(block=True, timeout=10)
                    event_collector.append(event)
            except Empty:
                # If the background refresh is not running, we can process events
                if not refresh_future:
                    self._dispatch_events(event_collector=event_collector)
                    self._process_pending_futures()
                    if not self._pending_futures:
                        # No pending futures, we can clear suppressed paths
                        self._suppressed_paths.clear()
                        if datetime.now() - retry_dt > retry_period:
                            self._retry_exception_events()
                            retry_dt = datetime.now()
                        if datetime.now() - refresh_dt > icloud_check_period:
                            root_changed_future: Future = None if root_has_changed else self._threadpool.submit(self._icloud.root_has_changed)
                            trash_changed_future: Future = None if trash_has_changed else self._threadpool.submit(self._icloud.trash_has_changed)
                            wait([f for f in [root_changed_future, trash_changed_future] if f is not None])
                            root_has_changed = root_changed_future.result() if root_changed_future else root_has_changed
                            trash_has_changed = trash_changed_future.result() if trash_changed_future else trash_has_changed
                            if datetime.now() - refresh_dt > icloud_refresh_period or root_has_changed or trash_has_changed:
                                if datetime.now() - refresh_dt > icloud_refresh_period:
                                    logger.debug("refresh period elapsed")
                                if root_has_changed:
                                    logger.debug(f"root has changed")
                                if trash_has_changed:
                                    logger.debug("trash has changed")
                                refresh = iCloudTree(ctx=self.ctx)
                                refresh_future = self._threadpool.submit(refresh.refresh)

                # If the background refresh is done, apply it
                elif refresh_future.done():
                    refresh_dt = datetime.now() 
                    result = refresh_future.result()
                    if result:
                        if not self._pending_futures and not event_collector:
                            self._dump_state(local=self._local, icloud=self._icloud)
                            logger.debug("No pending futures or events, proceeding with applying refresh")
                            uploaded, downloaded, deleted, folders_created = self._apply_icloud_refresh(refresh)
                            if any([uploaded, downloaded, deleted, folders_created]):
                                logger.info(f"Background refresh applied, {uploaded} uploaded, {downloaded} downloaded, {deleted} deleted, {folders_created} folders created")
                            else:
                                logger.info(f"Background refresh, no changes")
                            self._dump_state(local=self._local, icloud=self._icloud, refresh=refresh)
                            self._icloud = refresh
                            root_has_changed = trash_has_changed = False
                            icloud_refresh_period = self.ctx.icloud_refresh_period
                        else:
                            logger.warning(f"Background refresh discarded due to pending futures or events, will retry in {icloud_refresh_period}")
                    else:
                        if not(root_has_changed or trash_has_changed):
                            icloud_refresh_period = min(self.ctx.icloud_refresh_period * 6, icloud_refresh_period + self.ctx.icloud_refresh_period)
                        logger.warning(f"Background refresh was inconsisent, will retry in {icloud_refresh_period}")
                    refresh_future = None

    def _dispatch_events(self, event_collector):
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
        if not self._pending_futures:
            return
        logger.debug(f"Processing {len(self._pending_futures)} pending futures...")
        for _ in set(self._pending_futures):
            done, self._pending_futures = as_completed(self._pending_futures), set()
            for future in done:
                result = future.result()
                if isinstance(result, list) and all(isinstance(f, future) for f in result):
                    self._pending_futures.update(result)
                elif isinstance(result, DownloadActionResult):
                    if not result.success:
                        logger.error(f"Download failed for {result.path} with Exception {result.exception}")
                    else:
                        logger.debug(f"Download succeeded for {result.path}")
                elif isinstance(result, UploadActionResult):
                    if not result.success:
                        logger.error(f"Upload failed for {result.path} with Exception {result.exception}")
                    else:
                        logger.debug(f"Upload succeeded for {result.path}")
                        self._enqueue_event(iCloudFolderModifiedEvent(src_path=os.path.normpath(os.path.dirname(result.path))))

    def _dump_state(self, local: LocalTree, icloud: iCloudTree, refresh: iCloudTree=None):
        filename = "_before.log"
        if refresh is not None:
            filename = "_after.log"

        for tree, name in [(local, "local"), (icloud, "icloud"), (refresh, "refresh")]:
            if tree is not None:
                with open(os.path.join(self.ctx.log_path, "icloudds_"+name+filename), 'w') as f:
                    sorted_dict = dict(sorted(tree.root.items()))
                    for k, v in sorted_dict.items():
                        f.write(f"{k}: {v!r}\n")

    def _apply_icloud_refresh(self, refresh: iCloudTree) -> tuple[int, int, int, int]:
        downloaded_created: tuple[int, int] = self._sync_icloud(self._icloud, refresh)
        common: tuple[int, int] = self._sync_common(self._icloud, refresh)
        deleted = self._icloud.root.keys() - refresh.root.keys()
        for path in deleted:
            self._delete_local_file(path)
        return (common[0], downloaded_created[0] + common[1], len(deleted), downloaded_created[1])

    def _delete_icloud_trash_items(self) -> int:
        deleted_count = 0
        for name in self._icloud.trash:
            if name == BaseTree.ROOT_FOLDER_NAME:
                continue
            path = self._icloud.trash[name].node.data.get("restorePath")
            if path:
                self._delete_local_file(path)
                lfi = self._local.root.get(path, None)
                if lfi and isinstance(lfi, LocalFileInfo):
                    deleted_count += 1
        return deleted_count

    def _delete_local_file(self, path: str):
        self._suppressed_paths.add(path)
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

    def _sync_local_to_icloud(self) -> int:
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

    def _sync_icloud(self, these: LocalTree | iCloudTree, those: iCloudTree) -> tuple[int, int]:
        left = "Local" if isinstance(these, LocalTree) else "iCloud"
        right = "Refresh" if left == "iCloud" else "iCloud"
        downloaded_count = 0
        folder_created_count = 0
        in_icloud = those.root.keys() - these.root.keys()
        for path in in_icloud:
            if self._local.ignore(path, isinstance(those.root[path], iCloudFolderInfo)):
                continue
            self._suppressed_paths.add(path)
            cfi = those.root[path]
            try:
                if isinstance(cfi, iCloudFolderInfo):
                    if not os.path.exists(os.path.join(self._local.root_path, path)):
                        logger.info(f"{right} {path} is missing locally, creating folders...")
                        os.makedirs(os.path.join(self._local.root_path, path), exist_ok=True)
                        folder_created_count += 1
                else:
                    logger.info(f"{right} {path} is missing locally, downloading to Local...")
                    self._pending_futures.add(self._download_threadpool.submit(self._icloud.download, path, cfi, self._local.add))
                    downloaded_count += 1
            except Exception as e:
                logger.error(f"iCloud Drive download failed for {path}: {e}")
                self._icloud.handle_drive_exception(e)
        return downloaded_count, folder_created_count

    def _sync_common(self, these: LocalTree | iCloudTree, those: iCloudTree) -> tuple[int, int]:
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
                    logger.info(f"{right} is newer for {path}, downloading to Local...")
                    self._suppressed_paths.add(path)
                    self._pending_futures.add(self._download_threadpool.submit(those.download, path, right_fi, self._local.add))
                    downloaded_count += 1
            else:   
                if left_fi.size != right_fi.size:
                    logger.debug(f"Different size in both: {path} -> {left}: {left_fi} | {right}: {right_fi}")
        return (uploaded_count, downloaded_count)

    def _retry_exception_events(self) -> None:
        if self._exception_events:
            logger.debug(f"Reprocessing {len(self._exception_events)} events...")
            for event in list(self._exception_events):
                logger.debug(f"Reprocessing event: {event}")
                self._exception_events.remove(event)
                self._event_table.get(type(event), lambda e: logger.debug(f"Unhandled event {e}"))(event)

    def _handle_icloud_folder_modified_event(self, event: iCloudFolderModifiedEvent) -> None:
        self._pending_futures.add(self._threadpool.submit(self._icloud.process_folder, root=self._icloud.root, path=event.src_path, ignore=True, recursive=False, executor=self._threadpool))
    
    def _handle_file_created_event(self, event: FileCreatedEvent) -> None:
        self._handle_file_modified_event(event)

    def _handle_file_modified_event(self, event: FileModifiedEvent) -> None:
        parent_path: str = os.path.normpath(os.path.dirname(event.src_path))
        lfi: LocalFileInfo = self._local.add(event.src_path)

        parent: iCloudFolderInfo = self._icloud.root.get(parent_path, None)
        cfi: iCloudFileInfo = self._icloud.root.get(event.src_path, None)

        if cfi is not None:
            child_node: DriveNode = cfi.node
            parent_node: DriveNode = parent.node
            if lfi.modified_time > cfi.modified_time and lfi.size > 0:
                logger.debug(f"Local file {event.src_path} modified/created, iCloud Drive file {event.src_path} is outdated")
                # Delete the existing file from iCloud Drive
                try:
                    child_node.delete()
                    # Remove the node from pyicloud
                    parent_node.remove(child_node)
                except ValueError as e:
                    pass
                except Exception as e:
                    logger.error(f"Local file {event.src_path} modified/created, iCloud Drive remove node failed for {event.src_path}: {e}")
                    self._icloud.handle_drive_exception(e)
                # Remove the file from the iCloud tree
                self._icloud.root.pop(event.src_path)
            else:
                logger.debug(f"iCloud Drive file {event.src_path} is up to date, skipping upload...")
                return
        # cfi is None
        try:
            # Create parent folders as needed
            logger.debug(f"Local file {event.src_path} modified/created, creating folders for {parent_path}...")
            parent = self._icloud.create_icloud_folders(parent_path)
            lfi = self._local.root[event.src_path]
            cfi = self._icloud.root.get(event.src_path, None)
            if cfi is None or lfi.modified_time > cfi.modified_time and lfi.size > 0:
                if isinstance(lfi, LocalFileInfo):
                    logger.info(f"Local file {event.src_path} modified/created, uploading to iCloud Drive...")
                    self._pending_futures.add(self._upload_threadpool.submit(self._icloud.upload, event.src_path, lfi))
        except Exception as e:
            logger.error(f"iCloud Drive upload failed for {event.src_path}: {e}")
            self._icloud.handle_drive_exception(e)
            self._exception_events.add(event)

    def _handle_file_moved_event(self, event: FileMovedEvent) -> None:
        parent_path: str = os.path.normpath(os.path.dirname(event.src_path))
        dest_parent_path: str = os.path.normpath(os.path.dirname(event.dest_path))
        cfi: iCloudFileInfo | iCloudFolderInfo= self._icloud.root.get(event.src_path, None)

        logger.debug(f"Local {'file' if isinstance(cfi, iCloudFileInfo) else 'folder'} {event.src_path} renamed to {event.dest_path}")
        if parent_path == dest_parent_path:
            logger.info(f"iCloud Drive renaming {'file' if isinstance(cfi, iCloudFileInfo) else 'folder'} {event.src_path} to {event.dest_path}, as parent is the same")
            # Remove the file from the iCloud and local trees
            self._icloud.root.pop(event.src_path)
            self._local.root.pop(event.src_path)
            # Add the file back with the new name
            try:
                cfi.node.rename(os.path.basename(event.dest_path))
                self._enqueue_event(iCloudFolderModifiedEvent(src_path=parent_path))
                self._local.add(event.dest_path)
            except Exception as e:
                logger.error(f"iCloud Drive rename failed for {event.src_path} to {event.dest_path}: {e}")
                self._icloud.handle_drive_exception(e)
                self._exception_events.add(event)
        else:
            # Moving to a different folder is not supported by pyicloud, treat as delete + create
            logger.info(f"iCloud Drive moving {'file' if isinstance(cfi, iCloudFileInfo) else 'folder'} {event.src_path} to {event.dest_path} as parent is different")
            if isinstance(cfi, iCloudFileInfo):
                de = FileDeletedEvent(src_path=event.src_path)
                ce = FileCreatedEvent(src_path=event.dest_path)
            else:
                de = DirDeletedEvent(src_path=event.src_path)
                ce = DirCreatedEvent(src_path=event.dest_path)
            self._handle_file_deleted_event(de)
            self._handle_file_created_event(ce)

    def _handle_file_deleted_event(self, event: FileDeletedEvent) -> None:
        parent_path: str = os.path.normpath(os.path.dirname(event.src_path))
        parent: iCloudFolderInfo = self._icloud.root.get(parent_path, None)
        cfi: iCloudFileInfo | iCloudFolderInfo= self._icloud.root.get(event.src_path, None)
        if parent is not None and cfi is not None:
            parent_node: DriveNode = parent.node
            child_node: DriveNode = cfi.node

            try:
                # Delete the file from iCloud Drive
                logger.info(f"Local {'file' if isinstance(cfi, iCloudFileInfo) else 'folder'} {event.src_path} deleted, deleting iCloud Drive item")
                result = child_node.delete()
                logger.debug(f"iCloud Drive deleted result: {result}")

                # Remove the node from pyicloud 
                parent_node.remove(child_node)

                # Remove the file from the iCloud and local trees
                self._icloud.root.pop(event.src_path)
                self._local.root.pop(event.src_path)
            except Exception as e:
                logger.error(f"iCloud Drive delete failed for {event.src_path}: {e}")
                self._icloud.handle_drive_exception(e)
                self._exception_events.add(event)
        else:
            pass
            #logger.debug(f"iCloud Drive not deleting {'file' if isinstance(cfi, iCloudFileInfo) else 'folder'} {event.src_path} {parent} {cfi}")

    def _handle_dir_created_event(self, event: DirCreatedEvent) -> None:
        parent_path: str = os.path.normpath(os.path.dirname(event.src_path))
        self._local.add(event.src_path)
        parent: iCloudFolderInfo = self._icloud.root.get(parent_path, None)
        cfi: iCloudFileInfo = self._icloud.root.get(event.src_path, None)

        try:
            if parent is None:
                # Create parent folders as needed
                parent = self._icloud.create_icloud_folders(parent_path)
            if cfi is None:
                parent_node: DriveNode = parent.node
                logger.info(f"Local folder {event.src_path} created, iCloud Drive creating folder {event.src_path}...")
                parent_node.mkdir(os.path.basename(event.src_path))
                self._enqueue_event(iCloudFolderModifiedEvent(src_path=parent_path))
            else:
                logger.debug(f"iCloud Drive folder {event.src_path} already exists, skipping creation...")
        except Exception as e:
            logger.error(f"iCloud Drive create folder failed for {event.src_path}: {e}")
            self._icloud.handle_drive_exception(e)
            self._exception_events.add(event)

    def _handle_dir_modified_event(self, event: DirModifiedEvent) -> None:
        return

    def _handle_dir_moved_event(self, event: DirMovedEvent) -> None:
        self._handle_file_moved_event(event)
        return
        
    def _handle_dir_deleted_event(self, event: DirDeletedEvent) -> None:
        self._handle_file_deleted_event(event)
        return
    
    def _coalesce_events(self, events: list[QueuedEvent]) -> list[QueuedEvent]:
        """
        Coalesce events per file while preserving global time order.
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
        if hasattr(event, 'src_path') and event.src_path is not None and len(event.src_path) > 0:
            event.src_path = os.path.relpath(event.src_path, self._absolute_directory)
        if hasattr(event, 'dest_path') and event.dest_path is not None and len(event.dest_path) > 0:
            event.dest_path = os.path.relpath(event.dest_path, self._absolute_directory)
    
    def _enqueue_event(self, event: FileSystemEvent) -> None:
        logger.debug(f"Enqueueing: {event}")
        if event.src_path in self._suppressed_paths:
            try:
                lfi = LocalFileInfo(event.src_path, os.stat(os.path.join(self.ctx.directory, event.src_path)))
                logger.debug(f"Suppressed event for {lfi}")
            except Exception as e:
                logger.debug(f"suppressed event {event.src_path}")
            return
        
        qe = QueuedEvent(
            timestamp=time.time(),
            event = event)
        self._queue.put(qe)
    
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
