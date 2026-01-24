import os
import logging
from typing import override

from context import Context
from model.BaseTree import BaseTree
from model.FileInfo import LocalFileInfo, LocalFolderInfo

logger = logging.getLogger(__name__)

class LocalTree(BaseTree):
    def __init__(self, ctx: Context):
        self.ctx = ctx
        local_root = local_root if ctx.directory.endswith(os.sep) else ctx.directory + os.sep
        local_root = ctx.directory
        super().__init__(root_path=local_root, ignores=self.ctx.ignore_local, includes=self.ctx.include_icloud)

    @override
    def refresh(self):
        logger.debug(f"Refreshing Local Drive {self._root_path}...")
        if self._root == {}:
            self._root[BaseTree.ROOT_FOLDER_NAME] = LocalFolderInfo(BaseTree.ROOT_FOLDER_NAME)
        self._add_children(self._root_path)
        logger.debug(f"Refresh local complete root has {len(self.root)} items, {sum(1 for _ in self.folders(self.root))} folders, {sum(1 for _ in self.files(self.root))} files")

    @override
    def add(self, path) -> LocalFileInfo | LocalFolderInfo:
        parent_path = os.path.dirname(path)
        folder_path = BaseTree.ROOT_FOLDER_NAME
        for folder_name in parent_path.split(os.sep):
            folder_path = os.path.normpath(os.path.join(folder_path, folder_name))
            self._root[folder_path] = LocalFolderInfo(name=folder_name) #, stat_entry=os.stat(os.path.join(self._root_path, folder_path)))

        stat_entry = os.stat(os.path.join(self._root_path, path))
        if os.path.isfile(os.path.join(self._root_path, path)):
            self._root[path] = LocalFileInfo(name=os.path.basename(path), stat_entry=stat_entry)
        elif os.path.isdir(os.path.join(self._root_path, path)):
            self._root[path] = LocalFolderInfo(name=os.path.basename(path))#, stat_entry=stat_entry)
        return self._root[path]
    
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
                        logger.debug(f"Local file {path}")
                        self._root[path] = LocalFileInfo(name=entry.name, stat_entry=stat_entry)
                    elif entry.is_dir(follow_symlinks=True):
                        if self.ignore(path):
                            continue
                        logger.debug(f"Local folder {path}")
                        self._root[path] = LocalFolderInfo(name=entry.name) #, stat_entry=stat_entry)
                        self._add_children(entry.path)
        except PermissionError:
            pass  # Skip unreadable directories
