#!/usr/bin/env bash
# Fails if pyproject.toml and package.json declare different versions.
set -euo pipefail

py=$(grep -m1 '^version' pyproject.toml | sed 's/.*= *"//;s/"//' || true)
if [ -z "$py" ]; then
  echo "check_version_sync: no version field found in pyproject.toml"
  exit 1
fi

js=$(jq -r .version package.json)
if [ -z "$js" ] || [ "$js" = "null" ]; then
  echo "check_version_sync: no version field found in package.json"
  exit 1
fi

if [ "$py" != "$js" ]; then
  echo "Version mismatch: pyproject.toml=$py  package.json=$js"
  exit 1
fi
