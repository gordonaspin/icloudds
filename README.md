# icloudds (iCloud Drive Sync) [![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

icloudds is a python command-line utility designed to synchronize and maintain a local copy of your iCloud Drive. It is an improvemnt upon, and a re-write of my original iCloud Drive Sync which I no longer maintain.

- A command-line tool to synchronize your iCloud Drive with a local filesystem folder.
- Works on Linux, Windows, and MacOS.
- Run in the background to keep your iCloud Drive folders and contents in sync with the local filesysystem  

iCloud Drive Sync's basic operation is as follows:
1. Scan the local file system under the --directory specified
2. Scan your iCloud Drive root and trash folders
3. Apply differences to both local and iCloud (create/delete/upload/download folders and files)
4. Monitor the local file system under the --directory specified and apply changes as they occur
5. Check iCloud Drive and Trash for changes every --icloud-refres-period seconds (default 90) and apply changes
6. Perform 4 and 5 until exit

iCloud Drive Sync scans your local filesystem under --directory and builds a model of folders and files in memory. Next it does the same for iCloud by scanning all the folders and files. It creates local folders under the --directory you provide, if needed and downloads files that have a modification date newer than those that exist. If the file does not exist locally, it is downloaded and its modification time is set to that of the iCloud Drive item. iCloud Drive Sync uploads files that are newer or don't exist in iCloud, including directories. When the upload phase is complete, iCloud Drive Sync watches the local filesystem for changes and makes the corresponding add/delete/upload to iCloud Drive. iCloud Drive sync detects changes made to your iCloud Drive and refreshing its in-memory model and applies and changes such as adds/deletes/moves/updates etc.

## Clone, Install dependencies, Build, Install and Run
`icloudds` depends on a forked version of python pyicloud library implementation pyicloud @ git+https://github.com/timlaing/pyicloud.git. This forked implementation has added features to properly set timestamps of objects uploaded to iCloud Drive and it resolves a retrieval limit of 200 albums (in Photos). This implementation includes features I added to my fork of pyicloud, which is no longer needed. Do not use the `pyicloud` Python package that can be installed using `pip`, it is old and does not have the required features.

To build icloudds, you need Python, a few dependencies, and a virtual environment if you wish. I use pyenv and .venv:

### Clone
To clone the repo:
``` sh
$ git clone https://github.com/gordonaspin/icloudds.git
```
You can now install dependencies, and run from source
```
$ cd icloudds
$ pip install -r requirements.txt
$ python src/icloudds.py -h
``` 
### Build wheel, install and run
``` sh
$ git clone https://github.com/gordonaspin/icloudds.git
$ cd icloudds
$ pyenv local 3.14.2            #optional if using pyenv and virtual environments
$ python -m venv .venv          #optional if using virtual environments
$ source .venv/bin/activate     #optional if using virtual environments
$ pip install -r requirements.txt
$ python -m build
$ pip install dist/*.whl
$ icloudds -h
```
### Install pyicloud from git directly 
``` sh
$ pip install git+https://github.com/timlaing/pyicloud.git@673c1aa
```
or, to install the latest version, build and install:
``` sh
$ git clone https://github.com/timlaing/pyicloud.git
$ cd pyicloud
$ python -m build
$ pip install dist/*.whl
```
## Usage

[//]: # (This is now only a copy&paste from --help output)

``` plain
$ python icloudds.py -h
```
or, if you build and install the wheel from the dist/ folder
```
$ icloudds -h
```
```
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
icloudds --directory ./Drive \
--username testuser@example.com \
--password pass1234 \
--directory Drive/ \
```

## Requirements

- Python 3.14+
- pyicloud
- click
- watchdog


## Authentication

If your Apple account has two-factor authentication enabled, you will be prompted for a code when you run the script.

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
```bash
docker pull gordonaspin/icloudds:latest
```
The image defines an entrypoint:
```bash
ENTRYPOINT [ "icloudds", "-d", "/drive", "--cookie-directory", "/cookies" ]
```
Usage:

```bash
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

```bash
docker build --tag your-repo/icloudds:latest --progress=plain -f ./Dockerfile

# the pyicloud icloud command line utility
# this will optionally create a python keyring in the container for future use, cookies will go to a tmp folder in the container
docker exec -it icloudds icloud --username apple_id@mail.com

# run icloudds -h
docker exec -it icloudds icloudds -h

# start the container with mounts for the Drive folder and cookie storage:
docker run -it --name icloudds -v ~/iCloud\ Drive:/drive -v ~/.pyicloud:/cookies your-repo/icloudds -u username@email.com

```

The container has the default .ignore*, .include* and logging-config.json files in the home folder of the docker user and these are used by default. To override this you can either build your own container with the contents changed, or you can specify the --include and --ignore command line arguments that refer to a path you mount to the container. e.g.:
```bash
docker run -it --name icloudds -v ~/iCloud\ Drive:/drive -v ~/.pyicloud:/cookies your-repo/icloudds -v ~/.config:/cfg -u username@email.com --ignore-icloud /cfg/<filename> --ignore-local /cfg/<filename> --include-icloud /cfg/<filename> --include-local /cfg/<filename>
```
