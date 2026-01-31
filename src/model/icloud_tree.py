import os
import logging
import traceback
from typing import Callable, override 
from urllib3 import disable_warnings
from urllib3.exceptions import InsecureRequestWarning
from concurrent.futures import ThreadPoolExecutor, Future, as_completed
import threading
import pyicloud.services.drive
from pyicloud.services.drive import DriveNode, CLOUD_DOCS_ZONE_ID_ROOT, CLOUD_DOCS_ZONE_ID_TRASH
from pyicloud.exceptions import PyiCloudAPIResponseException
disable_warnings(category=InsecureRequestWarning)

from context import Context
import constants as constants
from icloud.authenticate import authenticate
from model.base_tree import BaseTree
from model.file_info import BaseInfo, LocalFileInfo, iCloudFileInfo, iCloudFolderInfo
from model.action_result import DownloadAction, NoAction, UploadAction, DeleteAction, CreateFolderAction, RenameAction
logger = logging.getLogger(__name__)

class iCloudMismatchException(Exception):
    pass

class iCloudTree(BaseTree):
    def __init__(self, ctx: Context):
        self.drive: pyicloud.services.drive.DriveService = None
        self._is_authenticated: bool = False
        self.ctx = ctx
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
            logger.debug(f"Refreshing iCloud Drive {self.ctx.username}::{self.drive.service_root}...")
            with ThreadPoolExecutor(os.cpu_count()*4) as executor:
                pending = set()
                for (_root, icf) in [(self._root, iCloudFolderInfo(self.drive.root)), (self._trash, iCloudFolderInfo(self.drive.trash))]:
                    logger.debug(f"Refreshing iCloud Drive {icf.drivewsid} ...")
                    _root[BaseTree.ROOT_FOLDER_NAME] = icf
                    future = executor.submit(self.process_folder, root=_root, path=BaseTree.ROOT_FOLDER_NAME, recursive=True, ignore=False, executor=executor)
                    pending = pending | set([future])
                while pending:
                    done, pending = as_completed(pending), set()
                    for future in done:
                        new_futures = future.result()
                        pending.update(new_futures)

            root_files_count = sum(1 for _ in self.files(self._root))
            trash_files_count = sum(1 for _ in self.files(self._trash))
            if self.root_count != root_files_count + trash_files_count:
                raise iCloudMismatchException(f"Mismatch root_count: {self.root_count} != root_files_count: {root_files_count} + trash_files_count: {trash_files_count}")

        except Exception as e:
            self.handle_drive_exception(e)
            succeeded = False

        logger.debug(f"Refresh iCloud Drive complete root has {len(self._root)} items, root count {self.root_count}, {sum(1 for _ in self.folders(self._root))} folders, {sum(1 for _ in self.files(self._root))} files")
        logger.debug(f"Refresh iCloud Drive complete trash has {len(self._trash)} items, trash count {self.trash_count}, {sum(1 for _ in self.folders(self._trash))} folders, {sum(1 for _ in self.files(self._trash))} files")
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
        return self._root.get(path, None)

    def process_folder(self, root=None, path=None, recursive=False, ignore=True, executor=None) -> None|list[Future]:
        threadname = threading.current_thread().name
        if executor:
            threading.current_thread().name = f"process_folder {"root" if root is self._root else "trash"} {path}"

        relative_path = os.path.normpath(path)
        futures = []
        children = root[path].node.get_children(force=True)
        for child in children:
            _path = os.path.normpath(os.path.join(relative_path, child.name))
            if ignore and self.ignore(_path, True):
                continue
            if child.type == "folder":
                    cfi = root[_path] = iCloudFolderInfo(child)
                    logger.debug(f"iCloud Drive {"root" if root is self._root else "trash"} {_path} {cfi}")
                    if recursive:
                        if executor is not None:
                            future = executor.submit(self.process_folder, root=root, path=_path, recursive=recursive, ignore=ignore, executor=executor)
                            futures.append(future)
                        else:
                            self.process_folder(root=root, path=_path, recursive=recursive, ignore=ignore, executor=executor)
            elif child.type == "file":
                cfi = root[_path] = iCloudFileInfo(child)
                # Update parent folder modified time to be that of the newest child (not stored in iCloud Drive)
                #root[relative_path].modified_time = cfi.modified_time if cfi.modified_time > root[relative_path].modified_time else root[relative_path].modified_time
                logger.debug(f"iCloud Drive {"root" if root is self._root else "trash"} {_path} {cfi}")
            else:
                logger.debug(f"iCloud Drive {"root" if root is self._root else "trash"} did not process {child.type} {os.path.join(relative_path, child.name)}")
    
        threading.current_thread().name = threadname
        return futures

    def _remove_ignored_items(self):
        for root in [self._root, self._trash]:
            for path in list(root):
                if self.ignore(path, isinstance(root[path], iCloudFolderInfo)):
                    root.pop(path)

    def delete(self, path: str, lfi: iCloudFileInfo) -> DeleteAction:
        name = threading.current_thread().name
        threading.current_thread().name = f"delete {path}"
        result = NoAction()
        parent: iCloudFolderInfo = self.root.get(os.path.normpath(os.path.dirname(path)), None)
        cfi: iCloudFileInfo | iCloudFolderInfo= self.root.get(path, None)
        if parent is not None and cfi is not None:
            parent_node: DriveNode = parent.node
            child_node: DriveNode = cfi.node
            try:
                res = child_node.delete()
                status = res['items'][0]['status']
                logger.debug(f"iCloud Drive deleted {path} result: {status}")
                parent_node.remove(child_node)
                self.root.pop(path)
                result = DeleteAction(success=True, path=path)
            except Exception as e:
                logger.error(f"Exception in delete {e}")
                self.handle_drive_exception(e)
                result = DeleteAction(success=False, path=path, fn=self.delete, args=[path, lfi], exception=e)
            finally:
                threading.current_thread().name = name
        return result

    def rename(self, path: str, dest_path: str) -> RenameAction:
        name = threading.current_thread().name
        threading.current_thread().name = f"rename {path}"
        result = NoAction()
        try:
            cfi = self.root.get(path, None)
            if cfi is not None:
                cfi.node.rename(os.path.basename(dest_path))
                self.root.pop(path)
                self.root[dest_path] = cfi
                result = RenameAction(success=True, path=path)
        except Exception as e:
            logger.error(f"Exception in rename {e}")
            self.handle_drive_exception(e)
            result = RenameAction(success=False, path=path, fn=self.rename, args=[path, dest_path], exception=e)
        finally:
            threading.current_thread().name = name
        return result
    
    def upload(self, path: str, lfi: LocalFileInfo) -> UploadAction:
        name = threading.current_thread().name
        threading.current_thread().name = f"upload {path}"
        result = NoAction()
        try:
            parent_path: str = os.path.normpath(os.path.dirname(path))
            parent_node: DriveNode = self._root[parent_path].node
            with open(os.path.join(self._root_path, path), 'rb') as f:
                parent_node.upload(f, mtime=lfi.modified_time.timestamp(), ctime=lfi.created_time.timestamp())
            result = UploadAction(success=True, path=path)
        except Exception as e:
            logger.error(f"Exception in upload {e}")
            self.handle_drive_exception(e)
            result = UploadAction(success=False, path=path, fn=self.upload, args=[path, lfi], exception=e)
        finally:
            threading.current_thread().name = name
        return result
    
    def download(self, path: str, cfi: iCloudFileInfo, apply_after: Callable[[str], str]) -> DownloadAction:
        name = threading.current_thread().name
        threading.current_thread().name = f"download {path}"
        result = NoAction()
        try:
            file_path = os.path.join(self._root_path, os.path.normpath(path))
            parent_path: str = os.path.normpath(os.path.dirname(path))
            os.makedirs(os.path.join(self._root_path, parent_path), exist_ok=True)
            with open(file_path, 'wb', buffering=0) as f:
                with cfi.node.open(stream=True) as response:
                    for chunk in response.iter_content(chunk_size=constants.DOWNLOAD_MEDIA_CHUNK_SIZE):
                        if chunk:
                            f.write(chunk)
                f.flush()
                os.fsync(f.fileno())
                f.close()

            logger.debug(f"setting {file_path} modified_time to {cfi.modified_time}")
            os.utime(file_path, (cfi.modified_time.timestamp(), cfi.modified_time.timestamp()))
            apply_after(path)
            result = DownloadAction(success=True, path=path)
        except Exception as e:
            logger.error(f"Exception in download {e}")
            self.handle_drive_exception(e)
            result = DownloadAction(success=False, path=path, fn=self.download, args=[path, cfi, apply_after], exception=e)
        finally:
            threading.current_thread().name = name
        return result
    
    def create_icloud_folders(self, path: str) -> iCloudFolderInfo:
        name = threading.current_thread().name
        threading.current_thread().name = f"create icloud folders {path}"
        result = NoAction()
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
                    self.process_folder(root=self._root, path=_path, ignore=True,recursive=False)
                    parent = self._root[folder_path]
                    parent_node = parent.node
                    _path = folder_path
                else:
                    parent_node = self._root[folder_path].node
                    _path = folder_path

            result = CreateFolderAction(success=True, path=path)
        except Exception as e:
            logger.error(f"Exception in create_icloud_folders {e}")
            self.handle_drive_exception(e)
            result = CreateFolderAction(success=False, path=path, fn=self.create_icloud_folders, args=[path], exception=e)
        finally:
            threading.current_thread().name = name
        return result

    @property
    def root_count(self) -> int:
        return self._root[BaseTree.ROOT_FOLDER_NAME].file_count

    @property
    def trash_count(self) -> int:
        return self._trash[BaseTree.ROOT_FOLDER_NAME].number_of_items
    
    def root_has_changed(self) -> bool:
        name = threading.current_thread().name
        threading.current_thread().name = "check root"
        pre_count: int = 0
        post_count: int = 0
        try:
            pre_count = self.root_count
            # This is a hack to get refreshed node info without replacing the node object in pyicloud
            # and having to reload the entire tree
            post_count = self.drive.get_node_data(CLOUD_DOCS_ZONE_ID_ROOT).get('fileCount')
            self.drive._root.data['fileCount'] = post_count
            logger.debug(f"iCloud Drive root count pre: {pre_count}, post: {post_count}")
        except Exception as e:
            logger.warning(f"iCloud Drive get fileCount failed: {e}")
            return False
        finally:
            threading.current_thread().name = name
        return pre_count != post_count            
 
    def trash_has_changed(self) -> bool:
        name = threading.current_thread().name
        threading.current_thread().name = "check trash"
        pre_count: int = 0
        post_count: int = 0
        try:
            pre_count = self.trash_count
            # This is a hack to get refreshed node info without replacing the node object in pyicloud
            # and having to reload the entire tree
            post_count = self.drive.get_node_data(CLOUD_DOCS_ZONE_ID_TRASH).get('numberOfItems')
            self.drive._trash.data['numberOfItems'] = post_count
            logger.debug(f"iCloud Drive trash count pre: {pre_count}, post: {post_count}")
        except Exception as e:
            logger.warning(f"iCloud Drive trash refresh failed: {e}")
            return False
        finally:
            threading.current_thread().name = name
        return pre_count != post_count

    def handle_drive_exception(self, e: Exception) -> None:
        match e:
            case PyiCloudAPIResponseException():
                logger.warning(f"iCloud Drive Exception: ({e.__class__.__name__}) {e}")
                logger.warning(traceback.format_exc())
                self._is_authenticated = False
            case iCloudMismatchException():
                logger.debug(f"iCloud Drive Exception: ({e.__class__.__name__}) in refresh: {e}")
            case _:
                logger.error(f"iCloud Drive unhandled Exception: ({e.__class__.__name__}) {e}")
                logger.error(traceback.format_exc())
    
