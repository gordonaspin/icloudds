# `icloudds` (iCloud Drive Sync) [![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

`icloudds` is a python command-line utility designed to synchronize and maintain a local copy of your iCloud Drive. It is an improvemnt upon, and a re-write of my original iCloud Drive Sync which I no longer maintain.

- A command-line tool to synchronize your iCloud Drive with a local filesystem folder.
- Works on Linux, Windows, and MacOS.
- Run in the background or as a docker container to keep your iCloud Drive folders and contents in sync with the local filesysystem  

`icloudds`'s basic operation is as follows:
1. Scan the local file system under the --directory specified
2. Scan your iCloud Drive root and trash folders
3. Apply differences to both local and iCloud Drive (create/delete/upload/download folders and files)
4. React to local file system events under the --directory specified and apply changes as they occur to iCloud Drive
5. Check iCloud Drive and Trash for changes every --icloud-check-period seconds (default 20) and apply changes
6. Perform an iCloud Drive refresh every --icloud-refresh-period seconds (default 90), regardless of result of #5 above
6. Perform 4, 5 and 6 forever

`icloudds` scans your local filesystem under --directory and builds a model of folders and files in memory. Next it does the same for iCloud by scanning all the folders and files. It creates local folders under the --directory you provide, if needed and downloads files that have a modification date newer than those that exist. If the file does not exist locally, it is downloaded and its modification time is set to that of the iCloud Drive item. `icloudds` uploads files that are newer or don't exist in iCloud, including directories. When the upload phase is complete, `icloudds` watches the local filesystem for changes and makes the corresponding add/delete/upload to iCloud Drive. `icloudds` detects changes made to your iCloud Drive, refreshes its in-memory model and applies changes such as adds/deletes/moves/updates accordingly.

## Platforms supported
`icloudds` works on Linux, Windows and MacOS. There is no platform-dependent code in `icloudds` except for setting timestamps on filesystems. There is also a docker image that is based on python:3.14-alpine. The docker image is on docker hub at https://hub.docker.com/repository/docker/gordonaspin/icloudds/general

*Warning*: Although `icloudds` will run on MacOS, it is not advisable as a) it's not needed of course, and b) race conditions will likely cause duplication of files
## High Level Design of `icloudds`
`icloudds` generally works on a model of two lists; one is a list of all files and folders under the --directory path on the local filesystem, the second list is all the items in your iCloud drive. When `icloudds` first starts these two lists are generated and then compared. Files in iCloud missing locally are downloaded. Local files that are missing from iCloud are uploaded. Those files/folders in common have their timestamps checked. Newer files are either uploaded or downloaded. If the objects have the same date but different size, they are ignored.

One this initial sync is complete, `icloudds` starts listening for local file system events and takes the action to create, delete, rename and move items in iCloud. Similarly, `icloudds` periodically checks whether the count of items in iCloud Drive has changed. If the count has changed, a fresh copy of the iCloud list is made in the background and compared with the existing iCloud list. This helps detect items that have been created, deleted, moved, renamed etc. and the corresponding changes are made locally and applied to the local list.

### More detail
`icloudds` uses the python watchdog FileSystemEventHandler event generator. That is to say no filtering by regexes is performed by watchdog. Filtering is solely under the control of `icloudds`. When a file or folder is created, moved, deleted, etc. watchdog generates many events so `icloudds` coalesces these events before dispatching to handlers. In addition to this, when `icloudds` needs to download, rename, create files locally it temporarily suppresses events for those paths. `icloudds` does perform a sanity check on iCloud Drive refreshes. iCloud's model includes a fileCount at the root node and after a refresh `icloudds` checks its count of files/folders with what iCloud reports and if it's different, `icloudds` discards the refresh and will try later.

`icloudds` uses threads managed by the python ThreadPoolExecutor. Traversing the iCloud model can take time as multiple round-trips are required to walk the entire tree. In this respect, starting at the root node, a thread is spawned for each sub-folder to retrieve information about that folder, and again more threads are spawned for each of its subfolfers, and so-on. All these Futures are gathered and waited on until they finish. This is the fastest way to retrieve all the iCloud node information. To protect the integrity of the iCloud file list, `icloudds` uses a ThreadSafeDict (protected with a RLock).

`icloudds` uses a timepool object to register functions that run periodically. These functions run the background iCloud Drive refresh cycle and check whether iCloud Drive root or trash folders have changes, if so a refresh is called immediately.

`icloudds` writes and reads ([upload/create, rename, move, delete] and download) files using separate ThreadPoolExecutors. The _limited_threadpool has only one worker as concurrent writes to iCloud Drive can cause a ZONE_BUSY errors with reason 'Conflict'. The _unlimited_threadpool is used to download files and scan the iCloud folder structure in parallel.

