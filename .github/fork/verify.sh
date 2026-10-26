#!/usr/bin/env bash
# Checks a change must pass before it is deployed:
#   frontend: unit tests + production build
#   backend:  no missing migrations + full Django test suite
#
# Needs Postgres and Redis (see the services in the fork workflows) and the
# Python deps from install-python-deps.sh. Run from the repository root.
# Usage: .github/fork/verify.sh [frontend|backend|all]   (default: all)
set -euo pipefail

part="${1:-all}"
export DJANGO_SECRET_KEY="${DJANGO_SECRET_KEY:-ci-test-secret-key}"
export POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
export POSTGRES_DB="${POSTGRES_DB:-dispatcharr}"
export POSTGRES_USER="${POSTGRES_USER:-dispatch}"
export POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-secret}"
export DISPATCHARR_LOG_LEVEL="${DISPATCHARR_LOG_LEVEL:-WARNING}"

if [[ "$part" == "frontend" || "$part" == "all" ]]; then
  echo "::group::Frontend tests and build"
  (
    cd frontend
    npm ci --no-audit --no-fund
    npx vitest --run
    npm run build
  )
  echo "::endgroup::"
fi

if [[ "$part" == "backend" || "$part" == "all" ]]; then
  echo "::group::Backend migrations check"
  python manage.py makemigrations --check --dry-run
  echo "::endgroup::"
  echo "::group::Backend tests"
  python manage.py test --noinput
  echo "::endgroup::"
fi

echo "verify: $part OK"
