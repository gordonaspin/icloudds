"""
Define an ICloudFolderModifiedEvent to post-process a folder change
Define QueuedEvent wrapper class in order to time-sort coalesced events
"""
from pathlib import Path
from dataclasses import dataclass

from watchdog.events import (
    FileSystemEvent,
    FileCreatedEvent,
    FileModifiedEvent,
    FileDeletedEvent,
    FileMovedEvent,
    DirCreatedEvent,
    DirModifiedEvent,
    DirMovedEvent,
    DirDeletedEvent,
)

class ICDSSystemEvent():
    """
    Docstring for ICDSSystemEvent
    """
    def __init__(self, e: FileSystemEvent=None, src_path:Path=Path(), absolute_path: Path=Path()):
        if e:
            self.src_path = src_path if src_path else Path(
                e.src_path).relative_to(absolute_path or '')
            if len(str(e.dest_path)) > 0:
                self.dest_path = Path(e.dest_path).relative_to(absolute_path or '')
            else:
                self.dest_path = ''
            self.is_directory = e.is_directory
            self.event_type = e.event_type
            self.is_synthetic = e.is_synthetic
        else:
            self.src_path = src_path
            self.dest_path = ''

    def __str__(self) -> str:
        parts = [f"type={getattr(self, 'event_type', None)}",
                 f"src={self.src_path!s}"]
        if getattr(self, 'dest_path', None):
            parts.append(f"dest={self.dest_path!s}")
        parts.append(f"dir={getattr(self, 'is_directory', False)}")
        if getattr(self, 'is_synthetic', False):
            parts.append("synthetic=True")
        return f"ICDSSystemEvent({', '.join(parts)})"


class ICDSFileCreatedEvent(ICDSSystemEvent):
    """
    Docstring for ICDSFileCreatedEvent
    """
    def __init__(self, e: FileCreatedEvent=None, src_path: Path=Path(), absolute_path: Path=Path()):
        super().__init__(e, src_path, absolute_path)
        self.event_type = "created"

    def __str__(self) -> str:
        return f"ICDSFileCreatedEvent(src={self.src_path!s})"

class ICDSFileModifiedEvent(ICDSSystemEvent):
    """
    Docstring for ICDSFileModifiedEvent
    """
    def __init__(self, e: FileModifiedEvent=None, src_path: Path=None, absolute_path: Path=None):
        super().__init__(e, src_path, absolute_path)
        self.event_type = "modified"

    def __str__(self) -> str:
        return f"ICDSFileModifiedEvent(src={self.src_path!s})"

class ICDSFileMovedEvent(ICDSSystemEvent):
    """
    Docstring for ICDSFileMovedEvent
    """
    def __init__(self, e: FileMovedEvent=None, src_path: Path=None, absolute_path: Path=None):
        super().__init__(e, src_path, absolute_path)
        self.event_type = "moved"

    def __str__(self) -> str:
        return f"ICDSFileMovedEvent(src={self.src_path!s}, dest={self.dest_path!s})"

class ICDSFileDeletedEvent(ICDSSystemEvent):
    """
    Docstring for ICDSFileDeletedEvent
    """
    def __init__(self, e: FileDeletedEvent=None, src_path: Path=None, absolute_path: Path=None):
        super().__init__(e, src_path, absolute_path)
        self.event_type = "deleted"

    def __str__(self) -> str:
        return f"ICDSFileDeletedEvent(src={self.src_path!s})"

class ICDSFolderCreatedEvent(ICDSSystemEvent):
    """
    Docstring for ICDSFolderCreatedEvent
    """
    def __init__(self, e: DirCreatedEvent=None, src_path: Path=None, absolute_path: Path=None):
        super().__init__(e, src_path, absolute_path)
        self.event_type = "created"

    def __str__(self) -> str:
        return f"ICDSFolderCreatedEvent(src={self.src_path!s})"

class ICDSFolderModifiedEvent(ICDSSystemEvent):
    """
    Docstring for ICDSFolderModifiedEvent
    """
    def __init__(self, e: DirModifiedEvent=None, src_path: Path=None, absolute_path: Path=None):
        super().__init__(e, src_path, absolute_path)
        self.event_type = "modified"

    def __str__(self) -> str:
        return f"ICDSFolderModifiedEvent(src={self.src_path!s})"

class ICDSFolderMovedEvent(ICDSSystemEvent):
    """
    Docstring for ICDSFolderMovedEvent
    """
    def __init__(self, e: DirMovedEvent=None, src_path: Path=None, absolute_path: Path=None):
        super().__init__(e, src_path, absolute_path)
        self.event_type = "moved"

    def __str__(self) -> str:
        return f"ICDSFolderMovedEvent(src={self.src_path!s}, dest={self.dest_path!s})"

class ICDSFolderDeletedEvent(ICDSSystemEvent):
    """
    Docstring for ICDSFolderDeletedEvent
    """
    def __init__(self, e: DirDeletedEvent=None, src_path: Path=None, absolute_path: Path=None):
        super().__init__(e, src_path, absolute_path)
        self.event_type = "deleted"

    def __str__(self) -> str:
        return f"ICDSFolderDeletedEvent(src={self.src_path!s})"

class ICloudFolderModifiedEvent(ICDSFolderModifiedEvent):
    """
    Event used to signal that a folder in iCloud has been modified.
    This could be because files were added, removed, or changed within the folder.
    It would be typical to rescan the folder to determine the specific changes.
    """
    def __init__(self, src_path: Path):
        super().__init__(None, src_path, None)
        self.event_type = "refreshed"

    def __str__(self) -> str:
        return f"ICloudFolderModifiedEvent(src={self.src_path!s})"

@dataclass
class QueuedEvent:
    """
    Wrapper around watchdog FileSytemEvent to process file system events
    in time-order and to coalesce events per path. 
    """
    timestamp: float
    event: FileSystemEvent

    def __str__(self) -> str:
        return f"QueuedEvent(time={self.timestamp}, event={self.event})"
