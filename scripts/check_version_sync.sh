#!/usr/bin/env bash
# Fails if pyproject.toml, package.json and clients/python/pyproject.toml declare
# different versions. The generated client ships at the app's version (#618);
# `uv run python -m scripts.regenerate_client` stamps it.
set -euo pipefail

client_pyproject=clients/python/pyproject.toml

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

client=$(grep -m1 '^version' "$client_pyproject" | sed 's/.*= *"//;s/"//' || true)
if [ -z "$client" ]; then
  echo "check_version_sync: no version field found in $client_pyproject"
  exit 1
fi

if [ "$py" != "$js" ] || [ "$py" != "$client" ]; then
  echo "Version mismatch: pyproject.toml=$py  package.json=$js  $client_pyproject=$client"
  if [ "$py" = "$js" ]; then
    echo "The client's version is stamped, not edited: run uv run python -m scripts.regenerate_client"
  fi
  exit 1
fi
