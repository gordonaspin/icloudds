import os
import logging
import re
from collections.abc import Iterator
from collections import UserDict
from threading import Lock

from model.FileInfo import BaseInfo, FileInfo, FolderInfo

logger = logging.getLogger(__name__)

class ThreadSafeDict(UserDict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._lock = Lock()

    def __setitem__(self, key, value):
        with self._lock:
            super().__setitem__(key, value)

    def __getitem__(self, key):
        with self._lock:
            return super().__getitem__(key)
        
class BaseTree():
    ROOT_FOLDER_NAME = "."

    def __init__(self, root_path: str, ignores: list[str]=None, includes: list[str]=None):
        self._root: ThreadSafeDict = ThreadSafeDict()
        self._trash: ThreadSafeDict = ThreadSafeDict()
        self._root_path : str = root_path
        self._ignores = ignores
        self._includes = includes
        self._ignores_patterns : list[str] = [
            r'.*\.com-apple-bird.*', 
            r'.*\.DS_Store'
            ]

        self._ignores_patterns.extend(ignores or [])
        self._ignores_regexes : list[re.Pattern] = [re.compile(pattern) for pattern in self._ignores_patterns]

        self._includes_list : list[str] = []
        self._includes_list.extend(includes or [])

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
    def ignores(self) -> list[str] | None:
        return self._ignores
    
    @property
    def includes(self) -> list[str] | None:
        return self._includes
    
    @property
    def ignores_patterns(self) -> list[str]:
        return self._ignores_patterns
    
    @property
    def ignores_regexes(self) -> list[re.Pattern]:
        return self._ignores_regexes
    
    @property
    def includes_list(self) -> list[str]:
        return self._includes_list
    
    def refresh(self) -> None:
        raise NotImplementedError("Subclasses should implement this method")

    def add(self, path) -> FileInfo | FolderInfo:
        raise NotImplementedError("Subclasses should implement this method")

    def ignore(self, name, isFolder: bool = False) -> bool:
        for ignore_regex in self._ignores_regexes:
            if re.match(ignore_regex, name):
                return True
        
        if not self._includes_list:
            return False
        
        for startswith in self._includes_list:
            if name.startswith(startswith):
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
