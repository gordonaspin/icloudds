#!/bin/bash
set -euo pipefail
source .venv/bin/activate
python -m pylint src
rm dist/*
python -m build
