#!/usr/bin/env bash
# DebArk installer, maintained by Mr.Nick (@Mohammad-Nicke).
# Project: https://github.com/Mohammad-Nicke/debark
set -euo pipefail
umask 022

REPO_URL="https://github.com/Mohammad-Nicke/debark.git"
RELEASE_BASE_URL="https://github.com/Mohammad-Nicke/debark/releases/download/v0.1"
REPO_ARCHIVE_URL="$RELEASE_BASE_URL/debark-source-main.tar.gz"
RELEASE_MANIFEST_URL="$RELEASE_BASE_URL/debark-source-main.manifest"
RELEASE_SIGNATURE_URL="$RELEASE_MANIFEST_URL.sig"
RELEASE_PUBLIC_KEY_URL="https://raw.githubusercontent.com/Mohammad-Nicke/debark/main/src/debark/data/debark-release-public.pem"
RELEASE_PUBLIC_KEY_SHA256="e6054fe47a50b5eb33c955cac256746abe86c23e3aa4aeaf85216a89918ee55f"
DEPENDENCY_BUNDLE_URL="$RELEASE_BASE_URL/debark-arch-dependencies-x86_64.tar.gz"
MAINTAINER="Mr.Nick (@Mohammad-Nicke)"
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

ensure_openssl() {
    if command -v openssl >/dev/null 2>&1; then
        return 0
    fi
    ensure_noninteractive_admin
    printf 'Installing OpenSSL for official release verification...\n'
    if run_as_admin pacman -S --needed "${PACMAN_CONFIRM_ARGS[@]}" openssl &&
        command -v openssl >/dev/null 2>&1; then
        return 0
    fi
    printf 'pacman could not provide OpenSSL; checking the Arch dependency bundle.\n' >&2
    install_dependencies_from_bundle openssl
    command -v openssl >/dev/null 2>&1 ||
        die "OpenSSL is still unavailable after the dependency bundle installation."
}

download_file() {
    local url="$1"
    local destination="$2"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL --retry 3 --connect-timeout 15 "$url" -o "$destination" ||
            die "Could not download a required DebArk release file from GitHub. Check the connection and retry."
    elif command -v wget >/dev/null 2>&1; then
        wget -q "$url" -O "$destination" ||
            die "Could not download a required DebArk release file from GitHub. Check the connection and retry."
    else
        die "Install curl or wget to download DebArk from GitHub."
    fi
}

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
    bundle_age_seconds=$((now_epoch - generated_epoch))
    if (( generated_epoch - now_epoch > 86400 )); then
        die "The dependency bundle build date is more than one day in the future; refusing an invalid freshness timestamp."
    fi
    if (( bundle_age_seconds > BUNDLE_MAX_AGE_DAYS * 86400 )); then
        bundle_age_hours=$((bundle_age_seconds / 3600))
        printf '\nThe dependency bundle is about %s hours old; the recommended maximum age is %s days.\n' \
            "$bundle_age_hours" "$BUNDLE_MAX_AGE_DAYS" >&2
        printf 'The age is advisory only; continuing. Signature, checksum, architecture, and pacman package-state checks remain active.\n' >&2
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

ensure_openssl

SOURCE_DIR="$TMP_DIR/source"
ARCHIVE_PATH="$TMP_DIR/debark-source-main.tar.gz"
MANIFEST_PATH="$TMP_DIR/debark-source-main.manifest"
SIGNATURE_PATH="$TMP_DIR/debark-source-main.manifest.sig"
PUBLIC_KEY_PATH="$TMP_DIR/debark-release-public.pem"
mkdir -p "$SOURCE_DIR"
printf 'Fetching the signed DebArk source release from GitHub...\n'
download_file "$REPO_ARCHIVE_URL" "$ARCHIVE_PATH"
download_file "$RELEASE_MANIFEST_URL" "$MANIFEST_PATH"
download_file "$RELEASE_SIGNATURE_URL" "$SIGNATURE_PATH"
download_file "$RELEASE_PUBLIC_KEY_URL" "$PUBLIC_KEY_PATH"

[[ "$(head -n 1 "$MANIFEST_PATH")" = "DEBARK-OFFICIAL-MANIFEST 1" ]] ||
    die "The DebArk release manifest has an unsupported format."
