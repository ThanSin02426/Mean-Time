#!/usr/bin/env bash
export PYTHONPATH="${PYTHONPATH}:$(pwd)"
python src/main.py "$@"
