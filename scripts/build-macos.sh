#!/bin/bash
set -eo pipefail

if [[ "$(uname -s)" != Darwin ]]; then
    echo "此入口仅用于 macOS；Windows 请使用 scripts/build.ps1。" >&2
    exit 1
fi

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${1:-rp2350}"
if [[ $# -gt 0 ]]; then shift; fi
UART_B=OFF
if [[ "${1:-}" == --enable-uart-b ]]; then
    UART_B=ON
    shift
fi
if [[ $# -gt 0 || ! "$TARGET" =~ ^(rp2350|esp32|all)$ ]]; then
    echo "用法：bash scripts/build-macos.sh [rp2350|esp32|all] [--enable-uart-b]" >&2
    exit 1
fi

build_rp2350() (
    PICO_ROOT="${PICO_ROOT:-$HOME/.pico-sdk}"
    SDK_VERSION="${SDK_VERSION:-2.3.1}"
    SDK_PATH="${PICO_SDK_PATH:-$PICO_ROOT/sdk/$SDK_VERSION}"
    TOOLCHAIN_PATH="${PICO_TOOLCHAIN_PATH:-$PICO_ROOT/toolchain/15_2_Rel1}"
    PIOASM_PATH="${PIOASM_DIR:-$PICO_ROOT/tools/$SDK_VERSION/pioasm}"
    PICOTOOL_PATH="${PICOTOOL_DIR:-$PICO_ROOT/picotool/$SDK_VERSION/picotool}"
    NINJA_BIN="${NINJA_BIN:-$PICO_ROOT/ninja/v1.13.2/ninja}"
    CMAKE_BIN="${CMAKE_BIN:-cmake}"
    for required in "$SDK_PATH/external/pico_sdk_import.cmake" \
        "$TOOLCHAIN_PATH/bin/arm-none-eabi-gcc" "$PIOASM_PATH/pioasmConfig.cmake" \
        "$PICOTOOL_PATH/picotoolConfig.cmake" "$NINJA_BIN"; do
        if [[ ! -e "$required" ]]; then
            echo "缺少构建依赖：$required（参见 README 的 macOS 构建说明）" >&2
            exit 1
        fi
    done
    "$CMAKE_BIN" -S "$PROJECT_ROOT/firmware/rp2350" -B "$PROJECT_ROOT/build/macos/rp2350" -G Ninja \
        "-DCMAKE_MAKE_PROGRAM=$NINJA_BIN" "-DPICO_SDK_PATH=$SDK_PATH" \
        "-DPICO_TOOLCHAIN_PATH=$TOOLCHAIN_PATH" "-Dpioasm_DIR=$PIOASM_PATH" \
        "-Dpicotool_DIR=$PICOTOOL_PATH" -DCMAKE_BUILD_TYPE=Release "-DAGENT_ENABLE_UART_B=$UART_B"
    "$CMAKE_BIN" --build "$PROJECT_ROOT/build/macos/rp2350" --parallel
    echo "构建完成：$PROJECT_ROOT/build/macos/rp2350/agent_probe.uf2"
)

build_esp32() (
    export IDF_PATH="${IDF_PATH:-$PROJECT_ROOT/build/macos/deps/esp-idf}"
    export IDF_TOOLS_PATH="${IDF_TOOLS_PATH:-$PROJECT_ROOT/build/macos/deps/espressif}"
    if [[ ! -f "$IDF_PATH/export.sh" ]]; then
        echo "缺少 ESP-IDF 6.1：$IDF_PATH（参见 README 的 macOS 构建说明）" >&2
        exit 1
    fi
    source "$IDF_PATH/export.sh"
    if [[ "$(idf.py --version)" != "ESP-IDF v6.1" ]]; then
        echo "此构建入口要求 ESP-IDF v6.1。" >&2
        exit 1
    fi
    idf.py -C "$PROJECT_ROOT/firmware/esp32" -B "$PROJECT_ROOT/build/macos/esp32" \
        -D "SDKCONFIG=$PROJECT_ROOT/build/macos/esp32/sdkconfig" build
    echo "构建完成：$PROJECT_ROOT/build/macos/esp32/agent_esp32_coprocessor.bin"
)

case "$TARGET" in
    rp2350) build_rp2350 ;;
    esp32) build_esp32 ;;
    all) build_rp2350; build_esp32 ;;
esac
