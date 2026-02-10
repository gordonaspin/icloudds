"""
model.local_tree

Provides a tree representation of the local file system. LocalTree extends BaseTree
to scan and manage the hierarchy of files and folders stored on the local disk,
with support for ignore/include filtering rules.
"""
from os import scandir
from pathlib import Path
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
    - Retrieves file metadata using Path.stat() for file properties
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
    def add(self, path: Path, _obj=None, _root:dict=None) -> LocalFileInfo | LocalFolderInfo:
        """Add a file or folder at the given path to the local tree structure."""
        for parent in path.parents:
            if parent.name and parent not in self._root:
                self._root[parent] = LocalFolderInfo(name=parent.name)

        if _obj is not None:
            self._root[path] = _obj
        else:
            if self._root_path.joinpath(path).is_file():
                # add file entry
                stat_entry = self._root_path.joinpath(path).stat()
                self._root[path] = LocalFileInfo(
                    name=path.name, stat_entry=stat_entry)
            elif self._root_path.joinpath(path).is_dir():
                # add folder entry
                self._root[path] = LocalFolderInfo(path.name)
            elif path in self._root:
                # path is neither file nor folder, remove if in _root
                self._root.pop(path)
        return self._root.get(path, None)

    def _add_children(self, path: Path):
        """Populate files and subfolders for a single folder."""
        try:
            with scandir(path) as entries:
                for entry in entries:
                    path = Path(entry.path).relative_to(self._root_path)
                    stat_entry = entry.stat()
                    if entry.is_file(follow_symlinks=True):
                        if self.ignore(path):
                            continue
                        logger.debug("Local file %s", path)
                        self.add(path=path, _obj=LocalFileInfo(
                            name=entry.name, stat_entry=stat_entry))
                    elif entry.is_dir(follow_symlinks=True):
                        if self.ignore(path):
                            continue
                        self.add(path=path, _obj=LocalFolderInfo(name=entry.name))
                        self._add_children(entry.path)
        except PermissionError:
            pass  # Skip unreadable directories
