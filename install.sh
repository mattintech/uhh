#!/bin/sh
# uhh installer — draft. Review, then move to the repo root and commit.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/mattintech/uhh/main/install.sh | sh
#
# Optional env vars:
#   UHH_OLLAMA_HOST=http://homelab:11434  use a remote ollama (skip the prompt)
#   UHH_INSTALL_MODEL=qwen2.5-coder:7b    override the default model (local install)
#   UHH_SKIP_OLLAMA=1                     skip ollama install (already have it)
#   UHH_SKIP_MODEL=1                      skip the model pull
#
# Supports macOS and Linux. Windows: see https://github.com/mattintech/uhh#install

set -eu

DEFAULT_MODEL="${UHH_INSTALL_MODEL:-llama3.2:3b}"
OLLAMA_HOST="${OLLAMA_HOST:-http://localhost:11434}"

# ----------------------------------------------------------------------------
# Wrap everything in main() and only call it at the very end. With `curl | sh`
# the shell evaluates bytes as they arrive — if the connection drops partway,
# a top-level command could run with a partial argument list. Defining main()
# means we either get the whole script or nothing.
# ----------------------------------------------------------------------------

main() {
    # Snapshot the user's original PATH so we can later tell whether their
    # interactive shell will find newly-installed binaries, vs. only this
    # script's modified PATH finding them.
    ORIG_PATH="$PATH"

    setup_colors
    detect_os
    print_banner

    check_python
    choose_ollama

    if [ "${USE_REMOTE_OLLAMA:-0}" = "1" ]; then
        info "skipping local ollama install — using remote at $OLLAMA_HOST"
    else
        install_ollama
        start_ollama
        pull_model
    fi

    install_uhh

    if [ "${USE_REMOTE_OLLAMA:-0}" = "1" ]; then
        write_remote_config
    fi

    print_success
}

# --- output helpers ---------------------------------------------------------

setup_colors() {
    if [ -t 1 ]; then
        BOLD=$(printf '\033[1m')
        DIM=$(printf '\033[2m')
        RED=$(printf '\033[31m')
        GREEN=$(printf '\033[32m')
        YELLOW=$(printf '\033[33m')
        CYAN=$(printf '\033[36m')
        RESET=$(printf '\033[0m')
    else
        BOLD= DIM= RED= GREEN= YELLOW= CYAN= RESET=
    fi
}

info() { printf '%s==>%s %s\n' "${CYAN}" "${RESET}" "$1"; }
warn() { printf '%swarn:%s %s\n' "${YELLOW}" "${RESET}" "$1" >&2; }
err()  { printf '%serror:%s %s\n' "${RED}" "${RESET}" "$1" >&2; }
have() { command -v "$1" >/dev/null 2>&1; }

# Prepend $1 to PATH if it isn't already on it; clear command-lookup cache.
ensure_path_has() {
    case ":$PATH:" in
        *:"$1":*) ;;
        *) PATH="$1:$PATH" ;;
    esac
    hash -r 2>/dev/null || true
}

# Where `pip install --user` puts binaries. macOS python.org Python returns
# ~/Library/Python/X.Y/bin; Linux and Homebrew Python return ~/.local/bin.
user_bin_dir() {
    python3 -m site --user-base 2>/dev/null | { read base; printf '%s/bin' "$base"; }
}

print_banner() {
    # Single-quoted heredoc — no variable expansion, no escape processing,
    # so the backslashes in the figlet output stay literal.
    printf '\n%s' "${CYAN}"
    cat <<'EOF'
        _     _
  _   _| |__ | |__
 | | | | '_ \| '_ \
 | |_| | | | | | | |
  \__,_|_| |_|_| |_|
EOF
    printf '%s\n' "${RESET}"
    printf '  %sinstaller — ollama (or remote) + a model + uhh%s\n\n' "${DIM}" "${RESET}"
}

