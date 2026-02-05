import os
import logging
import traceback
from typing import Callable, override 
from urllib3 import disable_warnings
from urllib3.exceptions import InsecureRequestWarning
from concurrent.futures import ThreadPoolExecutor, Future, as_completed

import pyicloud.services.drive
from pyicloud.services.drive import DriveNode, CLOUD_DOCS_ZONE_ID_ROOT, CLOUD_DOCS_ZONE_ID_TRASH
from pyicloud.exceptions import PyiCloudAPIResponseException

from context import Context
import constants as constants
from icloud.authenticate import authenticate
from model.base_tree import BaseTree
from model.file_info import LocalFileInfo, iCloudFileInfo, iCloudFolderInfo, FileInfo
from model.action_result import Download, Upload, Delete, MkDir, Rename, Move, Refresh, Nil

disable_warnings(category=InsecureRequestWarning)
logger = logging.getLogger(__name__)

class iCloudMismatchException(Exception):
    pass

class iCloudTree(BaseTree):
    """
    iCloudTree

    This class provides a representation of an iCloud Drive file system tree structure.
    iCloudTree extends BaseTree to manage files and folders stored in iCloud Drive.

    === How iCloudTree Works ===

    1. AUTHENTICATION:
    - iCloudTree authenticates with iCloud using credentials and manages the authentication state
    - Authentication is performed lazily (on first access) and cached to avoid redundant logins
    - Uses PyiCloud library to communicate with Apple's iCloud Drive API

    2. TREE STRUCTURE:
    - Maintains two separate tree structures: _root (main iCloud Drive) and _trash (iCloud Trash)
    - Each tree is built using iCloudFileInfo and iCloudFolderInfo objects
    - The tree is refreshed by traversing the entire iCloud Drive hierarchy

    3. REFRESH OPERATION:
    - Refreshes both root and trash folders recursively using multi-threaded processing
    - Uses ThreadPoolExecutor for concurrent folder processing to improve performance
    - Validates the tree by checking file counts and detecting mismatches
    - Applies ignore/include rules to filter out items as specified in context

    4. FILE OPERATIONS:
    - UPLOAD: Uploads local files to iCloud Drive
    - DOWNLOAD: Downloads files from iCloud Drive with proper timestamp preservation
    - DELETE: Removes files/folders from iCloud Drive
    - RENAME: Renames files/folders in iCloud Drive
    - CREATE_FOLDERS: Creates intermediate folder structure in iCloud Drive

    5. CHANGE DETECTION:
    - Monitors if the iCloud root or trash has changed by comparing file counts
    - Uses direct API calls to get fresh file count data without full tree refresh
    - Helps determine when a full refresh is needed

    6. ERROR HANDLING:
    - Catches and categorizes exceptions (API errors, mismatch errors, etc.)
    - Clears authentication state on API failures to force re-authentication
    - Returns action results with detailed error information for retry logic

    7. THREAD MANAGEMENT:
    - Updates thread names for better debugging and monitoring
    - Supports concurrent processing of folders and file operations
    - Thread-safe folder traversal during refresh operations
    """
    def __init__(self, ctx: Context):
        self.drive: pyicloud.services.drive.DriveService = None
        self._is_authenticated: bool = False
        self.ctx: Context = ctx
        self._threadpool: ThreadPoolExecutor = ThreadPoolExecutor(os.cpu_count()*4)
        super().__init__(root_path=ctx.directory, ignores=ctx.ignore_icloud, includes=ctx.include_icloud)

    def authenticate(self) -> None:
        """
        Authenticate with iCloud using provided credentials.
        Caches the authentication state to avoid redundant logins.
        """
        if self._is_authenticated:
            return
        api = authenticate(username=self.ctx.username, password=self.ctx.password, cookie_directory=self.ctx.cookie_directory, raise_authorization_exception=False, client_id=None, unverified_https=True)
        self.drive = api.drive
        self._is_authenticated = True

    @override
    def refresh(self, root=None, path=None, force=True) -> None:
        """
        Refresh the iCloud Drive tree structure.
        Traverses the entire iCloud Drive hierarchy to update the tree.
        Uses multi-threading for concurrent folder processing.
        Applies ignore/include rules to filter items.
        Validates the tree by checking file counts."""
        succeeded = True
        try:
            self.authenticate()
            logger.debug(f"Refreshing iCloud Drive {self.ctx.username}::{self.drive.service_root}...")
            with self._threadpool as executor:
                pending = set()
                for (root, icf) in [(self._root, iCloudFolderInfo(self.drive.root)), (self._trash, iCloudFolderInfo(self.drive.trash))]:
                    logger.debug(f"Refreshing iCloud Drive {icf.drivewsid}...")
                    root.clear()
                    root[BaseTree.ROOT_FOLDER_NAME] = icf
                    future = executor.submit(self.process_folder, root=root, path=BaseTree.ROOT_FOLDER_NAME, recursive=True, ignore=False, executor=executor)
                    pending = pending | set([future])
                while pending:
                    done, pending = as_completed(pending), set()
                    for future in done:
                        new_futures = future.result()
                        if isinstance(new_futures, list) and all(isinstance(f, Future) for f in new_futures):
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
        """
        Add a file or folder (and its parents)to the iCloud Drive tree structure.
        """
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

    def process_folder(self, root=None, path=None, recursive=False, ignore=True, executor=None) -> Refresh|list[Future]:
        """
        Process a folder in iCloud Drive, populating its children in the tree structure.
        Can be run recursively to process subfolders.
        Supports multi-threaded execution using ThreadPoolExecutor.
        Applies ignore/include rules as specified.
        Returns a list of Future objects for further processing or a Refresh.
        """
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
                #logger.debug(f"iCloud Drive {"root" if root is self._root else "trash"} {_path} {cfi}")
            else:
                logger.debug(f"iCloud Drive {"root" if root is self._root else "trash"} did not process {child.type} {os.path.join(relative_path, child.name)}")
    
        return futures if len(futures) > 0 else Refresh(path=path, success=True)

    def _remove_ignored_items(self):
        """
        Remove ignored items from both root and trash trees based on ignore/include rules.
        """
        for root in [self._root, self._trash]:
            for path in list(root):
                if self.ignore(path, isinstance(root[path], iCloudFolderInfo)):
                    root.pop(path)

    def delete(self, path: str, lfi: iCloudFileInfo, retry=constants.MAX_RETRIES) -> Delete:
        """
        Delete a file or folder from iCloud Drive.
        Updates the tree structure accordingly.
        Returns an Delete indicating success or failure."""
        result = Nil()
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
                result = Delete(success=True, path=path)
            except ValueError as e:
                logger.warning(f"ValueError in delete {e}")
                result = Delete(success=False, path=path, fn=self.delete, args=[path, lfi, 0], exception=e)
            except Exception as e:
                logger.error(f"Exception in delete {e}")
                self.handle_drive_exception(e)
                result = Delete(success=False, path=path, fn=self.delete, args=[path, lfi, retry-1], exception=e)
        return result

    def move(self, path: str, dest_path: str, retry=constants.MAX_RETRIES) -> Move:
        """
        Move a file or folder in iCloud Drive.
        Updates the tree structure accordingly.
        Returns an Move indicating success or failure.
        """
        result = Nil()
        try:
            cfi = self.root.get(path, None)
            dfi = self.root.get(os.path.normpath(os.path.dirname(dest_path)), None)
            if cfi is not None and dfi is not None:
                self.drive.move_nodes_to_node([cfi.node], dfi.node)
                self.root.pop(path)
                self.root[dest_path] = cfi
                result = Move(success=True, path=path, dest_path=dest_path)
        except Exception as e:
            logger.error(f"Exception in rename {e}")
            self.handle_drive_exception(e)
            result = Move(success=False, path=path, dest_path=dest_path, fn=self.rename, args=[path, dest_path, retry-1], exception=e)
        return result

    def rename(self, path: str, dest_path: str, retry=constants.MAX_RETRIES) -> Rename:
        """
        Rename a file or folder in iCloud Drive.
        Updates the tree structure accordingly.
        Returns an Rename indicating success or failure.
        """
        result = Nil()
        try:
            cfi = self.root.get(path, None)
            if cfi is not None:
                cfi.node.rename(os.path.basename(dest_path))
                self.root.pop(path)
                self.root[dest_path] = cfi
                result = Rename(success=True, path=dest_path)
        except Exception as e:
            logger.error(f"Exception in rename {e}")
            self.handle_drive_exception(e)
            result = Rename(success=False, path=path, fn=self.rename, args=[path, dest_path, retry-1], exception=e)
        return result
    
    def upload(self, path: str, lfi: LocalFileInfo, retry=constants.MAX_RETRIES) -> Upload:
        """
        Upload a local file to iCloud Drive.
        Preserves file metadata such as modification and creation times.
        Returns an Upload indicating success or failure."""
        result = Nil()
        try:
            self.delete(path=path, lfi=lfi, retry=0)
            parent_path: str = os.path.normpath(os.path.dirname(path))
            parent_node: DriveNode = self._root[parent_path].node
            with open(os.path.join(self._root_path, path), 'rb') as f:
                _result = parent_node.upload(f, mtime=lfi.modified_time.timestamp(), ctime=lfi.created_time.timestamp())
            result = Upload(success=True, path=path)
        except Exception as e:
            logger.error(f"Exception in upload {e}")
            self.handle_drive_exception(e)
            result = Upload(success=False, path=path, fn=self.upload, args=[path, lfi, retry-1], exception=e)
        return result
    
    def download(self, path: str, cfi: iCloudFileInfo, apply_after: Callable[[str], str], retry=constants.MAX_RETRIES) -> Download:
        """
        Download a file from iCloud Drive.
        Preserves file metadata such as modification and creation times.
        Calls apply_after callback after successful download.
        Returns an Download indicating success or failure.
        """
        result = Nil()
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
            result = Download(success=True, path=path)
        except Exception as e:
            logger.error(f"Exception in download {e}")
            self.handle_drive_exception(e)
            result = Download(success=False, path=path, fn=self.download, args=[path, cfi, apply_after, retry-1], exception=e)
        return result
    
    def create_icloud_folders(self, path: str, retry=constants.MAX_RETRIES) -> MkDir|Nil:
        """
        Create intermediate folders in iCloud Drive for the given path.
        Returns a MkDir or Nil indicating success or failure."""
        result = None
        try:
            folder_path = BaseTree.ROOT_FOLDER_NAME
            _path = folder_path
            parent = self._root[folder_path]
            parent_node: DriveNode = parent.node
            for folder_name in path.split(os.sep):
                folder_path = os.path.normpath(os.path.join(folder_path, folder_name))
                if folder_path not in self._root:
                    logger.debug(f"iCloud Drive creating parent folder {folder_path}...")
                    parent_node.mkdir(folder_name)
                    self.process_folder(root=self._root, path=_path, ignore=True,recursive=False)
                    parent = self._root[folder_path]
                    parent_node = parent.node
                    _path = folder_path
                    result = MkDir(success=True, path=path)
                else:
                    parent_node = self._root[folder_path].node
                    _path = folder_path
            if result is None:
                result = Nil()
        except Exception as e:
            logger.error(f"Exception in create_icloud_folders {e}")
            self.handle_drive_exception(e)
            result = MkDir(success=False, path=path, fn=self.create_icloud_folders, args=[path, retry-1], exception=e)
        return result
    
    def docwsids(self) -> dict[str, str]:
        d = dict()
        for k, v in self.root.items():
            d[v.node.data['docwsid']] = k
        return d

    @property
    def root_count(self) -> int:
        """Return the number of items in the iCloud Drive root folder."""
        return self._root[BaseTree.ROOT_FOLDER_NAME].file_count

    @property
    def trash_count(self) -> int:
        """Return the number of items in the iCloud Drive trash folder."""
        return self._trash[BaseTree.ROOT_FOLDER_NAME].number_of_items
    
    def root_has_changed(self) -> bool:
        """
        Check if the iCloud Drive root folder has changed by comparing file counts.
        Returns True if the root folder has changed, False otherwise."""
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
        return pre_count != post_count            
 
    def trash_has_changed(self) -> bool:
        """
        Check if the iCloud Drive trash folder has changed by comparing item counts.
        """
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
        return pre_count != post_count

    def handle_drive_exception(self, e: Exception) -> None:
        """
        Handle exceptions raised during iCloud Drive operations.
        Categorizes exceptions and logs appropriate messages.
        Clears authentication state on API failures to force re-authentication."""
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
    
