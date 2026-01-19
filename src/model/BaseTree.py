import os
import logging
import re
from collections.abc import Iterator

from model.FileInfo import BaseInfo, FileInfo, FolderInfo

logger = logging.getLogger(__name__)

class BaseTree():
    ROOT_FOLDER_NAME = "."

    def __init__(self, root_path: str, ignores: list[str]=None, includes: list[str]=None):
        self._root : dict[str, BaseInfo] = {}
        self._root_path : str = root_path
        self._ignores = ignores
        self._includes = includes
        self._ignores_patterns : list[str] = [
            r'.*\.com-apple-bird.*', 
            r'.*\.DS_Store'
            ]

        self._ignores_patterns.extend(ignores or [])
        self._ignores_regexes : list[re.Pattern] = [re.compile(pattern) for pattern in self._ignores_patterns]

        self._includes_patterns : list[str] = []
        self._includes_patterns.extend(includes or [])

    @property
    def root(self) -> BaseInfo:
        return self._root
    
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
    def includes_patterns(self) -> list[str]:
        return self.includes_patterns
    
    def refresh(self) -> None:
        raise NotImplementedError("Subclasses should implement this method")

    def add(self, path) -> FileInfo | FolderInfo:
        raise NotImplementedError("Subclasses should implement this method")

    def ignore(self, name, isFolder: bool = False) -> bool:
        for ignore_regex in self._ignores_regexes:
            if re.match(ignore_regex, name):
                return True
        
        if not self._includes_patterns:
            return False
        
        for startswith_pattern in self._includes_patterns:
            if name.startswith(startswith_pattern):
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
                path = BaseTree.ROOT_FOLDER_NAME if path == "" else path
                yield root[path], key, value

    def folders(self, root) -> Iterator[tuple[str, str, BaseInfo]]:
        """
        Breadth-first iteration over all folders.
        Yields: (pathname, name, item)
        """
        for key, value in root.items():
            if isinstance(value, FolderInfo):
                path = os.path.dirname(key)
                path = BaseTree.ROOT_FOLDER_NAME if path == "" else path
                yield root[path], key, value
