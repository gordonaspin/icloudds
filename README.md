# `icloudds` (iCloud Drive Sync) [![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

`icloudds` is a python command-line utility designed to synchronize and maintain a local copy of your iCloud Drive. It is an improvemnt upon, and a re-write of my original iCloud Drive Sync which I no longer maintain.

- A command-line tool to synchronize your iCloud Drive with a local filesystem folder.
- Works on Linux, Windows, and MacOS.
- Run in the background to keep your iCloud Drive folders and contents in sync with the local filesysystem  

`icloudds`'s basic operation is as follows:
1. Scan the local file system under the --directory specified
2. Scan your iCloud Drive root and trash folders
3. Apply differences to both local and iCloud (create/delete/upload/download folders and files)
4. Monitor the local file system under the --directory specified and apply changes as they occur
5. Check iCloud Drive and Trash for changes every --icloud-refres-period seconds (default 90) and apply changes
6. Perform 4 and 5 until exit

`icloudds` scans your local filesystem under --directory and builds a model of folders and files in memory. Next it does the same for iCloud by scanning all the folders and files. It creates local folders under the --directory you provide, if needed and downloads files that have a modification date newer than those that exist. If the file does not exist locally, it is downloaded and its modification time is set to that of the iCloud Drive item. `icloudds` uploads files that are newer or don't exist in iCloud, including directories. When the upload phase is complete, `icloudds` watches the local filesystem for changes and makes the corresponding add/delete/upload to iCloud Drive. `icloudds` detects changes made to your iCloud Drive and refreshing its in-memory model and applies and changes such as adds/deletes/moves/updates etc.

## High Level Design of `icloudds`
`icloudds` generally works on a model of two lists; one is a list of all files and folders under the --directory path on the local filesystem, the second list is all the items in your iCloud drive. When `icloudds` first starts these two lists are generated and then compared. Files in iCloud missing locally are downloaded. Local files that are missing from iCloud are uploaded. Those files/folders in common have their timestamps checked. Newer files are either uploaded or downloaded. If the objects have the same date but different size, they are ignored.

One this initial sync is complete, `icloudds` starts listening for local file system events and takes the action to create, delete, rename and move items in iCloud. Similarly, iCloud Drive periodically checks whether the count of items in iCloud has changed. If the count has changed, a fresh copy of the iCloud list is made in the background and compared with the existing iCloud list. This helps detect items that have been created, deleted, moved, renamed etc. and the corresponding changes are made locally and applied to the local list.

### More detail
`icloudds` uses the python watchdog filesystem event generator. When a file is created watchdog generates many events so `icloudds` coalesces these events before dispatching to handlers. In addition to this, when `icloudds` needs to download, rename, create files locally it suppresses event dispatch for those paths. `icloudds` does perform a sanity check on iCloud Drive refreshes. iCloud's model includes a fileCount at the root node and after a refresh `icloudds` checks its count of files/folders with what iCloud reports and if it's different, `icloudds` discards the refresh and will try later, using a backoff algorithm to let things settle in iCloud Drive.

`icloudds` uses threads managed by the python ThreadPoolExecutor. Traversing the iCloud model does take time as it takes multiple round-trips to walk the entire tree. In this respect, starting at the root node, a thread is spawned for each sub-folder to retrieve information about that folder, and again more threads are spawned for each of its subfolfers, and so-on. All these Futures are gathered and waited on until they finish. This is the fastest way to retrieve all the iCloud node information. Uploads and downloads of file are also spawned off as separate threads. To protect the ingtegrity of the iCloud file list, access to it is protected with a Lock during the multithreaded refresh. When not refreshing, the iCloud list and local list are only modified in the context of the thread running the EventHandler.

### Odds and Ends
Along the way of implementing this and my prior version of iCloud Drive Sync I learned a few wierd ways that iCloud works:
1. iCloud Drive stores file timestamps in UTC, but rounds up timestamps to the nearest second. Annoying.
2. iCloud Drive has the concept of a "root" folder and a "trash" folder. You would think that moving an item to Trash would decrease the count of items in root and increase the number of items in Trash, right? No. Trash increases, but the count of items in "root" stays the same. So, I guess Trash is in root ? Except it isn't when you retrieve all the items in root. Again, Annoying.
3. Not surprisingly, there are more types of objects in iCloud Drive beyond files and folders. There are also 'app_library' objects for applications such as GarageBand, TextEdit, Readdle etc. etc. `icloudds` currently ignores these items.
4. If you've ever inspected a .app on MacOS you know its a package (zip file). When this gets stored in iCloud Drive a .app file gets expanded into a folder and its contents. This is aggravating.

