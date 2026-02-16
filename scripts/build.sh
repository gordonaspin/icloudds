#!/bin/bash
set -euo pipefail
python -m pylint src
source .venv/bin/activate
rm dist/*
python -m build