print_success() {
    if ! have uhh; then
        err "install reported success, but 'uhh' isn't on PATH even after we"
        err "tried both ~/.local/bin and $(user_bin_dir)."
        err "look for the binary manually and add its parent dir to PATH."
        exit 1
    fi

    uhh_path=$(command -v uhh)
    uhh_dir=$(dirname "$uhh_path")

    printf '\n%s%sdone.%s\n\n' "${GREEN}" "${BOLD}" "${RESET}"
    printf 'try it:\n    %suhh how do i find the largest file in ~/Documents%s\n\n' "${BOLD}" "${RESET}"
    printf 'config: ~/.config/uhh/config.toml\n'
    printf 'docs:   https://github.com/mattintech/uhh\n\n'

    if [ "${USE_REMOTE_OLLAMA:-0}" = "1" ]; then
        printf 'using remote ollama at %s%s%s\n' "${BOLD}" "$OLLAMA_HOST" "${RESET}"
        printf 'first %suhh%s run will list models on that host and prompt you to pick one.\n\n' \
            "${BOLD}" "${RESET}"
    fi

    # If we installed Ollama (mac), let the user know the .app exists for the
    # menu bar / settings UI — we deliberately didn't launch it during install
    # to keep things headless, but they may still want it.
    if [ "${INSTALLED_OLLAMA:-0}" = "1" ] && [ "$OS" = mac ]; then
        printf 'tip: ollama is running headless. for the menu bar app + settings UI:\n'
        printf '    %sopen /Applications/Ollama.app%s\n\n' "${BOLD}" "${RESET}"
    fi

    # Check the user's ORIGINAL PATH (not the one we modified) — if uhh's
    # actual install dir isn't on it, their interactive shell won't find uhh
    # after this script exits. Give them a copy-pasteable export for THIS
    # shell; a new terminal will pick up the rc changes pipx made.
    case ":$ORIG_PATH:" in
        *:"$uhh_dir":*) ;;
        *)
            warn "uhh installed at $uhh_path — but $uhh_dir isn't on your shell's PATH."
            warn "for this shell, run:"
            warn "    export PATH=\"$uhh_dir:\$PATH\""
            if [ "${USED_PIPX:-0}" = "1" ]; then
                warn "new terminals: should pick it up automatically (pipx updated your rc)."
            else
                warn "new terminals: add the export above to ~/.zshrc (or ~/.bashrc) too."
            fi
            ;;
    esac
}

# --- detection --------------------------------------------------------------

detect_os() {
    case "$(uname -s)" in
        Darwin) OS=mac ;;
        Linux)  OS=linux ;;
        *)
            err "unsupported OS: $(uname -s)"
            err "this installer supports macOS and Linux."
            err "Windows: see https://github.com/mattintech/uhh#install"
            exit 1
            ;;
    esac
}

check_python() {
    if ! have python3; then
        err "python3 not found. install python 3.11+ first."
        err "  macOS:  https://www.python.org/downloads/"
        err "  Linux:  use your distro's package manager (apt, dnf, pacman, ...)"
        exit 1
    fi

    py_ver=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    py_major=$(echo "$py_ver" | cut -d. -f1)
    py_minor=$(echo "$py_ver" | cut -d. -f2)

    if [ "$py_major" -lt 3 ] || { [ "$py_major" -eq 3 ] && [ "$py_minor" -lt 11 ]; }; then
        err "uhh requires python 3.11+, found $py_ver"
        exit 1
    fi

    info "python $py_ver — ok"
}

# --- ollama choice ----------------------------------------------------------
#
# Three paths: (a) ollama already installed locally — use it; (b) user wants
# to install ollama locally — current install/start/pull flow; (c) user has
# a remote ollama somewhere — we just validate it's reachable and write a
# config pointing at it.
#
# Sets globals: USE_REMOTE_OLLAMA (0/1), OLLAMA_HOST.

