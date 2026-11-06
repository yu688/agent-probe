cmake_minimum_required(VERSION 3.20)

set(PICO_BOARD agent_rp2350a CACHE STRING "核心板配置")
set(PICO_BOARD_HEADER_DIRS "${CMAKE_CURRENT_LIST_DIR}/boards" CACHE STRING "板级头文件目录")
set(PICO_PLATFORM rp2350-arm-s CACHE STRING "RP2350 Arm Cortex-M33")

if(NOT PICO_SDK_PATH AND DEFINED ENV{PICO_SDK_PATH})
    set(PICO_SDK_PATH $ENV{PICO_SDK_PATH})
endif()
if(NOT EXISTS "${PICO_SDK_PATH}/external/pico_sdk_import.cmake")
    message(FATAL_ERROR "请传入 -DPICO_SDK_PATH=SDK目录，或运行 scripts/build.ps1")
endif()
include("${PICO_SDK_PATH}/external/pico_sdk_import.cmake")

project(rp2350_agent_probe VERSION 0.1.0 LANGUAGES C CXX ASM)
set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)
pico_sdk_init()

add_library(lwrb STATIC third_party/lwrb/lwrb.c)
target_include_directories(lwrb PUBLIC third_party/lwrb/include)

set(AGENT_USB_VID "0xCAFE" CACHE STRING "实验用 USB VID")
set(AGENT_USB_PID "0x40F0" CACHE STRING "实验用 USB PID")
set(AGENT_UART_A_RX_PIN 5 CACHE STRING "通道0 UART1 RX")
set(AGENT_UART_B_RX_PIN 13 CACHE STRING "通道1 UART0 RX，默认不启用")
option(AGENT_ENABLE_UART_B "启用第二路 UART0 RX" OFF)

add_executable(agent_probe
    src/main.c
    src/protocol.c
    src/usb_descriptors.c
    src/uart_capture.c
    src/logic_capture.c
    src/esp_spi_buffers.c
)
target_include_directories(agent_probe PRIVATE include)
target_compile_definitions(agent_probe PRIVATE
    PICO_STACK_SIZE=0x1000
    AGENT_USB_VID=${AGENT_USB_VID}
    AGENT_USB_PID=${AGENT_USB_PID}
    AGENT_UART_A_RX_PIN=${AGENT_UART_A_RX_PIN}
    AGENT_UART_B_RX_PIN=${AGENT_UART_B_RX_PIN}
    AGENT_ENABLE_UART_B=$<BOOL:${AGENT_ENABLE_UART_B}>
)
target_compile_options(agent_probe PRIVATE -Wall -Wextra -Werror -fstack-usage)
pico_generate_pio_header(agent_probe "${CMAKE_CURRENT_LIST_DIR}/pio/logic_capture.pio")
target_link_libraries(agent_probe PRIVATE
    pico_stdlib pico_unique_id hardware_uart hardware_irq hardware_sync
    hardware_pio hardware_dma hardware_clocks tinyusb_device lwrb
)
pico_enable_stdio_usb(agent_probe 0)
pico_enable_stdio_uart(agent_probe 0)
pico_set_program_name(agent_probe "RP2350 Agent Probe")
pico_set_program_version(agent_probe ${PROJECT_VERSION})
pico_add_extra_outputs(agent_probe)
