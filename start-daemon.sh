#!/bin/sh
# Arranca el daemon de CuaDriver en un socket propio para computer-use-mini.
set -eu

case "$(uname -s)" in
  Darwin) CACHE_HOME="${XDG_CACHE_HOME:-$HOME/Library/Caches}" ;;
  *) CACHE_HOME="${XDG_CACHE_HOME:-$HOME/.cache}" ;;
esac
SOCK="${CU_SOCKET:-$CACHE_HOME/cu-mini/cu.sock}"

if [ -n "${CU_DRIVER:-}" ]; then
  DRIVER="$CU_DRIVER"
else
  DRIVER="$(command -v cua-driver || true)"
fi

if [ -z "$DRIVER" ]; then
  DRIVER=""
  for candidate in \
    "/Applications/CuaDriver.app/Contents/MacOS/cua-driver" \
    "$HOME/Applications/CuaDriver.app/Contents/MacOS/cua-driver"
  do
    if [ -x "$candidate" ]; then
      DRIVER="$candidate"
      break
    fi
  done
fi

mkdir -p "$(dirname "$SOCK")"

if [ -z "$DRIVER" ] || [ ! -x "$DRIVER" ]; then
  echo "No se encontró el ejecutable de Cua Driver. Instálalo o define CU_DRIVER con la ruta a cua-driver." >&2
  exit 1
fi

set -- \
  serve \
  --socket "$SOCK" \
  --permission-mode standard \
  --no-overlay

if [ "${CUA_UNSAFE_MODE:-0}" = "1" ]; then
  set -- "$@" \
    --dangerously-bypass-approvals
fi

exec "$DRIVER" "$@"
