cmake_minimum_required(VERSION 3.22)

set(IDF_TARGET "esp32s3" CACHE STRING "ESP32 协处理器目标")
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
idf_build_set_property(MINIMAL_BUILD ON)
project(agent_esp32_coprocessor)

if(NOT IDF_TARGET STREQUAL "esp32s3")
    message(FATAL_ERROR "协处理器工程要求 IDF_TARGET=esp32s3")
endif()
