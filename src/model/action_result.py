from dataclasses import dataclass
from typing import Callable

@dataclass
class ActionResult:
    success: bool
    path: str
    fn: Callable = None
    args: list = None
    exception: Exception = None
    name = "ActionResult"

    def __str__(self):
        return f"{self.__class__.__name__.lower()} {'succeeded' if self.success else 'failed'} {self.path}"

class Nil(ActionResult):
    def __init__(self):
        self.success = True
        self.path = ""

class Download(ActionResult):
    pass

class Upload(ActionResult):
    pass

class Rename(ActionResult):
    pass
        
class Delete(ActionResult):
    pass

class MkDir(ActionResult):
    pass

class Refresh(ActionResult):
    pass