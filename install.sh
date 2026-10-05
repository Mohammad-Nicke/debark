#!/usr/bin/env bash
# DebArk installer, maintained by Master Nick (@Mohammad-Nicke).
# Project: https://github.com/Mohammad-Nicke/debark
set -euo pipefail
umask 022

REPO_URL="https://github.com/Mohammad-Nicke/debark.git"
REPO_ARCHIVE_URL="https://github.com/Mohammad-Nicke/debark/archive/refs/heads/main.tar.gz"
DEPENDENCY_BUNDLE_URL="https://github.com/Mohammad-Nicke/debark/releases/download/arch-dependencies/debark-arch-dependencies-x86_64.tar.gz"
MAINTAINER="Master Nick (@Mohammad-Nicke)"
BUNDLE_MAX_AGE_DAYS=14
TMP_DIR="$(mktemp -d -t debark-install-XXXXXX)"
trap 'rm -rf "$TMP_DIR"' EXIT

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    C_CYAN=$'\033[36m'
    C_GREEN=$'\033[32m'
    C_RED=$'\033[31m'
    C_DIM=$'\033[2m'
    C_RESET=$'\033[0m'
else
    C_CYAN=""
    C_GREEN=""
    C_RED=""
    C_DIM=""
    C_RESET=""
fi

if { exec 3</dev/tty; } 2>/dev/null; then
    :
else
    exec 3</dev/null
fi

PACMAN_CONFIRM_ARGS=()
if [ ! -t 3 ]; then
    PACMAN_CONFIRM_ARGS+=(--noconfirm)
fi

ask_value() {
    local prompt="$1"
    local default="$2"
    local answer
    if [ -t 3 ]; then
        read -r -p "${C_CYAN}${prompt}${C_RESET} [$default] " answer <&3 || answer=""
    else
        answer=""
    fi
    if [ -n "$answer" ]; then printf '%s' "$answer"; else printf '%s' "$default"; fi
}

ask_yes_no() {
    local prompt="$1"
    local default="$2"
    local answer
    if [ -t 3 ]; then
        read -r -p "${C_CYAN}${prompt}${C_RESET} [$default] " answer <&3 || answer=""
    else
        answer=""
    fi
    if [ -z "$answer" ]; then answer="$default"; fi
    [[ "$answer" =~ ^[Yy]$ ]]
}

die() {
    printf '%sDebArk installer:%s %s\n' "$C_RED" "$C_RESET" "$1" >&2
    exit 1
}

run_as_admin() {
    if [ "$EUID" -eq 0 ]; then
        "$@"
    elif command -v sudo >/dev/null 2>&1; then
        if [ -t 3 ]; then sudo "$@"; else sudo -n "$@"; fi
    elif command -v doas >/dev/null 2>&1; then
        if [ -t 3 ]; then doas "$@"; else doas -n "$@"; fi
    else
        die "Installing required Arch packages needs root, sudo, or doas."
    fi
}

ensure_noninteractive_admin() {
    [ "$EUID" -eq 0 ] && return 0
    [ -t 3 ] && return 0

    if command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
        return 0
    fi
    if command -v doas >/dev/null 2>&1 && doas -n true >/dev/null 2>&1; then
        return 0
    fi

    die "Installing Arch dependencies without a terminal needs root or passwordless sudo/doas. Rerun in a terminal or as root."
}

command -v pacman >/dev/null 2>&1 || die "DebArk requires Arch Linux and pacman."

SOURCE_DIR="$TMP_DIR/source"
ARCHIVE_PATH="$TMP_DIR/debark-main.tar.gz"
mkdir -p "$SOURCE_DIR"
printf 'Fetching DebArk source from GitHub...\n'
if command -v curl >/dev/null 2>&1; then
    curl -fsSL --retry 3 --connect-timeout 15 "$REPO_ARCHIVE_URL" -o "$ARCHIVE_PATH" ||
        die "Could not download DebArk from $REPO_URL. Check your internet connection and try again."
elif command -v wget >/dev/null 2>&1; then
    wget -q "$REPO_ARCHIVE_URL" -O "$ARCHIVE_PATH" ||
        die "Could not download DebArk from $REPO_URL. Check your internet connection and try again."
else
    die "Install curl or wget to download DebArk from GitHub."
fi
tar -xzf "$ARCHIVE_PATH" --strip-components=1 -C "$SOURCE_DIR" ||
    die "Could not unpack the DebArk source archive."