choose_ollama() {
    # 1. Headless override via env var — useful for CI / scripted installs.
    if [ -n "${UHH_OLLAMA_HOST:-}" ]; then
        info "using OLLAMA_HOST from env: $UHH_OLLAMA_HOST"
        if ! curl -sf "${UHH_OLLAMA_HOST%/}/" >/dev/null 2>&1; then
            err "couldn't reach $UHH_OLLAMA_HOST — check it's running and accessible."
            exit 1
        fi
        info "remote ollama is reachable"
        USE_REMOTE_OLLAMA=1
        OLLAMA_HOST="${UHH_OLLAMA_HOST%/}"
        return
    fi

    # 2. Already installed locally — keep using it (no need to ask).
    if have ollama; then
        return
    fi

    # 3. No tty available (curl | sh from a non-interactive context, CI, etc.)
    #    — can't prompt, so default to local install.
    if [ ! -e /dev/tty ]; then
        info "no tty available — defaulting to local ollama install"
        return
    fi

    # 4. Interactive — ask.
    printf '\n%sollama setup%s\n' "${BOLD}" "${RESET}"
    printf '%s────────────%s\n' "${DIM}" "${RESET}"
    printf 'uhh needs an ollama instance. options:\n'
    printf '  %s[1]%s install ollama locally  %s(default — best for new users)%s\n' \
        "${BOLD}" "${RESET}" "${DIM}" "${RESET}"
    printf '  %s[2]%s use an existing ollama  %s(homelab, work server, etc.)%s\n' \
        "${BOLD}" "${RESET}" "${DIM}" "${RESET}"
    printf '\nchoose [1/2] (default 1): '

    # Read from /dev/tty so this works under `curl | sh`, where stdin is the
    # script content rather than the user's terminal.
    read choice < /dev/tty || choice=1
    choice="${choice:-1}"

    case "$choice" in
        2)
            printf '\nenter the ollama host URL (e.g. http://homelab.lan:11434): '
            read remote_host < /dev/tty
            remote_host="${remote_host%/}"

            if [ -z "$remote_host" ]; then
                err "no host given. exiting."
                exit 1
            fi

            info "checking $remote_host..."
            if ! curl -sf "$remote_host/" >/dev/null 2>&1; then
                err "couldn't reach $remote_host"
                err "check the URL and that the remote ollama is running."
                exit 1
            fi
            info "remote ollama is reachable"

            USE_REMOTE_OLLAMA=1
            OLLAMA_HOST="$remote_host"
            ;;
        1|"")
            info "will install ollama locally"
            ;;
        *)
            err "invalid choice: $choice (expected 1 or 2)"
            exit 1
            ;;
    esac
}

# Write a minimal uhh config pointing at the remote so the first-run wizard
# doesn't ask for the host again. Model is left unset on purpose — the wizard
# will show the remote's available models and let the user pick.
write_remote_config() {
    config_dir="$HOME/.config/uhh"
    config_file="$config_dir/config.toml"

    if [ -e "$config_file" ]; then
        info "uhh config already exists at $config_file — not overwriting"
        return
    fi

    mkdir -p "$config_dir"
    cat > "$config_file" <<EOF
# Generated by the uhh installer.
default_profile = "remote"

[profiles.remote]
host = "$OLLAMA_HOST"
# model = "..."   # first \`uhh ...\` will list models from $OLLAMA_HOST and prompt
EOF
    info "wrote uhh config → $config_file"
}

# --- ollama -----------------------------------------------------------------

install_ollama() {
    if [ "${UHH_SKIP_OLLAMA:-}" = "1" ]; then
        info "skipping ollama install (UHH_SKIP_OLLAMA=1)"
        return
    fi

    if have ollama; then
        info "ollama already installed"
        return
    fi

    info "installing ollama..."
    # Ollama's installer handles both macOS (downloads .app, symlinks the CLI
    # to /usr/local/bin/ollama) and Linux (systemd setup).
    #
    # OLLAMA_NO_START=1 tells it to skip its own "Starting Ollama..." step.
    # On macOS, that step calls `open -a Ollama` immediately after moving the
    # .app to /Applications — but Launch Services hasn't indexed the new app
    # yet, so the lookup fails with "Unable to find application named 'Ollama'"
    # and (because Ollama's installer runs `set -e`) the whole pipeline aborts.
    # We start the service ourselves below in start_ollama.
    curl -fsSL https://ollama.com/install.sh | OLLAMA_NO_START=1 sh
    INSTALLED_OLLAMA=1

    # The installer just created /usr/local/bin/ollama. Make sure subsequent
    # calls in this script can find it: prepend /usr/local/bin if it isn't
    # already on PATH, and clear the shell's cached command-lookup table
    # (otherwise `command -v ollama` may still report "not found").
    case ":$PATH:" in
        *:/usr/local/bin:*) ;;
        *) PATH="/usr/local/bin:$PATH" ;;
    esac
    hash -r 2>/dev/null || true

    if ! have ollama; then
        err "ollama install appeared to succeed but 'ollama' isn't on PATH."
        err "open a new shell and re-run this installer."
        exit 1
    fi
}

