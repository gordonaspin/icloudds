"""
model.icloud_tree

Provides a tree representation of the iCloud Drive file system. ICloudTree extends BaseTree
to manage files and folders stored in Apple's iCloud Drive, with support for authentication,
file operations (upload, download, delete, rename), and change detection.
"""
import os
from pathlib import Path
import logging
import traceback
from typing import Callable, override
from concurrent.futures import ThreadPoolExecutor, Future, as_completed

from urllib3 import disable_warnings
from urllib3.exceptions import InsecureRequestWarning
from pyicloud import PyiCloudService
from pyicloud.services.drive import (
    DriveService,
    DriveNode,
    CLOUD_DOCS_ZONE_ID_ROOT,
    CLOUD_DOCS_ZONE_ID_TRASH)
from pyicloud.exceptions import PyiCloudAPIResponseException, PyiCloudFailedLoginException

from context import Context
import constants
from icloud.authenticate import authenticate
from model.thread_safe import ThreadSafePathDict
from model.base_tree import BaseTree
from model.file_info import LocalFileInfo, FolderInfo, FileInfo, ICloudFileInfo, ICloudFolderInfo
from model.action_result import (
    ActionResult,
    Download,
    Upload,
    Delete,
    MkDir,
    Rename,
    Move,
    Refresh,
    Nil)

disable_warnings(category=InsecureRequestWarning)
logger = logging.getLogger(__name__)

class MismatchException(Exception):
    """
    Exception raised when the iCloud Drive tree structure does not match expected counts.
    Indicates an inconsistency between the file count and the actual number of files in the tree.
    """