### Odds and Ends
Along the way of implementing this and my prior version of iCloud Drive Sync I learned a few wierd ways that iCloud works:
1. iCloud Drive stores file timestamps in UTC, but rounds up timestamps to the nearest second. Annoying.
2. iCloud Drive has the concept of a "root" folder and a "trash" folder. You would think that moving an item to Trash would decrease the count of items in root and increase the number of items in Trash, right? No. Trash increases, but the count of items in "root" stays the same. So, I guess Trash is in root ? Except it isn't when you retrieve all the items in root. Again, Annoying.
3. Not surprisingly, there are more types of objects in iCloud Drive beyond files and folders. There are also 'app_library' objects for applications such as GarageBand, TextEdit, Readdle etc. etc. `icloudds` ignores these items.
4. If you've ever inspected a .app on MacOS you know its a package (like a zip file or tar file). When this gets stored in iCloud Drive a .app file gets expanded into a folder and its contents. This is aggravating.
5. Concurrent uploads, deletes, renames to iCloud Drive results in ZONE_BUSY errors for 'Conflict' reasons. Shame on Apple for limiting concurrent actions!

## Configurable Items
`icloudds` can ignore and include files and folders by regexes. Regexes are treated as relative to the --directory option you specifiy.

### Ignore Regexes
`icloudds` can ignore files using the --ignore-regexes. Some ignores are built-in, for example in iCloud .DS_Store files and .com-apple-bird* files are ignored using regexes. Ignore regexes are defined in files, with one regex per line, e.g.:

```code
# regexex to ignore, one per line
# built-in ignore regexes are:
.*\.com-apple-bird.*
.*\.DS_Store
# This regex ignores a specific path
scripts/iTermHere\.app
# This regex ignores all files with a .swp extention, regardles of folder location
.*\.swp
``` 

### Include Regexes
`icloudds` can include regexes listed in files specified on the command line using the --include-regexes option., e.g.:
```code
# regexes to include, one per line
# There are no built-in include regexes
ThisFolder
ThatFolder
```
In this example, if used with --include-regexes only paths (and subfolders) matching the ThisFolder and ThatFolder will be processed, everything else will be ignored.

### How it works in code
In the snippet below, "name" is a path name to a file relative from the --directory command line option. e.g. "ThisFolder/some-file-name.txt". Processing this item will be ignored if it matches with one of the regexes from the ignore regexes. If it does not match a regex, the name is matched with the include regexes. If it matches, it is included, else it is ignored.

```python
    def ignore(self, path: str) -> bool:
        """
        Determine whether a file or folder should be ignored based on include/exclude regexes.

        Logic:
        - If the name matches any ignore regex, return True (ignore it).
        - If no include regexes are defined, return False (don't ignore).
        - If include regexes exist and the name matches one, return False (don't ignore).
        - Otherwise, return True (ignore it).

        Args:
            name: The name or path to check.

        Returns:
            True if the item should be ignored, False otherwise.
        """
        for regex in self._ignore_regexes:
            if re.match(regex, path.as_posix()):
                return True

        if not self._includes_regexes:
            return False

        for regex in self._includes_regexes:
            if re.match(regex, path.as_posix()):
                return False

        return True
```
## Logging
`icloudds` uses python logging. The configuration of logging is externalized to a .json file according to the specs of python logging. You can modify this to your needs. I typically run with minimal INFO logging to stderr, and DEBUG logging to the rolling icloudds.log* files.

### State Logging
`icloudds` writes to a set of files after every refresh. These files contain information about the state of the local filesystem and the icloud folders and files. There are 5 files created:
- icloudds_local_before.log - represents what `icloudds` is tracking as the state of the local filesystem objects before a refresh is applied
- icloudds_icloud_before.log - represents what `icloudds` is tracking as the state of icloud folders and files before a refresh is applied
- icloudds_local_after.log - represents what `icloudds` is tracking as the state of the local filesystem objects after the refresh is applied
- icloudds_icloud_after.log - represents what `icloudds` is tracking as the state of icloud folders and files after the refresh is applied
- icloudds_refresh_after.log - represents what `icloudds` is tracking as the NEW state of the icloud folders and files used to make changes to local and icloud (new files, renames, deletes, moves etc.)

By default, `icloudds` creates the logging log file in the current working directory. You may specify a path in the logging-config.json and `icloudds` will create the path if needed. In addition, `icloudds` will use that path for the state logging files. If you run icloudds in the docker container, these files will be inside the container. See below how to access those files, or have them written to the host filesystem.

# Clone, Install dependencies, Build, Install and Run
`icloudds` depends on a forked version of python pyicloud library implementation pyicloud @ git+https://github.com/gordonaspin/pyicloud.git. This forked implementation has added features to properly set timestamps of objects uploaded to iCloud Drive, it also implements move functionality to move a folder, or folders to a target folder. It also resolves a retrieval limit of 200 albums (in Photos). Do not use the `pyicloud` Python package that can be installed using `pip`, it is old and does not have the required features.

