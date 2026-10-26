#!/usr/bin/env bash
# Install the backend's Python dependencies for CI.
# torch / sentence-transformers are skipped: they are only imported lazily for
# EPG auto-matching, the test suite does not need them, and they are huge.
set -euo pipefail

python - > /tmp/fork-requirements.txt <<'PY'
import tomllib
deps = tomllib.load(open("pyproject.toml", "rb"))["project"]["dependencies"]
for dep in deps:
    if dep.lower().startswith(("torch", "sentence-transformers")):
        continue
    print(dep)
PY
python -m pip install --upgrade pip >/dev/null
python -m pip install -r /tmp/fork-requirements.txt
