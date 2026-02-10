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
        else:
            self.src_path = src_path
            self.dest_path = ''

    def __str__(self) -> str:
        return (f"{type(self).__name__}("
                f"src={self.src_path!s}, "
                f"dest={self.dest_path!s}, "
                f"is_directory={self.is_directory})"
                )

class ICDSFileCreatedEvent(ICDSSystemEvent):
    """
    Docstring for ICDSFileCreatedEvent
    """
    def __init__(self, e: FileCreatedEvent=None, src_path: Path=Path(), absolute_path: Path=Path()):
        super().__init__(e, src_path, absolute_path)
        self.is_directory = False

class ICDSFileModifiedEvent(ICDSSystemEvent):
    """
    Docstring for ICDSFileModifiedEvent
    """
    def __init__(self, e: FileModifiedEvent=None, src_path: Path=None, absolute_path: Path=None):
        super().__init__(e, src_path, absolute_path)

class ICDSFileMovedEvent(ICDSSystemEvent):
    """
    Docstring for ICDSFileMovedEvent
    """
    def __init__(self, e: FileMovedEvent=None, src_path: Path=None, absolute_path: Path=None):
        super().__init__(e, src_path, absolute_path)
        self.is_directory = False

class ICDSFileDeletedEvent(ICDSSystemEvent):
    """
    Docstring for ICDSFileDeletedEvent
    """
    def __init__(self, e: FileDeletedEvent=None, src_path: Path=None, absolute_path: Path=None):
        super().__init__(e, src_path, absolute_path)
        self.is_directory = False

class ICDSFolderCreatedEvent(ICDSSystemEvent):
    """
    Docstring for ICDSFolderCreatedEvent
    """
    def __init__(self, e: DirCreatedEvent=None, src_path: Path=None, absolute_path: Path=None):
        super().__init__(e, src_path, absolute_path)
        self.is_directory = True

class ICDSFolderModifiedEvent(ICDSSystemEvent):
    """
    Docstring for ICDSFolderModifiedEvent
    """
    def __init__(self, e: DirModifiedEvent=None, src_path: Path=None, absolute_path: Path=None):
        super().__init__(e, src_path, absolute_path)
        self.is_directory = True

class ICDSFolderMovedEvent(ICDSSystemEvent):
    """
    Docstring for ICDSFolderMovedEvent
    """
    def __init__(self, e: DirMovedEvent=None, src_path: Path=None, absolute_path: Path=None):
        super().__init__(e, src_path, absolute_path)
        self.is_directory = True

class ICDSFolderDeletedEvent(ICDSSystemEvent):
    """
    Docstring for ICDSFolderDeletedEvent
    """
    def __init__(self, e: DirDeletedEvent=None, src_path: Path=None, absolute_path: Path=None):
        super().__init__(e, src_path, absolute_path)
        self.is_directory = True

class ICloudFolderModifiedEvent(ICDSSystemEvent):
    """
    Event used to signal that a folder in iCloud has been modified.
    This could be because files were added, removed, or changed within the folder.
    It would be typical to rescan the folder to determine the specific changes.
    """
    def __init__(self, src_path: Path):
        super().__init__(None, src_path, None)
        self.is_directory = True

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