To build `icloudds`, you need Python, a few dependencies, and a virtual environment if you wish. I use pyenv and venv:

### Clone
To clone the repo:
```bash
$ git clone https://github.com/gordonaspin/icloudds.git
```
You can now install dependencies, and run from source
```bash
$ cd icloudds
$ pip install -r requirements.txt
$ python src/icloudds.py -h
``` 
### Build wheel, install and run using pyenv, virtualenv and build
```bash
$ git clone https://github.com/gordonaspin/icloudds.git
$ cd icloudds
$ pyenv local 3.14.2                #optional if using pyenv and virtual environments
$ python -m venv .venv              #optional if using virtual environments
$ source .venv/bin/activate         #optional if using virtual environments
$ pip install -r requirements.txt   #optional, as installing the wheel below will install dependencies also
$ mkdir -p dist
$ rm -f dist/*
$ python -m build
$ pip install dist/*.whl
$ icloudds -h
```
### Install pyicloud from git directly 
```bash
$ pip install git+https://github.com/gordonaspin/pyicloud.git
```
or, to install the latest version of pyicloud, build and install:
```bash
$ git clone https://github.com/gordonaspin/pyicloud.git
$ cd pyicloud
$ python -m build
$ pip install dist/*.whl
```
## Usage
```bash
$ python icloudds.py -h
```
or, if you build and install the wheel from the dist/ folder
```
$ icloudds -h
Usage: icloudds.py -d <directory> -u <apple-id> [options]

  Synchronize a local folder with your iCloud Drive

Options:
  -d, --directory <directory>     Local directory that should be used for download  [required]
  -u, --username <username>       Your iCloud username or email address  [required]
  -p, --password <password>       Your iCloud password (default: use pyicloud keyring or prompt for password)
  --cookie-directory <directory>  Directory to store cookies for authentication  [default: ~/.pyicloud]
  --ignore-regexes <filename>     Ignore regular expressions  [default: .ignore-regexes.txt]
  --include-regexes <filename>    Include regular expressions  [default: .include-regexes.txt]
  --logging-config <filename>     JSON logging config filename (default: logging-config.json)  [default: logging-
                                  config.json]
  --icloud-check-period <seconds>
                                  Period in seconds to look for iCloud changes  [default: 20; x>=20]
  --icloud-refresh-period <seconds>
                                  Period in seconds to perform full iCloud refresh  [default: 90; x>=90]
  --debounce-period <seconds>     Period in seconds to queue up filesystem events  [default: 10; x>=10]
  --max-workers <workers>         Maximum number of concurrent workers  [default: 32; x>=1]
  --version                       Show the version and exit.
  -h, --help                      Show this message and exit.
```

Example:

```bash
$ icloudds --directory ./Drive --username testuser@example.com --password pass1234 
```
Command line options are mostly self-explanatory, with extra detail here:
| option              | Explanation |
|---------------------|-------------|
| icloud-check-period | The iCloud web API does not have a notification API, so `icloudds` needs to poll for changes. Every icloud-check-period, `icloudds` will make 2 web services calls; 1 to update the count of files iCloud Drive has, and 1 to update the number of items in Trash. If either of those have changed since the last refresh, a new refresh is made|
| icloud-refresh-period | This is the period of time after which a full iCloud refresh is made and differences are applied. Even though `icloudds` checks root and trash counts, changes can happen that don't make counts change. Changes such as renames, moves, equal creates/deletes etc. So `icloudds` has to periodically do a full compare. |
| debouce-period | When making changes on the local filesystem, many FileSystemEvents are generated. e.g. an update to a file will generate multiple FileModifiedEvents and a file creation will generate a FileCreatedEvent followed by multiple FileModifiedEvents. `icloudds` debounces these events by waiting for a period of silence once events are being collected.| 

## Dependencies
```code
dependencies = [
    "click==8.3.1",
    "pyicloud @ git+https://github.com/gordonaspin/pyicloud.git",
    "watchdog==6.0.0",
    "timeloop-ng==1.0.0",
    "fasteners==0.20",
]
```
`click` for command-line options
`pyicloud` for sending http requests to iCloud
`watchdog` to watch the filesystem and generate events
`timeloop` to schedule functions based on time
`fasteners` to limit icloudds to one instance running

## Authentication

If your Apple account has two-factor authentication enabled, you will be prompted for a code when you run `icloudds`

Two-factor authentication will expire after an interval set by Apple, at which point you will have to re-authenticate. This interval is currently two months.

Authentication cookies will be stored in a temp directory (`~/.pyicloud` on Linux) This directory can be configured with the `--cookie-directory` option.

## Error on first run

When you run the script for the first time, you might see an error message like this:

``` plain
Bad Request (400)
```