## Configurable Items
`icloudds` can ignore regexes and include specific folders.
### Ignore Regexes
`icloudds` can ignore files locally and in iCloud. Some ignores are built-in, for example in iCloud .DS_Store files and .com-apple-bird* files are ignored using regexes. Ignore regexes are defined in files, with one regex per line, e.g.:
```code
# regex patterns to ignore, one per line
# built-in ignore patterns are:
.*\.com-apple-bird.*
.*\.DS_Store
# This regex ignores a specific path
scripts/iTermHere\.app
# This regex ignores all files with a .swp extention, regardles of folder location
.*\.swp
``` 
The --ignore-icloud option allows you to provide a filename of regexes to ignore when found in iCloud. The --ignore-local option similarly allows you to provide a filename of regexes to ignore on the local filesystem.
### Include folders
`icloudds` can include folders listed in files specified on the command line using the --include-local and --include-icloud options., e.g.:
```code
# Folders to include, one per line. A folder includes all sub-folders and files.
# if this include file is not specified or is empty, all folders are included
# 
ThisFolder
ThatFolder
```
In this example, if used with --include-icloud only files in the ThisFolder and ThatFolder will be processed, everything else will be ignored.
### How it works in code
In the snippet below, "name" is a path name to a file relative from the --directory command line option. e.g. "MyFolder/some-file-name.txt". Processing this item will be ignored if it matches with one of the regexes from the ignore specs. If it does not match a regex, the name is compared to see if it starts with any one of the includes patterns from the include specs. If the name does not start with any of those patterns, the file / folder is ignored.
```python
    def ignore(self, name, isFolder: bool = False) -> bool:
        for ignore_regex in self._ignores_regexes:
            if re.match(ignore_regex, name):
                return True
        
        if not self._includes_patterns:
            return False
        
        for startswith_pattern in self._includes_patterns:
            if name.startswith(startswith_pattern):
                return False
                
        return True
```
## Logging
`icloudds` uses python logging. The configuration of logging is externalized to a .json file according to the specs of python logging. You can modify this to your needs. I typically run with minimal logging to stderr, and DEBUG logging to the rolling icloudds.log* files.

# Clone, Install dependencies, Build, Install and Run
`icloudds` depends on a forked version of python pyicloud library implementation pyicloud @ git+https://github.com/timlaing/pyicloud.git. This forked implementation has added features to properly set timestamps of objects uploaded to iCloud Drive and it resolves a retrieval limit of 200 albums (in Photos). This implementation includes features I added to my fork of pyicloud, which is no longer needed. Do not use the `pyicloud` Python package that can be installed using `pip`, it is old and does not have the required features.

To build `icloudds`, you need Python, a few dependencies, and a virtual environment if you wish. I use pyenv and virtualenv:

### Clone
To clone the repo:
``` sh
$ git clone https://github.com/gordonaspin/icloudds.git
```
You can now install dependencies, and run from source
``` sh
$ cd icloudds
$ pip install -r requirements.txt
$ python src/icloudds.py -h
``` 
### Build wheel, install and run using pyenv, virtualenv and build
``` sh
$ git clone https://github.com/gordonaspin/icloudds.git
$ cd icloudds
$ pyenv local 3.14.2                #optional if using pyenv and virtual environments
$ python -m venv .venv              #optional if using virtual environments
$ source .venv/bin/activate         #optional if using virtual environments
$ pip install -r requirements.txt   #optional, as installing the wheel below will install dependencies also
$ python -m build
$ pip install dist/*.whl
$ icloudds -h
```
### Install pyicloud from git directly 
``` sh
$ pip install git+https://github.com/timlaing/pyicloud.git@673c1aa
```
or, to install the latest version of pyicloud, build and install:
``` sh
$ git clone https://github.com/timlaing/pyicloud.git
$ cd pyicloud
$ python -m build
$ pip install dist/*.whl
```
## Usage

