"""
Define an ICloudFolderModifiedEvent to post-process a folder change
Define QueuedEvent wrapper class in order to time-sort coalesced events
"""
from dataclasses import dataclass

from watchdog.events import DirModifiedEvent, FileSystemEvent

class ICloudFolderModifiedEvent(DirModifiedEvent):
    """
    Event used to signal that a folder in iCloud has been modified.
    This could be because files were added, removed, or changed within the folder.
    It would be typical to rescan the folder to determine the specific changes.
    """

@dataclass
class QueuedEvent:
    """
    Wrapper around watchdog FileSytemEvent to process file system events
    in time-order and to coalesce events per path. 
    """
    timestamp: float
    event: FileSystemEvent