[ -f "$SOURCE_DIR/debark" ] || die "The source does not contain the debark command."
[ -d "$SOURCE_DIR/src/debark" ] || die "The source does not contain DebArk's Python modules."
[ -f "$SOURCE_DIR/dependencies/required-arch.txt" ] ||
    die "The source is missing dependencies/required-arch.txt."

install_required_dependencies() {
    local manifest="$1"
    local package
    local -a required=()
    local -a missing=()

    while IFS= read -r package || [ -n "$package" ]; do
        package="${package%%#*}"
        package="${package//[[:space:]]/}"
        [ -z "$package" ] && continue
        [[ "$package" =~ ^[A-Za-z0-9@._+-]+$ ]] ||
            die "Invalid package name in $manifest: $package"
        required+=("$package")
    done < "$manifest"

    [ "${#required[@]}" -gt 0 ] || die "No required packages are listed in $manifest."

    for package in "${required[@]}"; do
        if ! pacman -Qq "$package" >/dev/null 2>&1; then
            missing+=("$package")
        fi
    done

    [ "${#missing[@]}" -eq 0 ] && return 0

    ensure_noninteractive_admin
    printf 'Installing required packages through pacman: %s\n' "${missing[*]}"
    if run_as_admin pacman -S --needed "${PACMAN_CONFIRM_ARGS[@]}" "${missing[@]}"; then
        missing=()
        for package in "${required[@]}"; do
            if ! pacman -Qq "$package" >/dev/null 2>&1; then
                missing+=("$package")
            fi
        done
        [ "${#missing[@]}" -eq 0 ] && return 0
        printf 'pacman did not install all required packages; checking the DebArk bundle.\n' >&2
    else
        printf 'pacman could not install the required packages; checking the DebArk bundle.\n' >&2
    fi

    install_dependencies_from_bundle "${missing[@]}"
}

