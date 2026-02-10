#!/bin/bash
source .venv/bin/activate
rm dist/*
python -m build
