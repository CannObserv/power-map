#!/usr/bin/env bash
# Fails if pyproject.toml and package.json declare different versions.
set -euo pipefail

py=$(grep -m1 '^version' pyproject.toml | sed 's/.*= *"//;s/"//')
js=$(node -p 'require("./package.json").version')

if [ "$py" != "$js" ]; then
  echo "Version mismatch: pyproject.toml=$py  package.json=$js"
  exit 1
fi
