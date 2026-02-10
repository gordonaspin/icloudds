"""
model.base_tree

Base tree class for managing a file/folder structure with filtering capabilities.
Provides common functionality for tracking files and folders, applying include/exclude filters,
and maintaining a trash/recycle bin for deleted items.
"""
from pathlib import Path
import logging
import re
from collections.abc import Iterator

from model.file_info import BaseInfo, FileInfo, FolderInfo
from model.thread_safe import ThreadSafePathDict

logger = logging.getLogger(__name__)


class BaseTree():
    """
    Abstract base class for managing a structure of files and folders.

    Provides filtering (ignore/include patterns), iteration methods, and manages both
    active content and a trash section for deleted items. This is intended to be subclassed
    by concrete implementations like LocalTree and ICloudTree.
    """
    ROOT_FOLDER_NAME = "."

    def __init__(self, root_path: Path, ignores: list[str] = None, includes: list[str] = None):
        """
        Initialize a BaseTree instance.

        Args:
            root_path: The root path of the tree.
            ignores: List of regex patterns for files/folders to ignore.
            includes: List of regex patterns for files/folders to explicitly include.
        """
        self._root: ThreadSafePathDict = ThreadSafePathDict()
        self._trash: ThreadSafePathDict = ThreadSafePathDict()
        self._root_path: Path = root_path
        self._ignores_patterns: list[str] = [
            r'.*\.com-apple-bird.*',
            r'.*\.DS_Store'
        ]
        self._includes_patterns: list[str] = []

        self._ignores_patterns.extend(ignores or [])
        self._ignores_regexes: list[re.Pattern] = [
            re.compile(pattern) for pattern in self._ignores_patterns]

        self._includes_patterns.extend(includes or [])
        self._includes_regexes: list[re.Pattern] = [
            re.compile(pattern) for pattern in self._includes_patterns]

    @property
    def root(self) -> ThreadSafePathDict:
        """Get the thread-safe dictionary of active files and folders in the tree."""
        return self._root

    @property
    def trash(self) -> ThreadSafePathDict:
        """Get the thread-safe dictionary of deleted files and folders in the trash."""
        return self._trash

    @property
    def root_path(self) -> Path:
        """Get the root path of the tree."""
        return self._root_path

    @property
    def ignores_patterns(self) -> list[str]:
        """Get the list of regex patterns for files/folders to ignore."""
        return self._ignores_patterns

    @property
    def ignores_regexes(self) -> list[re.Pattern]:
        """Get the compiled regex patterns for files/folders to ignore."""
        return self._ignores_regexes

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
            _root:dict=None) -> FileInfo | FolderInfo:
        """
        Add a file or folder to the tree at the specified path.
        Subclasses must implement this method with tree-specific logic.

        Args:
            path: The path to the file or folder to add.

        Returns:
            The FileInfo or FolderInfo object representing the added item.
        """
        raise NotImplementedError("Subclasses should implement this method")

    def ignore(self, path: Path | str) -> bool:
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
        if isinstance(path, Path):
            path = str(path)

        for regex in self._ignores_regexes:
            if re.match(regex, path):
                return True

        if not self._includes_regexes:
            return False

        for regex in self._includes_regexes:
            if re.match(regex, path):
                return False

        return True

    def files(self, root) -> Iterator[tuple[Path, BaseInfo]]:
        """
        Breadth-first iteration over all files.
        Yields: (pathname, name, item)
        """
        for path, cfi in root.items():
            if isinstance(cfi, FileInfo):
                yield path, cfi

    def folders(self, root) -> Iterator[tuple[Path, BaseInfo]]:
        """
        Breadth-first iteration over all folders.
        Yields: (pathname, name, item)
        """
        for path, cfi in root.items():
            if isinstance(cfi, FolderInfo):
                yield path, cfi
