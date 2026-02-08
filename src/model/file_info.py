"""File and folder information classes for representing local and iCloud Drive items.

This module provides dataclasses for representing file and folder metadata from both
the local file system and iCloud Drive. It includes base classes (FileInfo, FolderInfo)
and platform-specific implementations (LocalFileInfo, LocalFolderInfo, iCloudFileInfo,
ICloudFolderInfo) that handle timestamp conversions and metadata extraction.
"""
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

    def _round_seconds(self, dt: datetime) -> datetime:
        """
        Round seconds on timestamps to handle platform differences.
        iCloud Drive stores files using UTC and rounds seconds up
        to the nearest second on Linux and down on Darwin.
        """
        if platform.system() == "Linux":
            if dt.microsecond >= 500000:
                dt += timedelta(seconds=1)
            return dt.replace(microsecond=0)
        if platform.system() == "Darwin":
            return dt.replace(microsecond=0)
        return dt

class FolderInfo(BaseInfo):
    """
    Base class for folder information.
    Extends BaseInfo with folder-specific functionality.
    """

class FileInfo(BaseInfo):
    """
    Base class for file information.
    Extends BaseInfo with file-specific functionality including size and modification time.
    """

@dataclass
class LocalFolderInfo(FolderInfo):
    """
    Represents a local folder stored on the file system.
    Stores the folder name and inherits from FolderInfo.
    """
    name: str
    @override
    def __repr__(self):
        """Return string representation of the local folder."""
        return f"FolderInfo({self.name})"

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
        """
        Initialize file metadata from os.stat_result, extracting size and timestamps
        with platform-specific rounding.
        """
        self.size: int = stat_entry.st_size
        # st_birthtime (creation time) is only available on some platforms/filesystems
        # st_ctime is inode change time on Unix, creation time on Windows
        if hasattr(stat_entry, 'st_birthtime'):
            ctime = stat_entry.st_birthtime
        else:
            ctime = stat_entry.st_ctime  # Fallback: may be change time on Linux
        self.created_time: datetime = self._round_seconds(
            datetime.fromtimestamp(ctime, tz=timezone.utc)
        )
        self.modified_time: datetime = self._round_seconds(
            datetime.fromtimestamp(
                stat_entry.st_mtime,
                tz=timezone.utc)
            )

    @override
    def __repr__(self):
        """Return string representation of the local file with size and modification time."""
        return f"FileInfo({self.name}, size={self.size}, modified={self.modified_time})"

@dataclass
class ICloudFolderInfo(FolderInfo):
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
        """Get the folder name, returning '.' for root and trash folders."""
        if self.drivewsid in (CLOUD_DOCS_ZONE_ID_ROOT, NODE_TRASH):
            return "."
        return self.node.name

    @property
    def drivewsid(self) -> str:
        """Get the unique drive workspace ID for this folder."""
        return self.node.data['drivewsid']

    @property
    def file_count(self) -> int:
        """Get the total count of files in this folder (recursive)."""
        return self.node.data['fileCount']

    @property
    def direct_children_count(self) -> int:
        """Get the count of direct child items in this folder (non-recursive)."""
        return self.node.data['directChildrenCount']

    @property
    def number_of_items(self) -> int:
        """Get the total number of items in this folder."""
        return self.node.data['numberOfItems']

    @override
    def __repr__(self):
        """Return string representation of the iCloud folder."""
        return f"FolderInfo({self.name})"

@dataclass
class ICloudFileInfo(FileInfo):
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
        """Get the file name from the iCloud Drive node."""
        return self.node.name

    @override
    @property
    def size(self) -> int:
        """Get the file size from iCloud Drive, returning 0 if not available."""
        return self.node.size if self.node.size is not None else 0

    @override
    @property
    def modified_time(self) -> datetime:
        """Get the file modification time from iCloud Drive in UTC."""
        return self.node.date_modified.replace(tzinfo=timezone.utc)

    @modified_time.setter
    def modified_time(self, modified_time) -> None:
        """Set the file modification time (for internal use)."""
        self._modified_time = modified_time

    @override
    @property
    def created_time(self) -> datetime:
        """Get the file creation time from iCloud Drive in UTC."""
        return _date_to_utc(self.node.data.get("dateCreated")
            ).replace(tzinfo=timezone.utc)

    @override
    def __repr__(self):
        """Return string representation of the iCloud file with size and modification time."""
        return f"FileInfo({self.name}, size={self.size}, modified={self.modified_time})"