MANIFEST_REPOSITORY="$(sed -n 's/^repository=//p' "$MANIFEST_PATH")"
MANIFEST_COMMIT="$(sed -n 's/^commit=//p' "$MANIFEST_PATH")"
MANIFEST_VERSION="$(sed -n 's/^version=//p' "$MANIFEST_PATH")"
MANIFEST_ARCHIVE_SHA256="$(sed -n 's/^archive_sha256=//p' "$MANIFEST_PATH")"
MANIFEST_ROOT_KIND="$(sed -n 's/^root_kind=//p' "$MANIFEST_PATH")"
[ "$MANIFEST_REPOSITORY" = "https://github.com/Mohammad-Nicke/debark" ] ||
    die "The signed release manifest names a different repository."
[[ "$MANIFEST_COMMIT" =~ ^[[:xdigit:]]{40,64}$ ]] ||
    die "The signed release manifest contains an invalid commit ID."
[[ "$MANIFEST_VERSION" =~ ^[0-9]+(\.[0-9]+)+$ ]] ||
    die "The signed release manifest contains an invalid version."
[ "$MANIFEST_ROOT_KIND" = "source" ] ||
    die "The signed release manifest is not for the Python source distribution."
[[ "$MANIFEST_ARCHIVE_SHA256" =~ ^[[:xdigit:]]{64}$ ]] ||
    die "The signed release manifest contains an invalid source checksum."

PUBLIC_KEY_FINGERPRINT="$(openssl pkey -pubin -in "$PUBLIC_KEY_PATH" -outform DER | sha256sum | cut -d ' ' -f 1)" ||
    die "Could not read the official DebArk release key."
[ "$PUBLIC_KEY_FINGERPRINT" = "$RELEASE_PUBLIC_KEY_SHA256" ] ||
    die "The official DebArk release key does not match the pinned key."
openssl pkeyutl -verify -pubin -inkey "$PUBLIC_KEY_PATH" -rawin \
    -in "$MANIFEST_PATH" -sigfile "$SIGNATURE_PATH" >/dev/null ||
    die "The DebArk release signature is invalid. Do not install this archive."

ARCHIVE_SHA256="$(sha256sum "$ARCHIVE_PATH" | cut -d ' ' -f 1)"
[ "$ARCHIVE_SHA256" = "$MANIFEST_ARCHIVE_SHA256" ] ||
    die "The DebArk source archive does not match the signed release manifest. Retry after the release finishes updating."
while IFS= read -r archive_entry; do
    case "$archive_entry" in
        /*|..|../*|*/../*|*/..) die "The signed source archive contains an unsafe path: $archive_entry" ;;
    esac
done < <(tar -tzf "$ARCHIVE_PATH")
while IFS= read -r archive_record; do
    case "${archive_record:0:1}" in
        -|d) ;;
        *) die "The signed source archive contains a link or special file." ;;
    esac
done < <(tar -tvzf "$ARCHIVE_PATH")
tar -xzf "$ARCHIVE_PATH" --strip-components=1 -C "$SOURCE_DIR" ||
    die "Could not unpack the signed DebArk source archive."

[ -f "$SOURCE_DIR/debark" ] || die "The source does not contain the debark command."
[ -d "$SOURCE_DIR/src/debark" ] || die "The source does not contain DebArk's Python modules."
[ -f "$SOURCE_DIR/dependencies/required-arch.txt" ] ||
    die "The source is missing dependencies/required-arch.txt."
[ -f "$SOURCE_DIR/LICENSE" ] || die "The source is missing its license file."
[ -f "$SOURCE_DIR/src/debark/data/debark-release-public.pem" ] ||
    die "The source is missing the official release public key."
cmp -s "$PUBLIC_KEY_PATH" "$SOURCE_DIR/src/debark/data/debark-release-public.pem" ||
    die "The signed archive contains a different release public key."
