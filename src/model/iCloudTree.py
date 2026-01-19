import os
import logging
from typing import Callable, override 
from urllib3 import disable_warnings
from urllib3.exceptions import InsecureRequestWarning
from concurrent.futures import ThreadPoolExecutor, Future, as_completed
import threading
from threading import Lock
import pyicloud.services.drive
from pyicloud.services.drive import DriveNode, CLOUD_DOCS_ZONE_ID_ROOT, CLOUD_DOCS_ZONE_ID_TRASH
from pyicloud.exceptions import PyiCloudAPIResponseException
disable_warnings(category=InsecureRequestWarning)

from context import Context
import constants as constants
from icloud.authenticate import authenticate
from model.BaseTree import BaseTree
from model.FileInfo import BaseInfo, LocalFileInfo, iCloudFileInfo, iCloudFolderInfo
from model.ActionResult import DownloadActionResult, UploadActionResult

logger = logging.getLogger(__name__)

class iCloudTree(BaseTree):
    def __init__(self, ctx: Context):
        self.drive: pyicloud.services.drive.DriveService = None
        self._trash: dict[str, BaseInfo] = {}
        self._is_authenticated: bool = False
        self.ctx = ctx
        self._lock = Lock()
        self._trashlock = Lock()
        super().__init__(root_path=ctx.directory, ignores=ctx.ignore_icloud, includes=ctx.include_icloud)

    def authenticate(self) -> None:
        if self._is_authenticated:
            return
        api = authenticate(username=self.ctx.username, password=self.ctx.password, cookie_directory=self.ctx.cookie_directory, raise_authorization_exception=False, client_id=None, unverified_https=True)
        self.drive = api.drive
        self._is_authenticated = True

    @override
    def refresh(self, root=None, path=None, force=True) -> None:
        succeeded = True
        try:
            self.authenticate()
            logger.info(f"Refreshing iCloud Drive {self.ctx.username}::{self.drive.service_root}...")
            with ThreadPoolExecutor(os.cpu_count()*4) as executor:
                pending = set()
                for (root, name) in [(self._root, "root"), (self._trash, "trash")]:
                    logger.debug(f"Refreshing iCloud Drive {name}...")
                    future = executor.submit(self.process_folder, root=root, name=name, path=None, force=True, recursive=True, ignore=False, executor=executor)
                    pending = pending | set([future])
                while pending:
                    done, pending = as_completed(pending), set()
                    for future in done:
                        new_futures = future.result()
                        pending.update(new_futures)
            if self.root_count != sum(1 for _ in self.files(self._root)) + sum(1 for _ in self.files(self._trash)):
                raise Exception("Mismatch in counts")
            
        except Exception as e:
            self.handle_drive_exception(e)
            succeeded = False
            logger.error(f"iCloud Drive exception in refresh: {e}")

        logger.debug(f"Refresh iCloud Drive complete root has {len(self._root)} items, root count {self.root_count}, {sum(1 for _ in self.folders(self._root))} folders, {sum(1 for _ in self.files(self._root))} files")
        logger.debug(f"Refresh iCloud Drive complete trash has {len(self._trash)} items, trash count {self.trash_count}, {sum(1 for _ in self.folders(self._trash))} folders, {sum(1 for _ in self.folders(self._trash))} files")
        self._remove_ignored_items()
        return succeeded


    @override
    def add(self, path) -> iCloudFileInfo | iCloudFolderInfo:
        parent_path = os.path.dirname(path)
        folder_path = BaseTree.ROOT_FOLDER_NAME
        for folder_name in parent_path.split(os.sep):
            folder_path = os.path.normpath(os.path.join(folder_path, folder_name))
            self._root[folder_path] = iCloudFolderInfo(name=folder_name, stat_entry=os.stat(os.path.join(self._root_path, folder_path)))

        stat_entry = os.stat(os.path.join(self._root_path, path))
        if stat_entry.is_file():
            self._root[path] = iCloudFileInfo(name=os.path.basename(path), stat_entry=stat_entry)
        elif stat_entry.is_dir():
            self._root[path] = iCloudFolderInfo(name=os.path.basename(path), stat_entry=stat_entry)
        return self._root[path]

    @property
    def trash(self) -> dict:
        return self._trash

    def process_folder(self, root=None, name=None, path=None, force=False, recursive=True, ignore=True, executor=None) -> None|list[Future]:
        if name == "root":
            lock = self._lock
            if self._root == {}:
                logger.debug(f"resolving iCloud Drive {name}")
                self._root[BaseTree.ROOT_FOLDER_NAME] = iCloudFolderInfo(self.drive.root)
        elif name == "trash":
            lock = self._trashlock
            if self._trash == {}:
                logger.debug(f"resolving iCloud Drive {name}")
                self._trash[BaseTree.ROOT_FOLDER_NAME] = iCloudFolderInfo(self.drive.trash)

        path = BaseTree.ROOT_FOLDER_NAME if path is None or path == "" else path
        relative_path = os.path.normpath(path)
        futures = []
        threadname = threading.current_thread().name
        if executor:
            threading.current_thread().name = f"process_folder {name} {path}"

        children = root[path].node.get_children(force=force)
        for child in children:
            _path = os.path.normpath(os.path.join(relative_path, child.name))
            if ignore and self.ignore(_path, True):
                continue
            if child.type == "folder":
                    logger.debug(f"iCloud Drive {name} {_path}")
                    with lock:
                        root[_path] = iCloudFolderInfo(child)
                    if recursive:
                        if executor is not None:
                            future = executor.submit(self.process_folder, root, name, _path, force, recursive, ignore, executor)
                            futures.append(future)
                        else:
                            self.process_folder(root, _path, force, recursive, ignore, executor)
            elif child.type == "file":
                logger.debug(f"iCloud Drive {name} {_path}")
                with lock:
                    cfi = root[_path] = iCloudFileInfo(child)
                    # Update parent folder modified time to be that of the newest child (not stored in iCloud Drive)
                    root[relative_path].modified_time = cfi.modified_time if cfi.modified_time > root[relative_path].modified_time else root[relative_path].modified_time
            else:
                logger.debug(f"iCloud Drive {name} did not process {child.type} {os.path.join(relative_path, child.name)}")
    
        threading.current_thread().name = threadname
        return futures

    def _remove_ignored_items(self):
        for root in [self._root, self._trash]:
            for path in list(root):
                if self.ignore(path, isinstance(root[path], iCloudFolderInfo)):
                    root.pop(path)

    def upload(self, path: str, lfi: LocalFileInfo) -> UploadActionResult:
        try:
            parent_path: str = os.path.normpath(os.path.dirname(path))

            parent_node: DriveNode = self._root[parent_path].node
            with open(os.path.join(self._root_path, path), 'rb') as f:
                parent_node.upload(f, mtime=lfi.modified_time.timestamp(), ctime=lfi.created_time.timestamp())
            return UploadActionResult(success=True)
        except Exception as e:
            self.handle_drive_exception(e)
            return UploadActionResult(success=False, fn=self.upload, args=[str, lfi], exception=e)

    def download(self, path: str, cfi: iCloudFileInfo, apply_after: Callable[[str], str]) -> DownloadActionResult:
        try:
            file_path = os.path.join(self._root_path, os.path.normpath(path))
            parent_path: str = os.path.normpath(os.path.dirname(path))
            os.makedirs(os.path.join(self._root_path, parent_path), exist_ok=True)
            with cfi.node.open(stream=True) as response:
                with open(file_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=constants.DOWNLOAD_MEDIA_CHUNK_SIZE):
                        if chunk:
                            f.write(chunk)
                            f.flush()

            os.utime(file_path, (cfi.modified_time.timestamp(), cfi.modified_time.timestamp()))
            apply_after(path)
            return DownloadActionResult(success=True)

        except Exception as e:
            self.handle_drive_exception(e)
            return DownloadActionResult(success=False, fn=self.upload, args=[path, cfi, apply_after], exception=e)

    def create_icloud_folders(self, path: str) -> iCloudFolderInfo:
        try:
            folder_path = BaseTree.ROOT_FOLDER_NAME
            _path = folder_path
            parent = self._root[folder_path]
            parent_node: DriveNode = parent.node
            for folder_name in path.split(os.sep):
                folder_path = os.path.normpath(os.path.join(folder_path, folder_name))
                if folder_path not in self._root:
                    logger.info(f"iCloud Drive creating parent folder {folder_path}...")
                    parent_node.mkdir(folder_name)
                    self.process_folder(root=self._root, path=_path, force=True, recursive=False)
                    parent = self._root[folder_path]
                    parent_node = parent.node
                    _path = folder_path
                else:
                    parent_node = self._root[folder_path].node
                    _path = folder_path
            parent = self._root[_path]
            return parent
        except Exception as e:
            self.handle_drive_exception(e)

    @property
    def root_count(self) -> int:
        if self._root is None:
            return 0
        return self._root[BaseTree.ROOT_FOLDER_NAME].node.data.get('fileCount')
    
    def has_root_filecount_changed(self) -> bool:
        name = threading.current_thread().name
        threading.current_thread().name = "check root change"
        pre_count: int = 0
        post_count: int = 0
        try:
            pre_count = self.root_count
            logger.debug(f"iCloud Drive root has {pre_count} files before refresh")
            post_count = self.drive.get_node_data(CLOUD_DOCS_ZONE_ID_ROOT).get('fileCount')
            self.drive._root.data['fileCount'] = post_count
            logger.debug(f"iCloud Drive root has {post_count} files after refresh")
        except Exception as e:
            logger.warning(f"iCloud Drive get fileCount failed: {e}")
            return False
        finally:
            threading.current_thread().name = name
        return pre_count != post_count            
    
    @property
    def trash_count(self) -> int:
        if self._trash is None:
            return 0
        return self._trash[BaseTree.ROOT_FOLDER_NAME].node.data.get('numberOfItems')
    
    def has_trash_numberOfItems_changed(self) -> bool:
        name = threading.current_thread().name
        threading.current_thread().name = "check trash change"
        pre_count: int = 0
        post_count: int = 0
        try:
            pre_count = self.trash_count
            logger.debug(f"iCloud Drive trash has {pre_count} items before refresh")
            post_count = self.drive.get_node_data(CLOUD_DOCS_ZONE_ID_TRASH).get('numberOfItems')
            self.drive._trash.data['numberOfItems'] = post_count
            logger.debug(f"iCloud Drive trash has {post_count} items after refresh")
        except Exception as e:
            logger.warning(f"iCloud Drive trash refresh failed: {e}")
            return False
        finally:
            threading.current_thread().name = name
        return pre_count != post_count

    def handle_drive_exception(self, e: Exception) -> None:
        match e:
            case PyiCloudAPIResponseException():
                logger.error(f"iCloud Drive exception: {e}")
                self._is_authenticated = False
            case _:
                logger.error(f"iCloud Drive unhandled exception: {e}")
    