from watchdog.events import FileSystemEvent

class iCloudEvent(FileSystemEvent):
    pass

class iCloudFolderModifiedEvent(iCloudEvent):
    pass