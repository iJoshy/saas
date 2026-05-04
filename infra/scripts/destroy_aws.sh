#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

terraform -chdir="$ROOT_DIR/infra/aws" init
terraform -chdir="$ROOT_DIR/infra/aws" destroy "$@"
