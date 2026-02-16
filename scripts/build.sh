#!/bin/bash
set -euo pipefail
source .venv/bin/activate
rm dist/*
python -m build
