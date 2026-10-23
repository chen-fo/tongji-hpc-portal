"""Bash hook delivered through a process substitution: no remote rc/history files written."""

import shlex


def shell_command(
    official_home: str, virtual_home: str, nonce: str, verify_environment=False
) -> str:
    script = r"""
# Preserve the official initialization before changing HOME. Do not persist terminal history.
HISTFILE=/dev/null
export HISTFILE
if [ -r __OFFICIAL_RC__ ]; then . __OFFICIAL_RC__; fi
__portal_before_path=$PATH
__portal_before_conda=$(command -v conda 2>/dev/null)
__portal_before_module=$(type -t module 2>/dev/null)
__portal_before_prefix=${CONDA_PREFIX-}
export HOME=__VIRTUAL_HOME__
HISTFILE=/dev/null
export HISTFILE
cd -- "$HOME" || exit 1
__portal_collect=0
__portal_emit() {
    local encoded
    encoded=$(printf '%s\000%s\000%s' "$1" "$2" "${3:0:16384}" | command base64 | command tr -d '\r\n') || return 0
    printf '\033]777;PORTAL;__NONCE__;%s\007' "$encoded"
}
__portal_debug() {
    [ "$__portal_collect" = 1 ] || return 0
    case "$1" in __portal_* ) return 0 ;; esac
    __portal_emit command "$PWD" "$1" || :
    return 0
}
__portal_prompt() { local status=$?; __portal_collect=0; return "$status"; }
__portal_ready() { local status=$?; __portal_collect=1; return "$status"; }
__ENVIRONMENT_CHECK__
if command -v base64 >/dev/null && command -v tr >/dev/null && [ -z "$(trap -p DEBUG)" ]; then
    # Preserve existing prompt hooks (including Conda/module prompt updates).
    if declare -p PROMPT_COMMAND 2>/dev/null | command grep -q '^declare -a'; then
        PROMPT_COMMAND=(__portal_prompt "${PROMPT_COMMAND[@]}" __portal_ready)
    else
        PROMPT_COMMAND="__portal_prompt; ${PROMPT_COMMAND:+$PROMPT_COMMAND; }__portal_ready"
    fi
    trap '__portal_debug "$BASH_COMMAND"' DEBUG
    __portal_emit status "$PWD" enabled
else
    __portal_emit status "$PWD" disabled || :
    printf '\r\n[Portal] 审计 hook 不可用，终端仍可继续使用。\r\n'
fi
"""
    script = script.replace("__OFFICIAL_RC__", shlex.quote(official_home + "/.bashrc"))
    script = script.replace("__VIRTUAL_HOME__", shlex.quote(virtual_home))
    script = script.replace("__NONCE__", nonce)
    check = r"""
__portal_env_result=preserved
if [ "$PATH" != "$__portal_before_path" ] || [ "$(command -v conda 2>/dev/null)" != "$__portal_before_conda" ] || [ "$(type -t module 2>/dev/null)" != "$__portal_before_module" ] || [ "${CONDA_PREFIX-}" != "$__portal_before_prefix" ]; then
    __portal_env_result=changed
fi
__portal_emit environment "$PWD" "result=$__portal_env_result; conda=${__portal_before_conda:-unavailable}; module=${__portal_before_module:-unavailable}"
"""
    script = script.replace("__ENVIRONMENT_CHECK__", check if verify_environment else "")
    inner = "exec bash --rcfile <(printf %s " + shlex.quote(script) + ") -i"
    return "bash -c " + shlex.quote(inner)
