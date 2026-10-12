cmake_minimum_required(VERSION 3.22)

#
# This file is generated only once,
# and is not re-generated if converter is called multiple times.
#
# User is free to modify the file as much as necessary
#

# Setup compiler settings
set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS ON)


# Define the build type
if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE "Debug")
endif()

# Set the project name
set(CMAKE_PROJECT_NAME TMC5160H7_StepMotor)

# Enable compile command to ease indexing with e.g. clangd
set(CMAKE_EXPORT_COMPILE_COMMANDS TRUE)

# Core project settings
project(${CMAKE_PROJECT_NAME})
message("Build type: " ${CMAKE_BUILD_TYPE})

# Enable CMake support for ASM and C languages
enable_language(C ASM)

# Create an executable object type
add_executable(${CMAKE_PROJECT_NAME}
		module/config.h)

# Add STM32CubeMX generated sources
add_subdirectory(cmake/stm32cubemx)

# Link directories setup
target_link_directories(${CMAKE_PROJECT_NAME} PRIVATE
    # Add user defined library search paths
)

# Add sources to executable
target_sources(${CMAKE_PROJECT_NAME} PRIVATE
    # Add user sources here
    module/hal/can.c
    module/hal/rtt_dbg.c
    module/hal/spi_port.c
    module/hal/wdg.c
    module/drv/tmc5160.c
    module/hal/uart_dbg.c
    module/hal/nvm.c
    module/algo/pid.c
    module/algo/queue.c
    
    module/app/can_cmd.c
    module/app/closed_loop.c
    module/app/homing.c
    module/app/motor_ctrl.c
    module/app/sched.c
    module/app/sense.c
    module/app/tasks.c
    
    test/comm_test.c
    test/tim_test.c
    test/aging_test.c
    SEGGER_RTT/RTT/SEGGER_RTT.c
    SEGGER_RTT/RTT/SEGGER_RTT_printf.c
)

# Add include paths
target_include_directories(${CMAKE_PROJECT_NAME} PRIVATE
    # Add user defined include paths
    Core/Inc
    module
    test
    SEGGER_RTT/RTT
    SEGGER_RTT/Config
)

# Add project symbols (macros)
target_compile_definitions(${CMAKE_PROJECT_NAME} PRIVATE
    # Add user defined symbols
)

# Remove wrong libob.a library dependency when using cpp files
list(REMOVE_ITEM CMAKE_C_IMPLICIT_LINK_LIBRARIES ob)

# Add linked libraries
target_link_libraries(${CMAKE_PROJECT_NAME}
    stm32cubemx

    # Add user defined libraries
)

# Generate hex/bin from elf (cl: 烧录通道吃 hex，openocd/pyocd 通用)
find_program(ARM_OBJCOPY arm-none-eabi-objcopy)
if(ARM_OBJCOPY)
    add_custom_command(TARGET ${CMAKE_PROJECT_NAME} POST_BUILD
        COMMAND ${ARM_OBJCOPY} -O ihex
                $<TARGET_FILE:${CMAKE_PROJECT_NAME}>
                $<TARGET_FILE_DIR:${CMAKE_PROJECT_NAME}>/${CMAKE_PROJECT_NAME}.hex
        COMMAND ${ARM_OBJCOPY} -O binary
                $<TARGET_FILE:${CMAKE_PROJECT_NAME}>
                $<TARGET_FILE_DIR:${CMAKE_PROJECT_NAME}>/${CMAKE_PROJECT_NAME}.bin
        COMMENT "Generating hex/bin")
endif()