start_ollama() {
    if curl -sf "${OLLAMA_HOST}/" >/dev/null 2>&1; then
        info "ollama is already running at ${OLLAMA_HOST}"
        return
    fi

    info "starting ollama..."
    if [ "$OS" = mac ]; then
        # Headless: just run the API server. Avoids launching the .app, which
        # would pop the menu bar icon, an onboarding window, and possibly a
        # Gatekeeper prompt — all friction for a one-line install. The user
        # can launch /Applications/Ollama.app manually if they want the GUI.
        nohup ollama serve >/dev/null 2>&1 &
    else
        # Linux installer wires up systemd; if it's not running, start it.
        if have systemctl && systemctl list-unit-files 2>/dev/null | grep -q '^ollama\.service'; then
            sudo systemctl start ollama || nohup ollama serve >/dev/null 2>&1 &
        else
            nohup ollama serve >/dev/null 2>&1 &
        fi
    fi

    # Wait up to 30s for the API to come up.
    i=0
    while [ $i -lt 30 ]; do
        if curl -sf "${OLLAMA_HOST}/" >/dev/null 2>&1; then
            info "ollama is running"
            return
        fi
        sleep 1
        i=$((i + 1))
    done

    err "ollama didn't respond within 30s."
    err "try 'ollama serve' in another terminal, then re-run this installer."
    exit 1
}

pull_model() {
    if [ "${UHH_SKIP_MODEL:-}" = "1" ]; then
        info "skipping model pull (UHH_SKIP_MODEL=1)"
        return
    fi

    if ollama list 2>/dev/null | awk 'NR>1 {print $1}' | grep -qx "$DEFAULT_MODEL"; then
        info "model $DEFAULT_MODEL already pulled"
        return
    fi

    info "pulling default model: $DEFAULT_MODEL (~2 GB)"
    info "this is the only big download — grab a coffee."
    ollama pull "$DEFAULT_MODEL"
}

# --- uhh --------------------------------------------------------------------

# Try to install pipx via pip --user if it's missing. pipx is the modern
# standard for installing Python CLI tools (each gets its own isolated venv);
# bootstrapping it gives the user a much cleaner end state than pip --user,
# whose binaries can land in non-standard dirs that aren't on PATH.
bootstrap_pipx_if_missing() {
    if have pipx; then
        return
    fi

    info "pipx not found — installing it (manages isolated Python CLIs)..."
    if ! python3 -m pip install --user --quiet pipx; then
        warn "pipx bootstrap failed — will fall back to 'pip install --user' for uhh."
        return
    fi

    # pipx itself just landed in user_bin_dir; make it callable in this script.
    ensure_path_has "$(user_bin_dir)"

    if have pipx; then
        # Append pipx's bin dir (~/.local/bin) to user's shell rc so future
        # shells find pipx-installed tools like uhh. Also ensures user_bin_dir
        # itself is added on systems where pipx lives there.
        pipx ensurepath >/dev/null 2>&1 || true
        info "pipx installed"
    else
        warn "pipx installed but not on PATH — falling back to 'pip install --user'."
    fi
}

install_uhh() {
    bootstrap_pipx_if_missing

    if have uhh; then
        info "uhh already installed: $(uhh --version 2>&1 | head -1)"
        info "upgrading..."
        if have pipx; then
            pipx upgrade uhh || pipx install --force uhh
            USED_PIPX=1
        else
            python3 -m pip install --user --upgrade uhh
        fi
        ensure_path_has "$HOME/.local/bin"
        ensure_path_has "$(user_bin_dir)"
        return
    fi

    info "installing uhh..."
    if have pipx; then
        pipx install uhh
        # pipx ensurepath updates ~/.zshrc / ~/.bashrc so NEW shells find
        # ~/.local/bin. It can't modify this shell's env — Unix won't let a
        # child process touch its parent's. We handle that ourselves below.
        pipx ensurepath >/dev/null 2>&1 || true
        USED_PIPX=1
    else
        warn "pipx unavailable — falling back to 'pip install --user'."
        warn "you may need to add the install dir to your shell rc manually."
        python3 -m pip install --user uhh
    fi

    # Make sure both possible install locations are on PATH for the rest of
    # this script: pipx uses ~/.local/bin, while `pip install --user` may use
    # ~/Library/Python/X.Y/bin on macOS python.org Python.
    ensure_path_has "$HOME/.local/bin"
    ensure_path_has "$(user_bin_dir)"
}

# ----------------------------------------------------------------------------
main "$@"