install_dependencies_from_bundle() {
    local -a requested=("$@")
    local bundle_archive="$TMP_DIR/debark-arch-dependencies-x86_64.tar.gz"
    local bundle_dir="$TMP_DIR/arch-dependencies"
    local entry
    local line
    local key
    local value
    local bundle_arch=""
    local generated_utc=""
    local bundle_format=""
    local bundle_required=""
    local generated_epoch
    local now_epoch
    local installed_version
    local bundle_name
    local bundle_version
    local comparison
    local checksum_hash
    local checksum_file
    local -a package_files=()
    local -a install_files=()
    local -A checksummed=()

    [ "$(uname -m)" = "x86_64" ] ||
        die "The hosted dependency bundle is currently available for x86_64 only. Install the required packages with pacman on this architecture."

    printf 'Downloading the current x86_64 dependency bundle from GitHub...\n'
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL --retry 3 --connect-timeout 15 "$DEPENDENCY_BUNDLE_URL" -o "$bundle_archive" ||
            die "Could not download the DebArk dependency bundle from GitHub. Check your connection or restore access to an Arch mirror."
    elif command -v wget >/dev/null 2>&1; then
        wget -q "$DEPENDENCY_BUNDLE_URL" -O "$bundle_archive" ||
            die "Could not download the DebArk dependency bundle from GitHub. Check your connection or restore access to an Arch mirror."
    else
        die "Install curl or wget to download the DebArk dependency bundle from GitHub."
    fi

    [ -f "$bundle_archive" ] || die "Dependency bundle not found: $bundle_archive"
    mkdir -p "$bundle_dir"
    while IFS= read -r entry; do
        [[ "$entry" != */* && "$entry" != *\\* ]] ||
            die "The dependency bundle contains an unsafe path: $entry"
        case "$entry" in
            manifest.txt|SHA256SUMS|*.pkg.tar.zst|*.pkg.tar.zst.sig) ;;
            *) die "The dependency bundle contains an unexpected file: $entry" ;;
        esac
    done < <(tar -tzf "$bundle_archive")
    while IFS= read -r line; do
        [[ "${line:0:1}" = "-" ]] ||
            die "The dependency bundle contains a non-regular archive entry."
    done < <(tar -tvzf "$bundle_archive")
    tar -xzf "$bundle_archive" -C "$bundle_dir" ||
        die "Could not unpack the DebArk dependency bundle."
    if [ ! -f "$bundle_dir/manifest.txt" ] || [ ! -f "$bundle_dir/SHA256SUMS" ]; then
        die "The dependency bundle is missing its manifest or checksums."
    fi
    while IFS=' ' read -r checksum_hash checksum_file; do
        [[ "$checksum_hash" =~ ^[[:xdigit:]]{64}$ ]] ||
            die "The dependency bundle has an invalid checksum entry."
        [[ "$checksum_file" =~ ^[A-Za-z0-9@._:+-]+\.pkg\.tar\.zst(\.sig)?$ ]] ||
            die "The dependency bundle checksum file contains an unsafe path."
        checksummed["$checksum_file"]=1
    done < "$bundle_dir/SHA256SUMS"
    (cd "$bundle_dir" && sha256sum -c SHA256SUMS) ||
        die "The dependency bundle failed its checksum verification."

    while IFS='=' read -r key value || [ -n "$key" ]; do
        case "$key" in
            format) bundle_format="$value" ;;
            architecture) bundle_arch="$value" ;;
            generated_utc) generated_utc="$value" ;;
            required) bundle_required="$value" ;;
        esac
    done < "$bundle_dir/manifest.txt"
    [ "$bundle_format" = "1" ] || die "The dependency bundle format is unsupported."
    [ "$bundle_arch" = "x86_64" ] ||
        die "The dependency bundle does not match this system's architecture."
    [[ "$generated_utc" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]] ||
        die "The dependency bundle has an invalid build date."
    generated_epoch="$(date -u -d "$generated_utc" +%s 2>/dev/null)" ||
        die "Could not read the dependency bundle build date."
    now_epoch="$(date -u +%s)"
    if (( now_epoch - generated_epoch > BUNDLE_MAX_AGE_DAYS * 86400 || generated_epoch - now_epoch > 86400 )); then
        die "The hosted dependency bundle is older than ${BUNDLE_MAX_AGE_DAYS} days or has a future build date. Update Arch through pacman, then retry."
    fi

    mapfile -d '' -t package_files < <(find "$bundle_dir" -maxdepth 1 -type f -name '*.pkg.tar.zst' -print0 | sort -z)
    [ "${#package_files[@]}" -gt 0 ] || die "The dependency bundle contains no Arch packages."
    for entry in "${package_files[@]}"; do
        if [[ -z "${checksummed[${entry##*/}]:-}" ]] ||
            [[ -z "${checksummed[${entry##*/}.sig]:-}" ]]; then
            die "The bundle checksum manifest does not cover ${entry##*/} and its signature."
        fi
        [ -f "$entry.sig" ] || die "The Arch package is missing its upstream signature: ${entry##*/}"
        read -r bundle_name bundle_version _ < <(pacman -Qp "$entry") ||
            die "Could not read package metadata from ${entry##*/}."
        if installed_version="$(pacman -Q "$bundle_name" 2>/dev/null)"; then
            installed_version="${installed_version#* }"
            comparison="$(vercmp "$installed_version" "$bundle_version")"
            if [ "$comparison" -ge 0 ]; then
                continue
            fi
            die "Installed package '$bundle_name' is older than the release bundle. Refusing a partial Arch upgrade; update the system with pacman first, then retry."
        fi
        install_files+=("$entry")
    done

    if [ "${#install_files[@]}" -gt 0 ]; then
        if [ -t 3 ]; then
            printf 'Installing the signed Arch packages from the DebArk bundle. Pacman will show its normal confirmation prompt.\n'
        else
            printf 'Installing the signed Arch packages from the DebArk bundle without an interactive prompt.\n'
        fi
        run_as_admin pacman -U --needed "${PACMAN_CONFIRM_ARGS[@]}" "${install_files[@]}" ||
            die "Pacman rejected the dependency bundle. Check the Arch keyring and system package state, then use a current Arch repository."
    fi

    for entry in "${requested[@]}"; do
        [[ " $bundle_required " = *" $entry "* ]] ||
            die "The dependency bundle does not include required package '$entry'."
        pacman -Qq "$entry" >/dev/null 2>&1 ||
            die "Required package '$entry' is still unavailable after the bundle installation."
    done
}

printf '\n%sDebArk · Installer%s\n' "$C_CYAN" "$C_RESET"
printf '%sMaintainer: %s%s\n' "$C_DIM" "$MAINTAINER" "$C_RESET"
printf '%sProject: %s%s\n' "$C_DIM" "$REPO_URL" "$C_RESET"
printf '1) System-wide: /usr/local/bin (requires root, sudo, or doas)\n'
printf '2) Current user: ~/.local/bin\n'
DEFAULT_MODE="1"
if [ ! -t 3 ] && [ "$EUID" -ne 0 ]; then DEFAULT_MODE="2"; fi
MODE="$(ask_value 'Choose installation mode (1 or 2)' "$DEFAULT_MODE")"
case "$MODE" in
    1) INSTALL_MODE="system" ;;
    2) INSTALL_MODE="user" ;;
    *) die "Choose 1 or 2." ;;
esac

