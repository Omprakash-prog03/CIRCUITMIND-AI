cmake_minimum_required(VERSION 3.20)
project(CircuitMindAI LANGUAGES CXX)

option(CIRCUITMIND_BUILD_TESTS "Build C++ core tests" ON)
if(CIRCUITMIND_BUILD_TESTS)
    enable_testing()
endif()
add_subdirectory(core)
