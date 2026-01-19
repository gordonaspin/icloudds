from __future__ import annotations
from typing import override
import os
import platform
from datetime import datetime, timezone, timedelta

from pyicloud.services.drive import DriveNode, _date_to_utc

class BaseInfo():
    @property
    def name(self) -> str:
        pass

    def _round_seconds(self, obj: datetime) -> datetime:
        """iCloud Drive stores files in the cloud using UTC, however it rounds the seconds up to the nearest second"""
        if platform.system() == "Linux":
            if obj.microsecond >= 500000:
                obj += timedelta(seconds=1)
            return obj.replace(microsecond=0)
        elif platform.system() == "Darwin":
            return obj.replace(microsecond=0)

class FolderInfo(BaseInfo):
    @override
    @property
    def name(self) -> str:
        pass

    @property
    def modified_time(self) -> datetime:
        pass
        
    def __repr__(self) -> str:
        return f"FolderInfo(name={self.name}, modified_time={self.modified_time.isoformat()})"
    
    def __str__(self) -> str:
        return f"{self.name}, modified_time={self.modified_time.isoformat()})"

class FileInfo(BaseInfo):
    @override
    @property
    def name(self) -> str:
        pass

    @property
    def size(self) -> int:
        pass

    @property
    def modified_time(self) -> datetime:
        pass

    def __repr__(self) -> str:
        return f"FileInfo(name={self.name}, size={self.size}, modified_time={self.modified_time.isoformat()})"
    
    def __str__(self) -> str:
        return f"{self.name} (size={self.size}, modified_time={self.modified_time.isoformat()})"

class LocalFolderInfo(FolderInfo):
    def __init__(self, name: str, stat_entry: os.stat_result):
        self._name: str = name
        self._modified_time = self._round_seconds(datetime.fromtimestamp(stat_entry.st_mtime, tz=timezone.utc))
        self._created_time = self._round_seconds(datetime.fromtimestamp(stat_entry.st_ctime, tz=timezone.utc))

    @override
    @property
    def name(self) -> str:
        return self._name

    @override
    @property
    def modified_time(self) -> datetime:
        return self._modified_time
    
    @modified_time.setter
    def modified_time(self, modified_time) -> None:
        self._modified_time = modified_time

    @override
    @property
    def created_time(self) -> datetime:
        return self._created_time
    
    @created_time.setter
    def created_time(self, created_time) -> None:
        self._created_time = created_time

class LocalFileInfo(FileInfo):
    def __init__(self, name: str, stat_entry: os.stat_result):
        self._name: str = name
        self._size: int = stat_entry.st_size
        self._created_time: datetime = self._round_seconds(datetime.fromtimestamp(stat_entry.st_ctime, tz=timezone.utc))
        self._modified_time: datetime = self._round_seconds(datetime.fromtimestamp(stat_entry.st_mtime, tz=timezone.utc))
        
    @override
    @property
    def name(self) -> str:
        return self._name

    @override
    @property
    def size(self) -> int:
        return self._size
    
    @size.setter
    def size(self, size) -> None:
        self._size = size

    @override
    @property
    def modified_time(self) -> datetime:
        return self._modified_time
    
    @modified_time.setter
    def modified_time(self, modified_time) -> None:
        self._modified_time = modified_time

    @override
    @property
    def created_time(self) -> datetime:
        return self._created_time

    @created_time.setter
    def created_time(self, created_time) -> None:
        self._created_time = created_time


class iCloudFolderInfo(FolderInfo):
    def __init__(self, node: DriveNode):
        self._node: DriveNode = node
        self._modified_time = datetime.min.replace(tzinfo=timezone.utc)

    @property
    def node(self) -> DriveNode:
        return self._node
    
    @override
    @property
    def name(self) -> str:
        return self._node.name

    @override
    @property
    def size(self) -> int:
        return self._node.size

    @override
    @property
    def modified_time(self) -> datetime:
        return self._modified_time
        #return self._node.date_modified.replace(tzinfo=timezone.utc)
    
    @modified_time.setter
    def modified_time(self, modified_time) -> None:
        self._modified_time = modified_time

    @override
    @property
    def created_time(self) -> datetime:
        return self._created_time

    @created_time.setter
    def created_time(self, created_time) -> None:
        self._created_time = created_time    

class iCloudFileInfo(FileInfo):
    def __init__(self, node: DriveNode):
        self._node: DriveNode = node

    @property
    def node(self) -> DriveNode:
        return self._node

    @override
    @property
    def name(self) -> str:
        return self._node.name

    @override
    @property
    def size(self) -> int:
        return self._node.size if self._node.size is not None else 0

    @size.setter
    def size(self, size) -> None:
        self._size = size

    @override
    @property
    def modified_time(self) -> datetime:
        return self._node.date_modified.replace(tzinfo=timezone.utc)
    
    @modified_time.setter
    def modified_time(self, modified_time) -> None:
        self._modified_time = modified_time

    @override
    @property
    def created_time(self) -> datetime:
        return _date_to_utc(self._node.data.get("dateCreated"))  # Folder does not have date


    