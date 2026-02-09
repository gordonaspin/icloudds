"""
model.action_result. Classes representing success or failure of asynchronous threaded call
to iCloud to perform an action
"""
from pathlib import Path
from dataclasses import dataclass
from typing import Callable

@dataclass
class ActionResult:
    """
    Base class representing the result of an asynchronous action taken on iCloud Drive.
    
    Attributes:
        success: Whether the action completed successfully.
        path: The primary path affected by the action.
        dest_path: The destination path (for move/rename operations).
        fn: The callable function that performed the action (for retry purposes).
        args: Arguments to pass to fn when retrying the action.
        exception: The exception raised if the action failed.
    """
    success: bool
    path: Path
    dest_path: Path | None = None
    fn: Callable | None = None
    args: list | None = None
    exception: Exception | None = None

    def __str__(self):
        return f"{self.__class__.__name__.lower()}{'' if self.success else ' failed'} {self.path}"

class Nil(ActionResult):
    """
    Represents a no-op action result (no action taken).
    """
    def __init__(self):
        super().__init__(success=True, path="")

class Download(ActionResult):
    """
    Represents the result of downloading a file from iCloud Drive to the local filesystem.
    """

class Upload(ActionResult):
    """
    Represents the result of uploading a file from the local filesystem to iCloud Drive.
    """

class Rename(ActionResult):
    """
    Represents the result of renaming a file or folder in iCloud Drive.
    """

class Move(ActionResult):
    """
    Represents the result of moving a file or folder to a different directory in iCloud Drive.
    Requires both path (source) and dest_path (destination).
    """

class Delete(ActionResult):
    """
    Represents the result of deleting a file or folder from iCloud Drive.
    """

class MkDir(ActionResult):
    """
    Represents the result of creating a folder in iCloud Drive.
    """

class Refresh(ActionResult):
    """
    Represents the result of refreshing the iCloud Drive tree.
    """
