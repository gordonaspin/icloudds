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
from model.BaseTree import BaseTree
from model.iCloudTree import iCloudTree
from model.LocalTree import LocalTree
from model.FileInfo import FolderInfo, LocalFileInfo, LocalFolderInfo, iCloudFileInfo, iCloudFolderInfo
from model.ActionResult import UploadActionResult, DownloadActionResult

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
        self._pending = set()
        self._event_table = {
            FileCreatedEvent: self._handle_file_created,
            FileModifiedEvent: self._handle_file_modified,
            FileMovedEvent: self._handle_file_moved,
            FileDeletedEvent: self._handle_file_deleted,
            DirCreatedEvent: self._handle_dir_created,
            DirModifiedEvent: self._handle_dir_modified,
            DirMovedEvent: self._handle_dir_moved,
            DirDeletedEvent: self._handle_dir_deleted,
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

        self._local.refresh()
        if self._icloud.refresh():
            self._dump_state(local=self._local, icloud=self._icloud)
            self._sync_local_to_icloud()
            self._sync_icloud(self._local, self._icloud)
            self._sync_common(self._local, self._icloud)
            self._delete_icloud_trash_items()

        logger.info("Starting event handler main loop...")
        while True:
            event_collector: list[QueuedEvent] = []
            try:
                while True:
                    event: QueuedEvent = self._queue.get(block=True, timeout=10)
                    logger.debug(f"Dequeueing: {event}")
                    event_collector.append(event)
            except Empty:
                self._suppressed_paths.clear()
                self._dispatch_events(event_collector=event_collector)
                self._process_pending()
                if datetime.now() - retry_dt > retry_period:
                    self._retry_exception_events()
                    retry_dt = datetime.now()

                if refresh_future and refresh_future.done():
                    refresh_dt = datetime.now() 
                    result = refresh_future.result()
                    if result:
                        logger.debug(f"Background refresh complete")
                        self._dump_state(local=self._local, icloud=self._icloud)
                        self._apply_icloud_refresh(refresh)
                        self._dump_state(local=self._local, icloud=self._icloud, refresh=refresh)
                        self._icloud = refresh
                        root_has_changed = trash_has_changed = False
                        icloud_refresh_period = self.ctx.icloud_refresh_period
                    else:
                        if not(root_has_changed or trash_has_changed):
                            icloud_refresh_period = min(self.ctx.icloud_refresh_period * 6, icloud_refresh_period + self.ctx.icloud_refresh_period)
                        logger.debug(f"Background refresh was inconsisent, will retry in {icloud_refresh_period}")
                    refresh_future = None

                if datetime.now() - refresh_dt > icloud_check_period:
                    # Don't check for updates if the refresh thread is running
                    if refresh_future:
                        continue
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

    def _dispatch_events(self, event_collector):
        if not event_collector:
            return
        events: list[QueuedEvent] = self._coalesce_events(event_collector)
        logger.debug(f"Processing {len(events)} coalesced events...")
        for qe in events:
            event: FileSystemEvent = qe.event
            if self._local.ignore(event.src_path, event.is_directory) or self._icloud.ignore(event.src_path, event.is_directory):
                continue
            logger.debug(f"Dispatching {event}")
            self._event_table.get(type(event), lambda e: logger.debug(f"Unhandled event {e}"))(event)
        event_collector.clear()

    def _process_pending(self):
        while self._pending:
            done, self._pending = as_completed(self._pending), set()
            for future in done:
                result = future.result()
                if isinstance(result, list) and all(isinstance(f, future) for f in result):
                    self._pending.update(result)
                elif isinstance(result, DownloadActionResult):
                    if not result.success:
                        logger.debug(f"Download failed for {result.path} with Exception {result.exception}")
                elif isinstance(result, UploadActionResult):
                    if not result.success:
                        logger.debug(f"Upload failed for {result.path} with Exception {result.exception}")

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

    def _apply_icloud_refresh(self, refresh: iCloudTree) -> None:
        self._sync_icloud(self._icloud, refresh)
        self._sync_common(self._icloud, refresh)
        deleted = self._icloud.root.keys() - refresh.root.keys()
        for path in deleted:
            self._delete_local_file(path)

    def _delete_icloud_trash_items(self):
        for name in self._icloud.trash:
            if name == BaseTree.ROOT_FOLDER_NAME:
                continue
            path = self._icloud.trash[name].node.data.get("restorePath")
            if path:
                self._delete_local_file(path)

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

    def _sync_local_to_icloud(self) -> None:
        only_in_local = self._local.root.keys() - self._icloud.root.keys()        
        for path in only_in_local:
            if self._icloud.ignore(path, isinstance(self._local.root[path], LocalFolderInfo)):
                continue
            logger.debug(f"Only in local: {path} -> {self._local.root[path]}")
            if isinstance(self._local.root[path], LocalFolderInfo):
                self._handle_dir_created(DirCreatedEvent(src_path=path))
            else:
                self._handle_file_modified(FileModifiedEvent(src_path=path))

    def _sync_icloud(self, these: LocalTree | iCloudTree, those: iCloudTree) -> None:
        left = "Local" if isinstance(these, LocalTree) else "iCloud"
        right = "Refresh" if left == "iCloud" else "iCloud"
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
                else:
                    logger.info(f"{right} {path} is missing locally, downloading to Local...")
                    self._pending.add(self._threadpool.submit(self._icloud.download, path, cfi, self._local.add))
            except Exception as e:
                logger.error(f"iCloud Drive download failed for {path}: {e}")
                self._icloud.handle_drive_exception(e)

    def _sync_common(self, these: LocalTree | iCloudTree, those: iCloudTree) -> None:
        left = "Local" if isinstance(these, LocalTree) else "iCloud"
        right = "Refresh" if left == "iCloud" else "iCloud"
        in_common = these.root.keys() & those.root.keys()
        for path in in_common:
            lfi = these.root[path]
            cfi = those.root[path]
            if isinstance(lfi, FolderInfo) and isinstance(cfi, FolderInfo):
                continue
            if lfi.modified_time != cfi.modified_time:
                logger.debug(f"Different time in both: {path} -> {left}: {lfi} | {right}: {cfi}")
                # upload if the left file instance is newer and is a local file
                # ignore otherwise when the refresh missed an update
                if lfi.modified_time > cfi.modified_time and isinstance(lfi, LocalFileInfo):
                    logger.debug(f"{left} is newer for {path}, uploading to iCloud Drive...")
                    self._handle_file_modified(FileModifiedEvent(src_path=path))
                elif lfi.modified_time < cfi.modified_time:
                    logger.info(f"{right} is newer for {path}, downloading to Local...")
                    self._suppressed_paths.add(path)
                    self._pending.add(self._threadpool.submit(those.download, path, cfi, self._local.add))
            else:   
                if lfi.size != cfi.size:
                    logger.debug(f"Different size in both: {path} -> {left}: {lfi} | {right}: {cfi}")

    def _retry_exception_events(self) -> None:
        if self._exception_events:
            logger.debug(f"Reprocessing {len(self._exception_events)} events...")
            for event in list(self._exception_events):
                logger.debug(f"Reprocessing event: {event}")
                self._exception_events.remove(event)
                self._event_table.get(type(event), lambda e: logger.debug(f"Unhandled event {e}"))(event)

    def _handle_file_created(self, event: FileCreatedEvent) -> None:
        self._handle_file_modified(event)

    def _handle_file_modified(self, event: FileModifiedEvent) -> None:
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
                    self._icloud.upload(event.src_path, lfi)
                self._icloud.process_folder(root=self._icloud.root, path=parent_path, force=True, recursive=False)
        except Exception as e:
            logger.error(f"iCloud Drive upload failed for {event.src_path}: {e}")
            self._icloud.handle_drive_exception(e)
            self._exception_events.add(event)

    def _handle_file_moved(self, event: FileMovedEvent) -> None:
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
                self._pending.update(self._icloud.process_folder(root=self._icloud.root, path=dest_parent_path, force=True, executor=self._threadpool))
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
            self._handle_file_deleted(de)
            self._handle_file_created(ce)

    def _handle_file_deleted(self, event: FileDeletedEvent) -> None:
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

    def _handle_dir_created(self, event: DirCreatedEvent) -> None:
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
                self._icloud.process_folder(root=self._icloud.root, path=parent_path, force=True, recursive=False)
            else:
                logger.debug(f"iCloud Drive folder {event.src_path} already exists, skipping creation...")
        except Exception as e:
            logger.error(f"iCloud Drive create folder failed for {event.src_path}: {e}")
            self._icloud.handle_drive_exception(e)
            self._exception_events.add(event)

    def _handle_dir_modified(self, event: DirModifiedEvent) -> None:
        return

    def _handle_dir_moved(self, event: DirMovedEvent) -> None:
        self._handle_file_moved(event)
        return
        
    def _handle_dir_deleted(self, event: DirDeletedEvent) -> None:
        self._handle_file_deleted(event)
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
        self._modify_event(event)
        if event.src_path in self._suppressed_paths:
            logger.debug(f"Suppressed event for path: {event.src_path}")
            return
        
        qe = QueuedEvent(
            timestamp=time.time(),
            event = event)
        self._queue.put(qe)
    
    def on_any_event(self, event):
        pass

    def on_created(self, event):
        logger.debug(f"Enqueueing: {event}")
        self._enqueue_event(event)

    def on_deleted(self, event):
        logger.debug(f"Enqueueing: {event}")
        self._enqueue_event(event)

    def on_modified(self, event):
        logger.debug(f"Enqueueing: {event}")
        self._enqueue_event(event)
        
    def on_moved(self, event):
        logger.debug(f"Enqueueing: {event}")
        self._enqueue_event(event)
