#!/bin/bash
source .venv/bin/activate
pip install dist/*.whl --force-reinstall
