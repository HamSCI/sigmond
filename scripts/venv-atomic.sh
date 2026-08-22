#!/bin/bash
# venv-atomic.sh — build a venv BESIDE the live one, prove it imports, then swap.
#
# sigmond#47: install.sh used to `uv venv --clear $VENV_DIR` (wipe) and then
# install into it.  ENOSPC or no network after the wipe left an EMPTY venv —
# twice on B4 in the 2026-08-21 chaos drills — and nothing noticed because
# `smd` itself runs off lib/.  Here the live venv is never touched until a
# complete, import-verified replacement exists next to it.
#
# Usage (sourced):  venv_atomic_install <venv_dir> "<module> <module>..." <pip args...>
#   env: UV (path or empty → python -m venv fallback), PYTHON3, SUDO ("" or "sudo"),
#        UV_PYTHON_INSTALL_DIR.
# Layout: <venv_dir>.new (build), <venv_dir> (live), <venv_dir>.prev (last live, one kept).
# With uv the new venv is built --relocatable so the rename keeps console-script
# shebangs valid; without uv the fallback builds in place (legacy, non-atomic) and
# says so.

venv_atomic_install() {
    local target="$1" verify_mods="$2"; shift 2
    local new="${target}.new" prev="${target}.prev"
    local _sudo="${SUDO:-}" _uv="${UV:-}" _py="${PYTHON3:-python3}"
    local _log="${VENV_ATOMIC_LOG:-}"
    _va_say() { echo "[venv-atomic] $*" >&2; }

    if [[ -z "$_uv" ]]; then
        _va_say "uv not available — building IN PLACE at $target (legacy, not atomic)"
        $_sudo "$_py" -m venv --clear "$target" || return 1
        $_sudo "$target/bin/pip" install --quiet --upgrade pip || return 1
        $_sudo "$target/bin/pip" install --quiet "$@" || return 1
        $_sudo "$target/bin/python" -c "import ${verify_mods// /, }" || return 1
        return 0
    fi

    $_sudo rm -rf "$new"
    if ! $_sudo env "UV_PYTHON_INSTALL_DIR=${UV_PYTHON_INSTALL_DIR:-/opt/uv/python}" \
            "$_uv" venv --relocatable --python "$_py" --clear "$new"; then
        _va_say "venv create FAILED at $new — live venv untouched"
        $_sudo rm -rf "$new"; return 1
    fi
    if ! $_sudo env "UV_PYTHON_INSTALL_DIR=${UV_PYTHON_INSTALL_DIR:-/opt/uv/python}" \
            "$_uv" pip install --quiet --python "$new/bin/python" "$@"; then
        _va_say "package install FAILED into $new — live venv untouched"
        $_sudo rm -rf "$new"; return 1
    fi
    if ! $_sudo "$new/bin/python" -c "import ${verify_mods// /, }"; then
        _va_say "import check FAILED (${verify_mods}) in $new — refusing the swap; live venv untouched"
        $_sudo rm -rf "$new"; return 1
    fi
    $_sudo chmod -R a+rX "$new"
    $_sudo rm -rf "$prev"
    if [[ -d "$target" ]]; then
        $_sudo mv "$target" "$prev" || { _va_say "could not park live venv as $prev"; $_sudo rm -rf "$new"; return 1; }
    fi
    if ! $_sudo mv "$new" "$target"; then
        _va_say "swap FAILED — restoring previous venv"
        [[ -d "$prev" ]] && $_sudo mv "$prev" "$target"
        return 1
    fi
    _va_say "venv swapped into place at $target (previous kept at $prev)"
    return 0
}
