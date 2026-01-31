from dataclasses import dataclass
from typing import Callable

@dataclass
class ActionResult:
    success: bool
    path: str
    fn: Callable = None
    args: list = None
    exception: Exception = None

class NoAction(ActionResult):
    def __init__(self):
        self.success = True
        self.path = ""

class DownloadAction(ActionResult):
    pass

class UploadAction(ActionResult):
    pass

class RenameAction(ActionResult):
    pass

class DeleteAction(ActionResult):
    pass

class CreateFolderAction(ActionResult):
    pass
