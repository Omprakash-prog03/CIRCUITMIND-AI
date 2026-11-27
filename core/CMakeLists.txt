cmake_minimum_required(VERSION 3.20)
project(circuitmind_core VERSION 0.3.0 LANGUAGES CXX)

option(CIRCUITMIND_BUILD_TESTS "Build standalone native core tests" ON)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_library(circuitmind_core SHARED
    src/engine.cpp
    src/c_api.cpp
)
target_include_directories(circuitmind_core PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_compile_definitions(circuitmind_core PRIVATE CIRCUITMIND_CORE_BUILD)
set_target_properties(circuitmind_core PROPERTIES
    OUTPUT_NAME circuitmind_core
    WINDOWS_EXPORT_ALL_SYMBOLS ON
)
if(MSVC)
    target_compile_options(circuitmind_core PRIVATE /W4 /permissive-)
else()
    target_compile_options(circuitmind_core PRIVATE -Wall -Wextra -Wpedantic -Wconversion -Wshadow)
endif()

add_executable(circuitmind_engine_cli src/main.cpp)
target_link_libraries(circuitmind_engine_cli PRIVATE circuitmind_core)

if(CIRCUITMIND_BUILD_TESTS)
    enable_testing()
    add_executable(circuitmind_core_tests tests/test_engine.cpp)
    target_link_libraries(circuitmind_core_tests PRIVATE circuitmind_core)
    add_test(NAME circuitmind_core_tests COMMAND circuitmind_core_tests)
endif()
