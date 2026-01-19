from dataclasses import dataclass
from typing import Callable

@dataclass
class ActionResult:
    success: bool
    fn: Callable = None
    args: list = None
    exception: Exception = None

class DownloadActionResult(ActionResult):
    pass

class UploadActionResult(ActionResult):
    pass

class ProcessFolderResult(ActionResult):
    pass