install_required_dependencies "$SOURCE_DIR/dependencies/required-arch.txt"
command -v python3 >/dev/null 2>&1 || die "The python package was installed, but python3 is still unavailable."
command -v ar >/dev/null 2>&1 || die "The binutils package was installed, but ar is still unavailable."

INSTALL_UID=""
INSTALL_GID=""
if [ "$INSTALL_MODE" = "system" ]; then
    PRIVILEGE_HELPER=""
    if [ "$EUID" -ne 0 ]; then
        if command -v sudo >/dev/null 2>&1; then
            PRIVILEGE_HELPER="sudo"
        elif command -v doas >/dev/null 2>&1; then
            PRIVILEGE_HELPER="doas"
        else
            die "System installation requires root, sudo, or doas."
        fi
    fi
    BIN_DIR="/usr/local/bin"
    LIB_DIR="/usr/local/lib"
    CFG_DIR="/etc/debark"
    DATA_DIR="/var/lib/debark"
    BASH_COMPLETION_DIR="/usr/local/share/bash-completion/completions"
    ZSH_COMPLETION_DIR="/usr/local/share/zsh/site-functions"
    FISH_COMPLETION_DIR="/usr/local/share/fish/vendor_completions.d"
    MAN_DIR="/usr/local/share/man/man1"
else
    USER_HOME="$HOME"
    if [ "$EUID" -eq 0 ] && [ -n "${SUDO_USER:-}" ]; then
        USER_HOME="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
        [ -n "$USER_HOME" ] || die "Could not find the invoking user's home directory."
        INSTALL_UID="$(id -u "$SUDO_USER")"
        INSTALL_GID="$(id -g "$SUDO_USER")"
    fi
    BIN_DIR="$USER_HOME/.local/bin"
    LIB_DIR="$USER_HOME/.local/lib"
    CFG_DIR="$USER_HOME/.config/debark"
    DATA_DIR="$USER_HOME/.local/share/debark"
    BASH_COMPLETION_DIR="$USER_HOME/.local/share/bash-completion/completions"
    ZSH_COMPLETION_DIR="$USER_HOME/.local/share/zsh/site-functions"
    FISH_COMPLETION_DIR="$USER_HOME/.local/share/fish/vendor_completions.d"
    MAN_DIR="$USER_HOME/.local/share/man/man1"
fi

AUTO_YES="false"
COLORS="true"
THREADS="4"
if [ -t 3 ]; then
    if ask_yes_no "Auto-confirm DebArk prompts by default?" "n"; then AUTO_YES="true"; fi
    if ask_yes_no "Enable colored output?" "y"; then COLORS="true"; else COLORS="false"; fi
    THREADS="$(ask_value 'Copy workers (1-16)' '4')"
    if ! [[ "$THREADS" =~ ^[0-9]+$ ]]; then THREADS="4"; fi
    if [ "$THREADS" -lt 1 ]; then THREADS="1"; fi
    if [ "$THREADS" -gt 16 ]; then THREADS="16"; fi
fi

if [ -n "$(find "$SOURCE_DIR/src/debark" -type l -print -quit)" ]; then
    die "The Python package contains a symbolic link; refusing system installation."
fi

run_privileged() {
    if [ "$INSTALL_MODE" = "system" ] && [ "$EUID" -ne 0 ]; then
        "$PRIVILEGE_HELPER" "$@"
    else
        "$@"
    fi
}

for target in "$BIN_DIR/debark" "$LIB_DIR/debark" \
    "$BASH_COMPLETION_DIR/debark" "$ZSH_COMPLETION_DIR/_debark" \
    "$FISH_COMPLETION_DIR/debark.fish" "$MAN_DIR/debark.1"; do
    if [ -L "$target" ]; then
        die "Refusing to replace a symbolic link: $target"
    fi
    if pacman -Qo "$target" >/dev/null 2>&1; then
        die "Refusing to replace a pacman-owned path: $target"
    fi
done

run_privileged mkdir -p "$BIN_DIR" "$LIB_DIR" "$CFG_DIR" "$DATA_DIR/cache" "$DATA_DIR/pkgs"

MODULE_STAGE="$LIB_DIR/.debark.new.$$"
MODULE_BACKUP="$LIB_DIR/.debark.old.$$"
run_privileged rm -rf -- "$MODULE_STAGE" "$MODULE_BACKUP"
run_privileged mkdir -m 755 "$MODULE_STAGE"
run_privileged cp -a "$SOURCE_DIR/src/debark/." "$MODULE_STAGE/"
if [ "$INSTALL_MODE" = "system" ]; then
    run_privileged chown -R 0:0 "$MODULE_STAGE"
    run_privileged find "$MODULE_STAGE" -type d -exec chmod 755 {} +
    run_privileged find "$MODULE_STAGE" -type f -exec chmod 644 {} +
