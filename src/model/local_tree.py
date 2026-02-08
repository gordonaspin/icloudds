"""
model.local_tree

Provides a tree representation of the local file system. LocalTree extends BaseTree
to scan and manage the hierarchy of files and folders stored on the local disk,
with support for ignore/include filtering rules.
"""
import os
import logging
from typing import override

from context import Context
from model.base_tree import BaseTree
from model.file_info import LocalFileInfo, LocalFolderInfo

logger = logging.getLogger(__name__)


class LocalTree(BaseTree):
    """
    LocalTree

    This class provides a representation of the local file system as a tree structure.
    LocalTree extends BaseTree to manage files and folders stored on the local disk.

    === How LocalTree Works ===

    1. INITIALIZATION:
    - Takes a Context object that specifies the root directory to scan
    - Sets up ignore and include rules from context for filtering files and folders
    - Initializes the tree structure with a single root node

    2. TREE STRUCTURE:
    - Maintains a single tree structure (_root) representing the local directory hierarchy
    - Each node is represented by LocalFileInfo or LocalFolderInfo objects
    - The tree includes all files and directories under the specified root path

    3. REFRESH OPERATION:
    - Scans the entire local file system tree recursively
    - Initializes the root folder node if not already present
    - Calls _add_children to recursively populate files and directories
    - Applies ignore/include rules to filter out unwanted items
    - Logs the final count of folders and files in the tree

    4. ADDING FILES:
    - The add() method adds a file or folder path to the tree
    - Creates intermediate parent folder entries as needed
    - Retrieves file metadata using os.stat() for file properties
    - Returns the FileInfo object representing the added item

    5. RECURSIVE SCANNING:
    - _add_children() recursively traverses the directory structure
    - Uses os.scandir() for efficient directory scanning
    - Processes both files and directories, applying ignore filters
    - Handles permission errors gracefully by skipping unreadable directories
    - Converts absolute paths to relative paths for tree storage

    6. IGNORE/INCLUDE FILTERING:
    - Applies filtering rules during both refresh and add operations
    - Skips files and folders that match ignore patterns
    - Supports include patterns from context configuration
    - Filters are applied during tree population, not after

    7. SYMBOLIC LINK HANDLING:
    - Follows symbolic links when checking if entries are files or directories
    - Uses follow_symlinks=True in stat operations
    - This allows LocalTree to traverse symlinked directories

    8. ERROR HANDLING:
    - Catches and silently handles PermissionError for unreadable directories
    - Allows tree population to continue even if some directories cannot be accessed
    """

    def __init__(self, ctx: Context):
        self.ctx = ctx
        super().__init__(root_path=ctx.directory, ignores=self.ctx.ignore_local,
                         includes=self.ctx.include_icloud)

    @override
    def refresh(self) -> None:
        """Refresh the local file system tree by scanning the root directory."""
        logger.debug("Refreshing Local Drive %s...", self._root_path)
        self._root.clear()
        self._root[BaseTree.ROOT_FOLDER_NAME] = LocalFolderInfo(
            BaseTree.ROOT_FOLDER_NAME)
        self._add_children(self._root_path)
        logger.debug("Refresh local complete root has %d items, %d folders, %d files",
                     len(self.root),
                     sum(1 for _ in self.folders(self.root)),
                     sum(1 for _ in self.files(self.root)))

    @override
    def add(self, path, _obj=None) -> LocalFileInfo | LocalFolderInfo:
        """Add a file or folder at the given path to the local tree structure."""
        parent_path = os.path.dirname(path)
        folder_path = BaseTree.ROOT_FOLDER_NAME
        for folder_name in parent_path.split(os.sep):
            folder_path = os.path.join(folder_path, folder_name)
            self._root[folder_path] = LocalFolderInfo(name=folder_name)

        if os.path.isfile(os.path.join(self._root_path, path)):
            stat_entry = os.stat(os.path.join(self._root_path, path))
            self._root[path] = LocalFileInfo(
                name=os.path.basename(path), stat_entry=stat_entry)
        elif os.path.isdir(os.path.join(self._root_path, path)):
            self._root[path] = LocalFolderInfo(name=os.path.basename(path))
        return self._root.get(path, None)

    def _add_children(self, path):
        """Populate files and subfolders for a single folder."""
        try:
            with os.scandir(path) as entries:
                for entry in entries:
                    path = os.path.relpath(entry.path, self._root_path)
                    stat_entry = entry.stat()
                    if entry.is_file(follow_symlinks=True):
                        if self.ignore(path):
                            continue
                        logger.debug("Local file %s", path)
                        self._root[path] = LocalFileInfo(
                            name=entry.name, stat_entry=stat_entry)
                    elif entry.is_dir(follow_symlinks=True):
                        if self.ignore(path):
                            continue
                        self._root[path] = LocalFolderInfo(name=entry.name)
                        self._add_children(entry.path)
        except PermissionError:
            pass  # Skip unreadable directories