[//]: # (This is now only a copy&paste from --help output)

``` sh
$ python icloudds.py -h
```
or, if you build and install the wheel from the dist/ folder
``` sh
$ icloudds -h

Usage: icloudds <options>

Options:
  -d, --directory <directory>     Local directory that should be used for
                                  download
  -u, --username <username>       Your iCloud username or email address
  -p, --password <password>       Your iCloud password (default: use pyicloud
                                  keyring or prompt for password)
  --cookie-directory </cookie/directory>
                                  Directory to store cookies for
                                  authentication (default: ~/.pyicloud)
  --ignore-icloud <filename>      Ignore iCloud Drive files/folders filename
  --ignore-local <filename>       Ignore Local files/folders filename
  --include-icloud <filename>     Include iCloud Drive files/folders filename
  --include-local <filename>      Include Local files/folders filename
  --logging-config <filename>     JSON logging config filename (default:
                                  logging-config.json)
  --retry-period <seconds>        Period in seconds to retry failed events
                                  [x>=5]
  --icloud-check-period <seconds>
                                  Period in seconds to look for iCloud changes
                                  [x>=20]
  --icloud-refresh-period <seconds>
                                  Period in seconds to perform full iCloud
                                  refresh  [x>=90]
  --version                       Show the version and exit.
  -h, --help                      Show this message and exit.
```

Example:

``` sh
icloudds --directory ./Drive --username testuser@example.com --password pass1234 
```

## Requirements

- Python 3.14+
- pyicloud
- click
- watchdog


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

## Docker

This script is available in a Docker image:
``` sh
docker pull gordonaspin/icloudds:latest
```
The image defines an entrypoint:
``` sh
ENTRYPOINT [ "icloudds", "-d", "/drive", "--cookie-directory", "/cookies" ]
```
Usage:

``` sh
# Downloads all iCloud Drive items to ./Drive

docker pull gordonaspin/icloudds:latest
docker run -it --name icloudds \
    -v $(pwd)/Drive:/drive \
    -v $(pwd)/cookies:/cookies \
    gordonaspin/icloudds:latest \
    --username testuser@example.com \
```

On Windows:

- use `%cd%` instead of `$(pwd)`
- or full path, e.g. `-v c:/icloud/Drive:/drive`

Building docker image from this repo and gordonaspin/pyicloud repo image locally:

``` sh
docker build --tag your-repo/icloudds:latest --progress=plain -f ./Dockerfile

# the pyicloud icloud command line utility
# this will optionally create a python keyring in the container for future use, cookies will go to a tmp folder in the container
docker exec -it icloudds icloud --username apple_id@mail.com

# run icloudds -h
docker exec -it icloudds icloudds -h

# start the container with mounts for the Drive folder and cookie storage:
docker run -it --name icloudds -v ~/iCloud\ Drive:/drive -v ~/.pyicloud:/cookies your-repo/icloudds -u username@email.com

```

The container has the default .ignore*, .include* and logging-config.json files in the home folder of the docker user and these are used by default. To change this you can edit the files in the container in /home/docker, or override permamently you can build your own container with the contents changed, or you can specify the --include and --ignore command line arguments that refer to a path you mount to the container. e.g.:
``` sh
docker run -it --name icloudds -v ~/iCloud\ Drive:/drive -v ~/.pyicloud:/cookies your-repo/icloudds -v ~/.config:/cfg -u username@email.com --ignore-icloud /cfg/<filename> --ignore-local /cfg/<filename> --include-icloud /cfg/<filename> --include-local /cfg/<filename>
```
The container as-built has verbose DEBUG logging to file enabled, but the log files are inside the container. These are accessible for example like so:
``` sh
$ docker exec -it icloudds /bin/bash -c "tail -F icloudds.log"
```
You can modify the logging-config.json by editing it inside the container like so:
``` sh
$ docker exec -it icloudds /bin/bash -c "vi logging-config.json"
```
Using this approach is easy, but will not persist if you delete the container. An example of moving the include/exclude files and logging-config.json to outside the container would look something like this:
``` sh
docker run -it --name icloudds -v ~/iCloud\ Drive:/drive -v ~/.pyicloud:/cookies your-repo/icloudds -v ~/.config:/cfg -u username@email.com --ignore-icloud /cfg/<filename> --ignore-local /cfg/<filename> --include-icloud /cfg/<filename> --include-local /cfg/<filename> --logging-config /cfg/logging-config.json
```
In the example above, you would have your ignore files, include files and logging-config.json files in your `~/.config` folder and refer to them on the command line as `/cfg/filename`