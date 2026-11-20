#!/usr/bin/env bash
set -euo pipefail
# The assembler accepts an approved candidate build record and separate host credentials.
umask 077
exec python3 "$(dirname -- "${BASH_SOURCE[0]}")/skyward_guest_pack.py" "$@"
