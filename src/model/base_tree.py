"""
model.base_tree

Base tree class for managing a file/folder structure with filtering capabilities.
Provides common functionality for tracking files and folders, applying include/exclude filters,
and maintaining a trash/recycle bin for deleted items.
"""
from pathlib import Path
import logging
from logging import Logger
import re
from collections.abc import Iterator
from typing import Any, Tuple

from context import Context
from model.file_info import FileInfo, FolderInfo
from model.thread_safe import ThreadSafePathDict

logger: Logger = logging.getLogger(__name__)


class BaseTree():
    """
    Abstract base class for managing a structure of files and folders.

    Provides filtering (ignore/include patterns), iteration methods, and manages both
    active content and a trash section for deleted items. This is intended to be subclassed
    by concrete implementations like LocalTree and ICloudTree.
    """
    ROOT_FOLDER_NAME: Path = Path(".")

    def __init__(self,
                 ctx: Context) -> BaseTree:
        """
        Initialize a BaseTree instance.

        Args:
            root_path: The root path of the tree.
            ignores: List of regex patterns for files/folders to ignore.
            includes: List of regex patterns for files/folders to explicitly include.
        """
        self._root: ThreadSafePathDict = ThreadSafePathDict()
        self._trash: ThreadSafePathDict = ThreadSafePathDict()
        self._root_path: Path = ctx.directory
        builtin_ignore_regexes: list[str] = [
            r'.*\.com-apple-bird.*',
            r'.*\.DS_Store'
        ]
        include_patterns: list[str] = []

        builtin_ignore_regexes.extend(ctx.ignore_regexes or [])
        self._ignore_regexes: list[re.Pattern] = [
            re.compile(pattern) for pattern in builtin_ignore_regexes]

        include_patterns.extend(ctx.include_regexes or [])
        self._includes_regexes: list[re.Pattern] = [
            re.compile(pattern) for pattern in include_patterns]

    def keys(self, root:bool=True):
        """returns keys in root or trash"""
        root = self._root if root else self._trash
        return root.keys()

    def items(self, root:bool=True) -> Iterator[Tuple[str, Any]]:
        """returns items in root or trash"""
        root = self._root if root else self._trash
        return root.items()

    @property
    def ignores_patterns(self) -> list[str]:
        """Get the list of regex patterns for files/folders to ignore."""
        return self._builtin_ignore_regexes

    @property
    def ignores_regexes(self) -> list[re.Pattern]:
        """Get the compiled regex patterns for files/folders to ignore."""
        return self._ignore_regexes

    @property
    def includes_patterns(self) -> list[str]:
        """Get the list of regex patterns for files/folders to explicitly include."""
        return self._includes_patterns

    @property
    def includes_regexes(self) -> list[str]:
        """Get the compiled regex patterns for files/folders to explicitly include."""
        return self._includes_regexes

    def refresh(self) -> None:
        """
        Refresh the tree state by reloading the current file/folder structure.
        Subclasses must implement this method with tree-specific logic.
        """
        raise NotImplementedError("Subclasses should implement this method")

    def add(self,
            path: Path,
            _obj: FileInfo | FolderInfo=None,
            _root:bool=True) -> FileInfo | FolderInfo:
        """
        Add a file or folder to the tree at the specified path.
        Subclasses must implement this method with tree-specific logic.

        Args:
            path: The path to the file or folder to add.
            _obj: a FileInfo or FolderInfo object
            _root: True if adding to root, False if trash

        Returns:
            The FileInfo or FolderInfo object representing the added item.
        """
        raise NotImplementedError("Subclasses should implement this method")

    def get(self, key, default=None, root: bool=True) -> FileInfo | FolderInfo:
        """gets item from root by default, else trash"""
        target = self._root if root else self._trash
        return target.get(key, default)

    def pop(self, path: Path) -> FileInfo | FolderInfo:
        """pops an item from root and returns it"""
        return self._root.pop(path)

    def prune(self, path: Path, inclusive:bool=True) -> None:
        """prunes all sub-paths from tree, and path if inclusive"""
        for k in self.keys():
            p: Path = Path(k)
            if p.is_relative_to(path):
                if not inclusive and p == path:
                    continue
                with self._root as root:
                    if k in root:
                        root.pop(k)

    def re_key(self, old_path: Path, new_path: Path) -> None:
        """re-keys old_path to new_path including children"""
        for k in list(self.keys()):
            p: Path = Path(k)
            if p.is_relative_to(old_path):
                new_key = k.replace(str(old_path), str(new_path))
                with self._root as root:
                    if k in root:
                        self._root[new_key] = self._root.pop(k)

    def ignore(self, path: Path) -> bool:
        """
        Determine whether a file or folder should be ignored based on include/exclude patterns.

        Logic:
        - If the name matches any ignore pattern, return True (ignore it).
        - If no include patterns are defined, return False (don't ignore).
        - If include patterns exist and the name matches one, return False (don't ignore).
        - Otherwise, return True (ignore it).

        Args:
            name: The name or path to check.

        Returns:
            True if the item should be ignored, False otherwise.
        """
        for regex in self._ignore_regexes:
            if re.match(regex, path.as_posix()):
                return True

        if not self._includes_regexes:
            return False

        for regex in self._includes_regexes:
            if re.match(regex, path.as_posix()):
                return False

        return True

    def files(self, root: bool=True) -> Iterator[Path]:
        """
        Breadth-first iteration over all files.
        Yields: (pathname, name, item)
        """
        root = self._root if root else self._trash
        for path, cfi in root.items():
            if isinstance(cfi, FileInfo):
                yield path

    def folders(self, root: bool=True) -> Iterator[Path]:
        """
        Breadth-first iteration over all folders.
        Yields: (pathname, name, item)
        """
        root = self._root if root else self._trash
        for path, cfi in root.items():
            if isinstance(cfi, FolderInfo):
                yield path