SOURCE_VERSION="$(sed -n 's/^__version__ = "\([^"]*\)"$/\1/p' \
    "$SOURCE_DIR/src/debark/__init__.py" | head -n 1)"
[ "$SOURCE_VERSION" = "$MANIFEST_VERSION" ] ||
    die "The signed release version does not match the package source."
install -m 644 "$MANIFEST_PATH" "$SOURCE_DIR/src/debark/data/official.manifest"
install -m 644 "$SIGNATURE_PATH" "$SOURCE_DIR/src/debark/data/official.manifest.sig"

printf '\n%sDebArk · Installer%s\n' "$C_CYAN" "$C_RESET"
printf '%sMaintainer: %s%s\n' "$C_DIM" "$MAINTAINER" "$C_RESET"
printf '%sProject: %s%s\n' "$C_DIM" "$REPO_URL" "$C_RESET"
printf 'System-wide installation: /usr/local/bin\n'

DETECTED_USER_HOME="$HOME"
CALLER_UID="$(id -u)"
CALLER_GID="$(id -g)"
if [ "$EUID" -eq 0 ]; then
    INVOKING_ID="${SUDO_USER:-${DOAS_USER:-${SUDO_UID:-${PKEXEC_UID:-}}}}"
    if [ -n "$INVOKING_ID" ]; then
        INVOKING_RECORD="$(getent passwd "$INVOKING_ID")"
        [ -n "$INVOKING_RECORD" ] || die "Could not find the invoking user's account."
        DETECTED_USER_HOME="$(cut -d: -f6 <<< "$INVOKING_RECORD")"
        CALLER_UID="$(cut -d: -f3 <<< "$INVOKING_RECORD")"
        CALLER_GID="$(cut -d: -f4 <<< "$INVOKING_RECORD")"
    fi
fi

if [ "${DEBARK_SELF_UPDATE:-0}" = "1" ]; then
    printf 'Updating the system-wide installation. Preferences, repository definitions, and DebArk caches will be kept.\n'
fi

EXPERIMENTAL_FEATURES="false"
EXPERIMENTAL_PROMPTED="false"
if [ -t 3 ] && [ "${DEBARK_SELF_UPDATE:-0}" != "1" ]; then
    printf '\n%sExperimental preview%s: signed APT sync/install, Debian CVE review and update checks.\n' "$C_CYAN" "$C_RESET"
    printf 'These features are beta, may be incomplete or fail, and do not automatically replace installed apps.\n'
    if ask_yes_no "Enable the experimental preview?" "n"; then
        EXPERIMENTAL_FEATURES="true"
    fi
    EXPERIMENTAL_PROMPTED="true"
fi

install_required_dependencies "$SOURCE_DIR/dependencies/required-arch.txt"
command -v python3 >/dev/null 2>&1 || die "The python package was installed, but python3 is still unavailable."
command -v ar >/dev/null 2>&1 || die "The binutils package was installed, but ar is still unavailable."

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
LICENSE_DIR="/usr/local/share/licenses/debark"
USER_BIN_DIR="$DETECTED_USER_HOME/.local/bin"
APT_COMMANDS=(apt apt-get apt-cache dpkg apt-real apt-get-real apt-cache-real dpkg-real)
for user_path in "$DETECTED_USER_HOME" "$DETECTED_USER_HOME/.local" "$USER_BIN_DIR"; do
    [ ! -L "$user_path" ] || die "Refusing to install user shortcuts through a symbolic link: $user_path"
done

AUTO_YES="false"
COLORS="true"
THREADS="4"
if [ -t 3 ] && [ "${DEBARK_SELF_UPDATE:-0}" != "1" ]; then
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
    if [ "$EUID" -ne 0 ]; then
        "$PRIVILEGE_HELPER" "$@"
    else
        "$@"
    fi
}

for apt_tool in "${APT_COMMANDS[@]}"; do
    apt_wrapper="$BIN_DIR/$apt_tool"
    if [ -L "$apt_wrapper" ]; then
        [ "$(readlink -f -- "$apt_wrapper" 2>/dev/null || true)" = "$BIN_DIR/debark" ] ||
            die "Refusing to replace an unrelated symbolic link: $apt_wrapper"
    elif [ -e "$apt_wrapper" ]; then
        die "Refusing to replace an existing command: $apt_wrapper"
    fi
    if pacman -Qo "$apt_wrapper" >/dev/null 2>&1; then
        die "Refusing to replace a pacman-owned path: $apt_wrapper"
    fi
done

if [ -e "$USER_BIN_DIR/debark" ] || [ -L "$USER_BIN_DIR/debark" ]; then
    user_debark_target="$(readlink -f -- "$USER_BIN_DIR/debark" 2>/dev/null || true)"
    if [ "$user_debark_target" != "$BIN_DIR/debark" ] &&
        ! { [ -f "$USER_BIN_DIR/debark" ] &&
            grep -Fq 'github.com/Mohammad-Nicke/debark' "$USER_BIN_DIR/debark"; }; then
        die "A non-DebArk command already exists at $USER_BIN_DIR/debark; remove or rename it before installing."
    fi
fi
for apt_tool in "${APT_COMMANDS[@]}"; do
    user_wrapper="$USER_BIN_DIR/$apt_tool"
    if [ -L "$user_wrapper" ]; then
        user_wrapper_target="$(readlink -f -- "$user_wrapper" 2>/dev/null || true)"
        if [ "$user_wrapper_target" != "$BIN_DIR/debark" ] &&
            [ "$user_wrapper_target" != "$USER_BIN_DIR/debark" ]; then
            die "Refusing to replace an unrelated symbolic link: $user_wrapper"
        fi
    elif [ -e "$user_wrapper" ]; then
        die "Refusing to replace an existing command: $user_wrapper"
    fi
done

for target in "$BIN_DIR/debark" "$LIB_DIR/debark" \
    "$BASH_COMPLETION_DIR/debark" "$ZSH_COMPLETION_DIR/_debark" \
    "$FISH_COMPLETION_DIR/debark.fish" "$MAN_DIR/debark.1" \
    "$LICENSE_DIR/LICENSE"; do
    if [ -L "$target" ]; then
        die "Refusing to replace a symbolic link: $target"
    fi
    if pacman -Qo "$target" >/dev/null 2>&1; then
        die "Refusing to replace a pacman-owned path: $target"
    fi
done

TARGET_VERSION="$(sed -n 's/^__version__ = "\([^"]*\)"$/\1/p' \
    "$SOURCE_DIR/src/debark/__init__.py" | head -n 1)"
[ -n "$TARGET_VERSION" ] || die "Could not read the version from the downloaded DebArk source."
if [ -d "$LIB_DIR/debark" ] && [ ! -L "$LIB_DIR/debark" ] && \
    [ -f "$LIB_DIR/debark/__init__.py" ] && [ ! -L "$LIB_DIR/debark/__init__.py" ]; then
    OLD_VERSION="$(sed -n 's/^__version__ = "\([^"]*\)"$/\1/p' \
        "$LIB_DIR/debark/__init__.py" | head -n 1)"
    if [ -n "$OLD_VERSION" ]; then
        printf 'Existing DebArk %s found; replacing it with %s.\n' "$OLD_VERSION" "$TARGET_VERSION"
    else
        printf 'Existing DebArk installation found; replacing its program files.\n'
    fi
elif [ -e "$BIN_DIR/debark" ]; then
    printf 'Existing DebArk command found; replacing the managed program files with %s.\n' \
        "$TARGET_VERSION"
fi

run_privileged mkdir -p "$BIN_DIR" "$LIB_DIR" "$CFG_DIR" "$DATA_DIR/cache" "$DATA_DIR/pkgs"

MODULE_STAGE="$LIB_DIR/.debark.new.$$"
MODULE_BACKUP="$LIB_DIR/.debark.old.$$"
run_privileged rm -rf -- "$MODULE_STAGE" "$MODULE_BACKUP"
run_privileged mkdir -m 755 "$MODULE_STAGE"
run_privileged cp -a "$SOURCE_DIR/src/debark/." "$MODULE_STAGE/"
run_privileged chown -R 0:0 "$MODULE_STAGE"
run_privileged find "$MODULE_STAGE" -type d -exec chmod 755 {} +
run_privileged find "$MODULE_STAGE" -type f -exec chmod 644 {} +
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
ENTRYPOINT_STAGE="$BIN_DIR/.debark.new.$$"
run_privileged rm -f -- "$ENTRYPOINT_STAGE"
run_privileged install -m 755 "$SOURCE_DIR/debark" "$ENTRYPOINT_STAGE"
if ! run_privileged mv -fT -- "$ENTRYPOINT_STAGE" "$BIN_DIR/debark"; then
    run_privileged rm -f -- "$ENTRYPOINT_STAGE"
    die "Could not replace the DebArk command."
fi
for apt_tool in "${APT_COMMANDS[@]}"; do
    apt_wrapper="$BIN_DIR/$apt_tool"
    if [ ! -L "$apt_wrapper" ]; then
        run_privileged ln -s -- "$BIN_DIR/debark" "$apt_wrapper"
    fi
done
run_privileged install -d -m 755 -o "$CALLER_UID" -g "$CALLER_GID" "$USER_BIN_DIR"
if [ -e "$USER_BIN_DIR/debark" ] || [ -L "$USER_BIN_DIR/debark" ]; then
    run_privileged rm -f -- "$USER_BIN_DIR/debark"
fi
run_privileged ln -s -- "$BIN_DIR/debark" "$USER_BIN_DIR/debark"
for apt_tool in "${APT_COMMANDS[@]}"; do
    user_wrapper="$USER_BIN_DIR/$apt_tool"
    if [ -e "$user_wrapper" ] || [ -L "$user_wrapper" ]; then
        run_privileged rm -f -- "$user_wrapper"
    fi
    run_privileged ln -s -- "$BIN_DIR/debark" "$user_wrapper"
done
run_privileged install -Dm 644 "$SOURCE_DIR/completions/debark.bash" \
    "$BASH_COMPLETION_DIR/debark"
run_privileged install -Dm 644 "$SOURCE_DIR/completions/_debark" \
    "$ZSH_COMPLETION_DIR/_debark"
run_privileged install -Dm 644 "$SOURCE_DIR/completions/debark.fish" \
    "$FISH_COMPLETION_DIR/debark.fish"
run_privileged install -Dm 644 "$SOURCE_DIR/man/debark.1" "$MAN_DIR/debark.1"
run_privileged install -Dm 644 "$SOURCE_DIR/LICENSE" "$LICENSE_DIR/LICENSE"
if [ -f "$SOURCE_DIR/LICENSE.fa" ]; then
    run_privileged install -Dm 644 "$SOURCE_DIR/LICENSE.fa" "$LICENSE_DIR/LICENSE.fa"
fi

CONFIG_TMP="$TMP_DIR/config.json"
python3 - "$CONFIG_TMP" "$AUTO_YES" "$COLORS" "$THREADS" "$EXPERIMENTAL_FEATURES" <<'PY'
import json
import sys
path, auto_yes, colors, threads, experimental_features = sys.argv[1:]
config = {
    "managed_by": "DebArk",
    "maintainer": "Mr.Nick (@Mohammad-Nicke)",
    "repository": "https://github.com/Mohammad-Nicke/debark",
    "auto_yes": auto_yes == "true",
    "colors": colors == "true",
    "threads": int(threads),
    "experimental_features": experimental_features == "true",
}
with open(path, "w", encoding="utf-8") as stream:
    json.dump(config, stream, indent=2)
    stream.write("\n")
PY

if [ ! -f "$CFG_DIR/config.json" ]; then
    run_privileged install -m 600 "$CONFIG_TMP" "$CFG_DIR/config.json"
else
    printf 'Keeping existing preferences at %s/config.json\n' "$CFG_DIR"
fi

if [ "$EXPERIMENTAL_PROMPTED" = "true" ]; then
    CONFIG_FLAG_HELPER="$TMP_DIR/set-experimental-config.py"
    cat > "$CONFIG_FLAG_HELPER" <<'PY'
import json
import os
import sys
import tempfile

path, enabled = sys.argv[1:]
with open(path, encoding="utf-8") as stream:
    values = json.load(stream)
if not isinstance(values, dict):
    raise SystemExit("DebArk config must contain a JSON object")
values["experimental_features"] = enabled == "true"
fd, temporary = tempfile.mkstemp(prefix=".debark-config-", dir=os.path.dirname(path))
try:
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(values, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
except Exception:
    try:
        os.unlink(temporary)
    except FileNotFoundError:
        pass
    raise
PY
    run_privileged python3 "$CONFIG_FLAG_HELPER" "$CFG_DIR/config.json" "$EXPERIMENTAL_FEATURES"
fi

printf '\n%sDebArk installed:%s %s/debark\n' "$C_GREEN" "$C_RESET" "$BIN_DIR"
printf 'Configuration: %s/config.json\n' "$CFG_DIR"
printf 'Shell completions and man page installed.\n'
printf 'System and user command shortcuts installed.\n'
printf 'Running DebArk compatibility repair...\n'
run_privileged "$BIN_DIR/debark" fix
hash -r
printf '\nRefresh this terminal command cache with hash -r (Bash) or rehash (Zsh), if an older command is still cached.\n'
printf 'Use apt-real, apt-get-real, apt-cache-real, or dpkg-real for the installed original tools.\n'
printf 'Try: debark help\n'
