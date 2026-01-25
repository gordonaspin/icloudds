from __future__ import annotations
from typing import override
import os
import platform
from dataclasses import dataclass, InitVar
from datetime import datetime, timezone, timedelta

from pyicloud.services.drive import DriveNode, _date_to_utc, CLOUD_DOCS_ZONE_ID_ROOT, NODE_TRASH

class BaseInfo:
    def _round_seconds(self, obj: datetime) -> datetime:
        """iCloud Drive stores files in the cloud using UTC, however it rounds the seconds up to the nearest second"""
        if platform.system() == "Linux":
            if obj.microsecond >= 500000:
                obj += timedelta(seconds=1)
            return obj.replace(microsecond=0)
        elif platform.system() == "Darwin":
            return obj.replace(microsecond=0)


class FolderInfo(BaseInfo):
    pass

class FileInfo(BaseInfo):
    pass

@dataclass
class LocalFolderInfo(FolderInfo):
    name: str

    @override
    def __repr__(self):
        return f"FolderInfo({self.name})"

@dataclass
class LocalFileInfo(FileInfo):
    name: str
    stat_entry: InitVar[os.stat_result]
    size: int = 0
    modified_time: datetime = None
    created_time: datetime = None

    def __post_init__(self, stat_entry):
        self.size: int = stat_entry.st_size
        self.created_time: datetime = self._round_seconds(datetime.fromtimestamp(stat_entry.st_ctime, tz=timezone.utc))
        self.modified_time: datetime = self._round_seconds(datetime.fromtimestamp(stat_entry.st_mtime, tz=timezone.utc))

    @override
    def __repr__(self):
        return f"FileInfo({self.name}, size={self.size}, modified={self.modified_time})"
    
@dataclass
class iCloudFolderInfo(FolderInfo):
    node: DriveNode

    @override
    @property
    def name(self) -> str:
        if self.drivewsid == CLOUD_DOCS_ZONE_ID_ROOT or self.drivewsid == NODE_TRASH:
            return "."
        return self.node.name
    
    @property
    def drivewsid(self) -> str:
        return self.node.data['drivewsid']
    
    @property
    def file_count(self) -> int:
        return self.node.data['fileCount']
    
    @property
    def direct_children_count(self) -> int:
        return self.node.data['directChildrenCount']
    
    @property
    def number_of_items(self) -> int:
        return self.node.data['numberOfItems']

    @override
    def __repr__(self):
        return f"FolderInfo({self.name})"

@dataclass
class iCloudFileInfo(FileInfo):
    node: DriveNode

    @override
    @property
    def name(self) -> str:
        return self.node.name

    @override
    @property
    def size(self) -> int:
        return self.node.size if self.node.size is not None else 0

    @override
    @property
    def modified_time(self) -> datetime:
        return self.node.date_modified.replace(tzinfo=timezone.utc)
    
    @modified_time.setter
    def modified_time(self, modified_time) -> None:
        self._modified_time = modified_time

    @override
    @property
    def created_time(self) -> datetime:
        return _date_to_utc(self.node.data.get("dateCreated")).replace(tzinfo=timezone.utc)  # Folder does not have date
    
    @override
    def __repr__(self):
        return f"FileInfo({self.name}, size={self.size}, modified={self.modified_time})"
    