fi
if [ -d "$LIB_DIR/debark" ] || [ -L "$LIB_DIR/debark" ]; then
    run_privileged mv -- "$LIB_DIR/debark" "$MODULE_BACKUP"
fi
if run_privileged mv -- "$MODULE_STAGE" "$LIB_DIR/debark"; then
    run_privileged rm -rf -- "$MODULE_BACKUP"
else
    if [ -d "$MODULE_BACKUP" ] || [ -L "$MODULE_BACKUP" ]; then
        run_privileged mv -- "$MODULE_BACKUP" "$LIB_DIR/debark"
    fi
    die "Could not install DebArk's Python modules."
fi
run_privileged install -m 755 "$SOURCE_DIR/debark" "$BIN_DIR/debark"
run_privileged install -Dm 644 "$SOURCE_DIR/completions/debark.bash" \
    "$BASH_COMPLETION_DIR/debark"
run_privileged install -Dm 644 "$SOURCE_DIR/completions/_debark" \
    "$ZSH_COMPLETION_DIR/_debark"
run_privileged install -Dm 644 "$SOURCE_DIR/completions/debark.fish" \
    "$FISH_COMPLETION_DIR/debark.fish"
run_privileged install -Dm 644 "$SOURCE_DIR/man/debark.1" "$MAN_DIR/debark.1"

CONFIG_TMP="$TMP_DIR/config.json"
python3 - "$CONFIG_TMP" "$AUTO_YES" "$COLORS" "$THREADS" <<'PY'
import json
import sys
path, auto_yes, colors, threads = sys.argv[1:]
config = {
    "managed_by": "DebArk",
    "maintainer": "Master Nick (@Mohammad-Nicke)",
    "repository": "https://github.com/Mohammad-Nicke/debark",
    "auto_yes": auto_yes == "true",
    "colors": colors == "true",
    "threads": int(threads),
}
with open(path, "w", encoding="utf-8") as stream:
    json.dump(config, stream, indent=2)
    stream.write("\n")
PY

if [ ! -f "$CFG_DIR/config.json" ]; then
    run_privileged install -m 600 "$CONFIG_TMP" "$CFG_DIR/config.json"
    if [ "$INSTALL_MODE" = "user" ] && [ "$EUID" -eq 0 ] && [ -n "${SUDO_USER:-}" ]; then
        run_privileged chown "$INSTALL_UID:$INSTALL_GID" "$CFG_DIR/config.json"
    fi
else
    printf 'Keeping existing preferences at %s/config.json\n' "$CFG_DIR"
fi

if [ "$INSTALL_MODE" = "user" ] && [ "$EUID" -eq 0 ] && [ -n "${SUDO_USER:-}" ]; then
    run_privileged chown -R "$INSTALL_UID:$INSTALL_GID" \
        "$BIN_DIR/debark" "$LIB_DIR/debark" \
        "$BASH_COMPLETION_DIR/debark" "$ZSH_COMPLETION_DIR/_debark" \
        "$FISH_COMPLETION_DIR/debark.fish" "$MAN_DIR/debark.1"
    for directory in "$USER_HOME/.local" "$USER_HOME/.local/bin" "$USER_HOME/.local/lib" \
        "$USER_HOME/.local/share" "$USER_HOME/.config" "$CFG_DIR" \
        "$(dirname "$BASH_COMPLETION_DIR")" "$BASH_COMPLETION_DIR" \
        "$(dirname "$ZSH_COMPLETION_DIR")" "$ZSH_COMPLETION_DIR" \
        "$(dirname "$FISH_COMPLETION_DIR")" "$FISH_COMPLETION_DIR" \
        "$(dirname "$MAN_DIR")" "$MAN_DIR" "$DATA_DIR" \
        "$DATA_DIR/cache" "$DATA_DIR/pkgs"; do
        if [ -d "$directory" ]; then
            run_privileged chown "$INSTALL_UID:$INSTALL_GID" "$directory"
        fi
    done
fi

printf '\n%sDebArk installed:%s %s/debark\n' "$C_GREEN" "$C_RESET" "$BIN_DIR"
printf 'Configuration: %s/config.json\n' "$CFG_DIR"
printf 'Shell completions and man page installed.\n'
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
        if [ "$INSTALL_MODE" = "user" ]; then
            printf 'Add %s to your PATH to run debark from a new shell.\n' "$BIN_DIR"
        fi
        ;;
esac
printf 'Try: debark help\n'
