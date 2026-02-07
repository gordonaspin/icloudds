import os
import logging
import re
from collections.abc import Iterator

from model.file_info import BaseInfo, FileInfo, FolderInfo
from model.thread_safe import ThreadSafeDict

logger = logging.getLogger(__name__)

class BaseTree():
    ROOT_FOLDER_NAME = "."

    def __init__(self, root_path: str, ignores: list[str]=None, includes: list[str]=None):
        self._root: ThreadSafeDict = ThreadSafeDict()
        self._trash: ThreadSafeDict = ThreadSafeDict()
        self._root_path : str = root_path
        self._ignores_patterns : list[str] = [
            r'.*\.com-apple-bird.*', 
            r'.*\.DS_Store'
            ]
        self._includes_patterns: list[str] = []
        
        self._ignores_patterns.extend(ignores or [])
        self._ignores_regexes : list[re.Pattern] = [re.compile(pattern) for pattern in self._ignores_patterns]

        self._includes_patterns.extend(includes or [])
        self._includes_regexes : list[re.Pattern] = [re.compile(pattern) for pattern in self._includes_patterns]

    @property
    def root(self) -> ThreadSafeDict:
        return self._root
    
    @property
    def trash(self) -> ThreadSafeDict:
        return self._trash
    
    @property
    def root_path(self) -> str:
        return self._root_path
    
    @property
    def ignores_patterns(self) -> list[str]:
        return self._ignores_patterns
    
    @property
    def ignores_regexes(self) -> list[re.Pattern]:
        return self._ignores_regexes

    @property
    def includes_patterns(self) -> list[str]:
        return self._includes_patterns
        
    @property
    def includes_regexes(self) -> list[str]:
        return self._includes_regexes
    
    def refresh(self) -> None:
        raise NotImplementedError("Subclasses should implement this method")

    def add(self, path) -> FileInfo | FolderInfo:
        raise NotImplementedError("Subclasses should implement this method")

    def ignore(self, name, isFolder: bool = False) -> bool:
        for regex in self._ignores_regexes:
            if re.match(regex, name):
                return True
        
        if not self._includes_regexes:
            return False
        
        for regex in self._includes_regexes:
            if re.match(regex, name):
                return False
                
        return True
    
    def files(self, root) -> Iterator[tuple[str, str, BaseInfo]]:
        """
        Breadth-first iteration over all files.
        Yields: (pathname, name, item)
        """
        for key, value in root.items():
            if isinstance(value, FileInfo):
                path = os.path.dirname(key)
                path = BaseTree.ROOT_FOLDER_NAME if not path else path
                yield root[path], key, value

    def folders(self, root) -> Iterator[tuple[str, str, BaseInfo]]:
        """
        Breadth-first iteration over all folders.
        Yields: (pathname, name, item)
        """
        for key, value in root.items():
            if isinstance(value, FolderInfo):
                path = os.path.dirname(key)
                path = BaseTree.ROOT_FOLDER_NAME if not path else path
                yield root[path], key, value
