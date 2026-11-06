#ifndef AGENT_RP2350A_BOARD_H
#define AGENT_RP2350A_BOARD_H

// 独立板级配置，不继承 Pico 2 的 GP23 电源控制等板载功能。
pico_board_cmake_set(PICO_PLATFORM, rp2350)
#define PICO_RP2350A 1
#define PICO_XOSC_STARTUP_DELAY_MULTIPLIER 64
#define PICO_BOOT_STAGE2_CHOOSE_GENERIC_03H 1
#define PICO_FLASH_SPI_CLKDIV 4
pico_board_cmake_set_default(PICO_FLASH_SIZE_BYTES, (4 * 1024 * 1024))
#ifndef PICO_FLASH_SIZE_BYTES
#define PICO_FLASH_SIZE_BYTES (4 * 1024 * 1024)
#endif
// 兼容未知修订的现有核心板；输入漏电勘误仍需实板核对。
pico_board_cmake_set_default(PICO_RP2350_A2_SUPPORTED, 1)
#define PICO_RP2350_A2_SUPPORTED 1

#endif
