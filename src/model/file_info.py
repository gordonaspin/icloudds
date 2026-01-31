from __future__ import annotations
from typing import override
import os
import platform
from dataclasses import dataclass, InitVar
from datetime import datetime, timezone, timedelta

from pyicloud.services.drive import DriveNode, _date_to_utc, CLOUD_DOCS_ZONE_ID_ROOT, NODE_TRASH

class BaseInfo:
    """
    Base class for file and folder information objects.
    Provides common utility methods for handling timestamps across different platforms.
    """
    def _round_seconds(self, obj: datetime) -> datetime:
        """iCloud Drive stores files in the cloud using UTC, however it rounds the seconds up to the nearest second"""
        if platform.system() == "Linux":
            if obj.microsecond >= 500000:
                obj += timedelta(seconds=1)
            return obj.replace(microsecond=0)
        elif platform.system() == "Darwin":
            return obj.replace(microsecond=0)


class FolderInfo(BaseInfo):
    """
    Base class for folder information.
    Extends BaseInfo with folder-specific functionality.
    """
    @override
    def __repr__(self):
        return f"FolderInfo({self.name})"

class FileInfo(BaseInfo):
    """
    Base class for file information.
    Extends BaseInfo with file-specific functionality including size and modification time.
    """
    @override
    def __repr__(self):
        return f"FileInfo({self.name}, size={self.size}, modified={self.modified_time})"

@dataclass
class LocalFolderInfo(FolderInfo):
    """
    Represents a local folder stored on the file system.
    Stores the folder name and inherits from FolderInfo.
    """
    name: str


@dataclass
class LocalFileInfo(FileInfo):
    """
    Represents a local file stored on the file system.
    Extracts and stores file metadata (size, created time, modified time) from os.stat().
    Handles platform-specific timestamp rounding (Linux rounds up, Darwin rounds down).
    """
    name: str
    stat_entry: InitVar[os.stat_result]
    size: int = 0
    modified_time: datetime = None
    created_time: datetime = None

    def __post_init__(self, stat_entry):
        self.size: int = stat_entry.st_size
        self.created_time: datetime = self._round_seconds(datetime.fromtimestamp(stat_entry.st_ctime, tz=timezone.utc))
        self.modified_time: datetime = self._round_seconds(datetime.fromtimestamp(stat_entry.st_mtime, tz=timezone.utc))
    
@dataclass
class iCloudFolderInfo(FolderInfo):
    """
    Represents a folder in iCloud Drive.
    Wraps a DriveNode object and provides properties to access folder metadata.
    Properties are retrieved dynamically from the underlying DriveNode data.
    Handles special cases for root and trash folders.
    """
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

@dataclass
class iCloudFileInfo(FileInfo):
    """
    Represents a file in iCloud Drive.
    Wraps a DriveNode object and provides properties to access file metadata.
    Properties are retrieved dynamically from the underlying DriveNode data.
    Handles timezone conversion for iCloud timestamps to UTC.
    """
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