class ICloudTree(BaseTree):
    """
    ICloudTree

    This class provides a representation of an iCloud Drive file system tree structure.
    ICloudTree extends BaseTree to manage files and folders stored in iCloud Drive.

    === How ICloudTree Works ===

    1. AUTHENTICATION:
    - ICloudTree authenticates with iCloud using credentials and manages the authentication state
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
    def __init__(self, ctx: Context) -> ICloudTree:
        self.drive: DriveService = None
        self._is_authenticated: bool = False
        self.ctx: Context = ctx
        self._threadpool: ThreadPoolExecutor = ThreadPoolExecutor(
            max((os.cpu_count() or 1) * 4, constants.DOWNLOAD_WORKERS))
        super().__init__(ctx)

    @override
    @property
    def document_root(self):
        """return document root"""
        if self.drive is None:
            self.authenticate()
        return self.drive.service_root

    def authenticate(self) -> None:
        """
        Authenticate with iCloud using provided credentials.
        Caches the authentication state to avoid redundant logins.
        """
        if self._is_authenticated:
            return
        try:
            api: PyiCloudService = authenticate(
                username=self.ctx.username,
                password=self.ctx.password,
                cookie_directory=self.ctx.cookie_directory,
                raise_authorization_exception=False,
                client_id=None,
                unverified_https=True
                )
            self.drive: DriveService = api.drive
            self._is_authenticated: bool = True
            logger.debug("iCloud Drive is %s@%s", self.ctx.username, self.drive.service_root)
        except PyiCloudFailedLoginException as e:
            logger.debug("exception is authenticate %s", e)
            self._handle_drive_exception(e)

    @override
    def refresh(self) -> None:
        """
        Refresh the iCloud Drive tree structure.
        Traverses the entire iCloud Drive hierarchy to update the tree.
        Uses multi-threading for concurrent folder processing.
        Applies ignore/include rules to filter items.
        Validates the tree by checking file counts."""
        succeeded: bool = True
        try:
            self.authenticate()
            self._root.clear()
            self._trash.clear()
            self._root[BaseTree.ROOT_FOLDER_NAME] = ICloudFolderInfo(node=self.drive.root)
            self._trash[BaseTree.ROOT_FOLDER_NAME] = ICloudFolderInfo(node=self.drive.trash)

            with self._threadpool as executor:
                pending = set()
                for root in [True, False]:
                    logger.debug("refreshing iCloud Drive %s", "root" if root else "trash")
                    future = executor.submit(
                        self.process_folder,
                        root=root,
                        path=BaseTree.ROOT_FOLDER_NAME,
                        recursive=True,
                        ignore=False,
                        executor=executor
                        )
                    pending = pending | set([future])
                while pending:
                    done, pending = as_completed(pending), set()
                    for future in done:
                        new_futures = future.result()
                        if (isinstance(new_futures, list)
                            and all(isinstance(f, Future) for f in new_futures)
                            ):
                            pending.update(new_futures)

            root_files_count = sum(1 for _ in self.files(root=True))
            trash_files_count = sum(1 for _ in self.files(root=False))
            if self._root_count() != root_files_count + trash_files_count:
                raise MismatchException(f"mismatch root_count: {self._root_count()} "
                                        f"!= root_files_count: {root_files_count} +"
                                        f" trash_files_count: {trash_files_count}")
        except MismatchException:
            # expect from time to time, normal behaviour
            logger.debug("Mismatch exception")
            succeeded = False
        except Exception as e:
            logger.warning("caught exception %s in refresh()", e)
            self._handle_drive_exception(e)
            succeeded = False

        logger.debug("refresh iCloud Drive complete root has %d items, "
                     "root count %d, %d folders, %d files",
                     len(self._root),
                     self._root_count(),
                     sum(1 for _ in self.folders(root=True)),
                     sum(1 for _ in self.files(root=True)))
        logger.debug("refresh iCloud Drive complete trash has %d items, "
                     "trash count %d, %d folders, %d files",
                     len(self._trash),
                     self._trash_count(),
                     sum(1 for _ in self.folders(root=False)),
                     sum(1 for _ in self.files(root=False)))
        self._remove_ignored_items()
        return succeeded

    @override
    def add(self,
            path: Path,
            _obj: FileInfo | FolderInfo=None,
            _root: bool=True) -> FileInfo | FolderInfo:
        """
        Add a file or folder to the iCloud Drive tree structure.
        """
        target = self._root if _root else self._trash
        target[path] = _obj
        return _obj

    def process_folder(self,
                       root: bool=True,
                       path: Path=None,
                       recursive: bool=False,
                       ignore: bool=True,
                       executor: ThreadPoolExecutor=None) -> Refresh | list[Future]:
        """
        Process a folder in iCloud Drive, populating its children in the tree structure.
        Can be run recursively to process subfolders.
        Supports multi-threaded execution using ThreadPoolExecutor.
        Applies ignore/include rules as specified.
        Returns a list of Future objects for further processing or a Refresh.
        """
        _the_dict: ThreadSafePathDict = self._root if root else self._trash
        futures: list[Future] = []
        children: list[DriveNode] = []
        result: ActionResult = Refresh(path=path, success=True)
        cfi: ICloudFolderInfo = _the_dict.get(path, None)

        if cfi is None:
            result: Nil = Nil()
        else:
            children = _the_dict[path].node.get_children(force=True)

        for child in children:
            if path == BaseTree.ROOT_FOLDER_NAME:
                child_path: Path = Path(child.name)
            else:
                child_path: Path = path.joinpath(child.name)
            if ignore and self.ignore(child_path):
                logger.debug("iCloud Drive %s ignore %s",
                             "root" if root else "trash",
                              child_path)
                continue

            if child.type == "folder":
                self.add(path=child_path, _obj=ICloudFolderInfo(child), _root=root)
                logger.debug("iCloud Drive %s add folder %s",
                             "root" if root else "trash",
                              child_path)
                if recursive:
                    if executor is not None:
                        future: Future = executor.submit(
                            self.process_folder,
                            root=root,
                            path=child_path,
                            recursive=recursive,
                            ignore=ignore,
                            executor=executor)
                        futures.append(future)
                    else:
                        self.process_folder(
                            root=root,
                            path=child_path,
                            recursive=recursive,
                            ignore=ignore,
                            executor=executor)
            elif child.type == "file":
                self.add(path=child_path, _obj=ICloudFileInfo(child), _root=root)
                logger.debug("iCloud Drive %s add file %s",
                             "root" if root else "trash",
                              child_path)
            else:
                logger.debug("iCloud Drive %s did not process %s %s",
                             "root" if root else "trash",
                             child.type,
                             path.joinpath(child.name))

        return futures if len(futures) > 0 else result

    def delete(self, path: Path, lfi: ICloudFileInfo, retry: int=constants.MAX_RETRIES) -> Delete:
        """
        Delete a file or folder from iCloud Drive.
        Updates the tree structure accordingly.
        Returns an Delete indicating success or failure."""
        result:ActionResult = Nil()
        parent: ICloudFolderInfo = self._root.get(path.parent, None)
        cfi: ICloudFileInfo | ICloudFolderInfo = self._root.get(path, None)
        if parent is not None and cfi is not None:
            parent_node: DriveNode = parent.node
            child_node: DriveNode = cfi.node
            try:
                res = child_node.delete()
                status = res['items'][0]['status']
                logger.debug("deleted %s result: %s", path, status)
                parent_node.remove(child_node)
                if isinstance(cfi, ICloudFolderInfo):
                    self.prune(path)
                else:
                    self.pop(path)
                result = Delete(success=True, path=path)
            except ValueError as e:
                logger.warning("exception ValueError in delete %s", e)
                result = Delete(success=False,
                                path=path,
                                fn=self.delete,
                                args=[path, lfi, 0],
                                exception=e)
            except Exception as e:
                logger.error("exception in delete %s", e)
                self._handle_drive_exception(e)
                result = Delete(success=False,
                                path=path,
                                fn=self.delete,
                                args=[path, lfi, retry-1],
                                exception=e)
        return result

    def move(self, path: Path, dest_path: Path, retry: int=constants.MAX_RETRIES) -> Move:
        """
        Move a file or folder in iCloud Drive.
        Updates the tree structure accordingly.
        Returns an Move indicating success or failure.
        """
        result: ActionResult = Nil()
        try:
            cfi: ICloudFolderInfo | ICloudFileInfo = self._root.get(path, None)
            dfi: ICloudFolderInfo = self._root.get(dest_path.parent, None)
            if cfi is not None and dfi is not None:
                res: dict = self.drive.move_nodes_to_node([cfi.node], dfi.node)
                status: str = res['items'][0]['status']
                logger.debug("iCloud Drive move %s result: %s", path, status)
                self._root.pop(path)
                self._root[dest_path] = cfi
                result = Move(success=True, path=path, dest_path=dest_path)
        except Exception as e:
            logger.error("exception in move %s", e)
            self._handle_drive_exception(e)
            result = Move(success=False,
                          path=path,
                          dest_path=dest_path,
                          fn=self.move,
                          args=[path, dest_path, retry-1],
                          exception=e)
        return result

    def rename(self, old_path: Path, new_path: Path, retry: int=constants.MAX_RETRIES) -> Rename:
        """
        Rename a file or folder in iCloud Drive.
        Updates the tree structure accordingly.
        Returns an Rename indicating success or failure.
        """
        result: ActionResult = Nil()
        try:
            cfi: ICloudFolderInfo | ICloudFileInfo = self._root.get(old_path, None)
            if cfi is not None:
                res: dict = cfi.node.rename(new_path.name)
                status: str = res['items'][0]['status']
                logger.debug("iCloud Drive rename %s result: %s", old_path, status)
                self.re_key(old_path=old_path, new_path=new_path)
                result = Rename(success=True, path=new_path)
        except Exception as e:
            logger.error("exception in rename %s", e)
            self._handle_drive_exception(e)
            result = Rename(success=False,
                            path=old_path,
                            fn=self.rename,
                            args=[old_path, new_path, retry-1],
                            exception=e)
        return result

    def upload(self, path: Path, lfi: LocalFileInfo, retry: int=constants.MAX_RETRIES) -> Upload:
        """
        Upload a local file to iCloud Drive.
        Preserves file metadata such as modification and creation times.
        Returns an Upload indicating success or failure."""
        result: ActionResult = Nil()
        try:
            self.delete(path=path, lfi=lfi, retry=0)
            parent_path: Path = path.parent
            parent_node: DriveNode = self._root[parent_path].node
            with open(self._root_path.joinpath(path), 'rb') as f:
                parent_node.upload(f,
                                   mtime=lfi.modified_time.timestamp(),
                                   ctime=lfi.created_time.timestamp())
            result = Upload(success=True, path=path)
        except FileNotFoundError as e:
            logger.warning("exception FileNotFoundError %s file %s in upload", e, path)
            result = Upload(success=False,
                            path=path,
                            fn=self.upload,
                            args=[path, lfi, 0])
        except Exception as e:
            logger.error("exception in upload %s", e)
            self._handle_drive_exception(e)
            result = Upload(success=False,
                            path=path,
                            fn=self.upload,
                            args=[path, lfi, retry-1],
                            exception=e)
        return result

    def download(self, path: Path,
                 cfi: ICloudFileInfo,
                 apply_after: Callable[[str], str],
                 retry: int=constants.MAX_RETRIES) -> Download:
        """
        Download a file from iCloud Drive.
        Preserves file metadata such as modification and creation times.
        Calls apply_after callback after successful download.
        Returns an Download indicating success or failure.
        """
        result: ActionResult = Nil()
        try:
            file_path: Path = self._root_path.joinpath(path)
            parent_path: Path = path.parent
            #os.makedirs(os.path.join(self._root_path, parent_path), exist_ok=True)
            self._root_path.joinpath(parent_path).mkdir(parents=True, exist_ok=True)

            with open(file_path, 'wb', buffering=0) as f:
                with cfi.node.open(stream=True) as response:
                    for chunk in response.iter_content(
                        chunk_size=constants.DOWNLOAD_MEDIA_CHUNK_SIZE
                        ):
                        if chunk:
                            f.write(chunk)
                f.flush()
                os.fsync(f.fileno())
                f.close()

            logger.debug("setting %s modified_time to %s", file_path, cfi.modified_time)
            os.utime(file_path, (cfi.modified_time.timestamp(), cfi.modified_time.timestamp()))
            apply_after(path)
            result = Download(success=True, path=path)
        except Exception as e:
            logger.error("exception in download %s", e)
            self._handle_drive_exception(e)
            result = Download(success=False,
                              path=path,
                              fn=self.download,
                              args=[path, cfi, apply_after, retry-1],
                              exception=e)
        return result

    def create_icloud_folders(self, path: Path, retry: int=constants.MAX_RETRIES) -> MkDir | Nil:
        """
        Create intermediate folders in iCloud Drive for the given path.
        Returns a MkDir or Nil indicating success or failure."""
        result: ActionResult = None
        try:
            folder_path = BaseTree.ROOT_FOLDER_NAME
            _path: Path = folder_path
            parent_cfi: ICloudFolderInfo = self._root[folder_path]
            parent_node: DriveNode = parent_cfi.node
            for folder in path.parts:
                folder_path: Path = folder_path.joinpath(folder)
                if folder_path not in self._root:
                    logger.debug("iCloud Drive mkdir %s", folder_path)
                    res: dict = parent_node.mkdir(folder)
                    status: str = res['folders'][0]['status']
                    logger.debug("iCloud Drive mkdir %s result: %s", path, status)
                    self.process_folder(root=True, path=_path, ignore=True,recursive=False)
                    parent_cfi = self._root[folder_path]
                    parent_node = parent_cfi.node
                    _path = folder_path
                    result = MkDir(success=True, path=path)
                else:
                    parent_node = self._root[folder_path].node
                    _path = folder_path
            if result is None:
                result = Nil()
        except Exception as e:
            logger.error("exception in create_icloud_folders %s", e)
            self._handle_drive_exception(e)
            result = MkDir(success=False,
                           path=path,
                           fn=self.create_icloud_folders,
                           args=[path, retry-1],
                           exception=e)
        return result

    def docwsids(self) -> dict[str, str]:
        """
        Build a mapping of document workspace IDs to their paths in the iCloud Drive tree.
        
        Returns:
            A dictionary where keys are document workspace IDs (docwsid) and values are the
            corresponding file/folder paths in the tree.
        """
        d: dict[str, str] = {}
        for k, v in self._root.items():
            d[v.node.data['docwsid']] = k
        return d

    def is_dirty(self) -> bool:
        """returns True if icloud has changes, else False"""
        if not self._is_authenticated:
            return False

        try:
            # it is possible that self._root or self._trash is empty but
            # don't want to lock as trash_has_changed() is a lengthy operation
            if self._root_has_changed() or self._trash_has_changed():
                return True
        except Exception as e:
            logger.warning("exception in is_dirty() %s", e)
            self._handle_drive_exception(e)

        return False

    def _root_count(self) -> int:
        """Return the number of items in the iCloud Drive root folder."""
        with self._root as root:
            if BaseTree.ROOT_FOLDER_NAME in root:
                return self._root[BaseTree.ROOT_FOLDER_NAME].file_count
        return 0

    def _trash_count(self) -> int:
        """Return the number of items in the iCloud Drive trash folder."""
        with self._trash as root:
            if BaseTree.ROOT_FOLDER_NAME in root:
                return self._trash[BaseTree.ROOT_FOLDER_NAME].number_of_items
        return 0

    def _root_has_changed(self) -> bool:
        """
        Check if the iCloud Drive root folder has changed by comparing file counts.
        Returns True if the root folder has changed, False otherwise."""

        pre_count: int = 0
        post_count: int = 0
        try:
            pre_count = self._root_count()
            # This is a hack to get refreshed node info without replacing
            # the node object in pyicloud and having to reload the entire tree
            post_count = self.drive.get_node_data(CLOUD_DOCS_ZONE_ID_ROOT).get('fileCount')
            self.drive.root.data['fileCount'] = post_count
        except Exception as e:
            logger.warning("exception in _root_has_changed: %s", e)
            self._handle_drive_exception(e)
            return False
        return pre_count != post_count

    def _trash_has_changed(self) -> bool:
        """
        Check if the iCloud Drive trash folder has changed by comparing item counts.
        """
        pre_count: int = 0
        post_count: int = 0
        try:
            pre_count = self._trash_count()
            # This is a hack to get refreshed node info without replacing
            # the node object in pyicloud and having to reload the entire tree
            post_count = self.drive.get_node_data(CLOUD_DOCS_ZONE_ID_TRASH).get('numberOfItems')
            self.drive.trash.data['numberOfItems'] = post_count
        except Exception as e:
            logger.warning("exception in _trash_has_changed: %s", e)
            self._handle_drive_exception(e)
            return False
        return pre_count != post_count

    def _handle_drive_exception(self, e: Exception) -> None:
        """
        Handle exceptions raised during iCloud Drive operations.
        Categorizes exceptions and logs appropriate messages.
        Clears authentication state on API failures to force re-authentication."""
        match e:
            case PyiCloudAPIResponseException():
                logger.warning("exception PyiCloudAPIResponseException: %s %s code: %s",
                               e.__class__.__name__, e, e.code)
                if e.code is not None and e.code in [503,]:
                    logger.warning(traceback.format_exc())
                self._is_authenticated: bool = False
                logger.info("pausing jobs")
                self.ctx.jobs_disabled.set()
            case PyiCloudFailedLoginException():
                logger.warning("exception PyiCloudFailedLoginException: %s %s",
                               e.__class__.__name__, e)
                logger.warning(traceback.format_exc())
                self._is_authenticated: bool = False
                logger.info("pausing jobs")
                self.ctx.jobs_disabled.set()
            case _:
                logger.error("unhandled exception in ICloudTree: %s %s", e.__class__.__name__, e)
                logger.error(traceback.format_exc())
                self._is_authenticated: bool = False
                logger.info("pausing jobs")
                self.ctx.jobs_disabled.set()