This error often happens because your account hasn't used the iCloud API before, so Apple's servers need to prepare some information about your iCloud Drive. This process can take around 5-10 minutes, so please wait a few minutes and try again.

If you are still seeing this message after 30 minutes, then please [open an issue on GitHub](https://github.com/gordonaspin/icloudds/issues/new) and post the script output.

# Docker

This script is available in a Docker image on Docker Hub:
```bash
$ docker pull gordonaspin/icloudds:latest
```
The image defines an entrypoint:
```bash
ENTRYPOINT [ "icloudds", "-d", "/drive", "--cookie-directory", "/cookies" ]
```
### On Linux:

```bash
# Downloads all iCloud Drive items to ./Drive

$ docker pull gordonaspin/icloudds:latest
$ docker run -it --name icloudds \
    -v $(pwd)/Drive:/drive \
    -v $(pwd)/cookies:/cookies \
    gordonaspin/icloudds:latest \
    --username testuser@example.com 
```

### On Windows:

- use `%cd%` instead of `$(pwd)`
- or full path, e.g. `-v c:/icloud/Drive:/drive`

### Building the docker image

#### Building docker image from this repo:
```bash
$ docker build --tag your-repo/icloudds:latest --progress=plain -f ./Dockerfile
```
#### Building docker image from local source code and gordonaspin/pyicloud repo image locally:
```bash
$ docker build --tag your-repo/icloudds:latest --progress=plain -f ./Dockerfile.local
```
### Running the docker image
```bash
# Optionally, run the pyicloud icloud command line utility.
# This will create a python keyring in the container for future use, and cookies will go to ~/.pyicloud in the container
$ docker exec -it icloud --username your-icloud-email-address --password your-icloud-password
# Run icloudds in the container, specifying external mounted folders for the directory and cookies folders
$ docker run -it --restart=always --name icloudds -v "path/to/directory":/drive -v ~/.pyicloud:/cookies <your-repo>/icloudds:latest -u username@email.com 
```
The container as-built has verbose DEBUG logging to file enabled and terse logging to stderr. The terse logging is what you see with `docker logs icloudds`. The verbose DEBUG log files are inside the container and rollover 3 times as icloudds.log, icloudds.log.1, icloudds.log.2 and icloudds.log.3. These are accessible for example like so:
```bash
$ docker exec -it icloudds /bin/bash -c "tail -F icloudds.log"
# or
$ docker cp icloudds icloudds.log .
$ docker cp icloudds icloudds.log.1 .
```
You can modify the logging-config.json by editing it inside the container like so:
```bash
$ docker exec -it icloudds /bin/bash -c "vi logging-config.json"
```
Using this approach is easy, but will not persist if you delete the container. An example of moving the include/exclude files and logging-config.json to outside the container would look something like this:
Suppose you specify a path in the logging-config.json as log/icloudds.log. This will result in log files in the container in the /home/docker/log folder. You can map a host folder to be mounted at /home/docker/log inside the container. This will externalize and persist log files outside the container.

```bash
$ docker run -it --restart=always --name icloudds -v "path/to/directory":/drive -v ~/.pyicloud:/cookies -v "/var/log":/home/docker/log <your-repo>/icloudds:latest -u username@email.com 
```
The container has the default .ignore-regexes.txt, .include-regexes.txt and logging-config.json files in the /home/docker folder of the docker user and these are used by default. To change this you can edit the files in the container in /home/docker, or override by copying the files to a folder on the host and edit them and map that folder to /home/docker. Or you can build your own container with the contents changed, or you can specify the --include-regexes and --ignore-regexes command line arguments that refer to a path you mount to the container. e.g.:

#### Unchanged container, mount host folder over /home/docker:
```bash
# Copy and edit the config files as needed
$ cd ~/somepath
$ docker cp icloudds:.include-regexes.txt .
$ docker cp icloudds:.ignore-regexes.txt .
$ docker cp icloudds:logging-config.json .

$ docker run -it --name icloudds -v ~/iCloud\ Drive:/drive -v ~/.pyicloud:/cookies your-repo/icloudds -v ~/somepath:/home/docker -u username@email.com
# This will result in log files and state log files appearing in the ~/somepath folder (external to the container)
```
#### Unchanged container, mount a cfg folder and provide command line arguments
```bash
$ docker run -it --name icloudds -v ~/iCloud\ Drive:/drive -v ~/.pyicloud:/cookies your-repo/icloudds -v ~/.config:/cfg -u username@email.com --ignore-icloud /cfg/<filename> --ignore-local /cfg/<filename> --include-regexes /cfg/<filename> --exclude-regexes /cfg/<filename> --logging-config /cfg/logging-config.json
```
In the example above, you would have your ignore files, include files and logging-config.json files in your `~/.config` folder and refer to them on the command line as `/cfg/filename`