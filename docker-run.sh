#!/usr/bin/env bash
set -o errexit

cd "$(dirname S0)"
python3 cloudbot/__main__.py &>> cloudbot.log
