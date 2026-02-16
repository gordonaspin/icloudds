#!/bin/bash
set -euo pipefail
rm dist/*
source .venv/bin/activate
python -m pylint src
python -m build
