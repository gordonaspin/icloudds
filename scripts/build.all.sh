#!/bin/bash
set -euo pipefail
source .venv/bin/activate
scripts/build.sh
scripts/install.sh
scripts/build.docker.local.sh
scripts/build.docker.repo.sh
