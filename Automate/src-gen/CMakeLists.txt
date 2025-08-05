cmake_minimum_required(VERSION 3.5)
project(castfunction_variables VERSION 0.0.0 LANGUAGES CXX)

# The Test build type is the Debug type plus coverage generation
if(CMAKE_BUILD_TYPE STREQUAL "Test")
  set(CMAKE_BUILD_TYPE "Debug")

  if(CMAKE_CXX_COMPILER_ID STREQUAL "GNU")
    find_program(LCOV_BIN lcov)
    if(LCOV_BIN MATCHES "lcov$")
      set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} --coverage -fprofile-arcs -ftest-coverage -fprofile-update=atomic")
    else()
      message("Not producing code coverage information since lcov was not found")
    endif()
  else()
    message("Not producing code coverage information since the selected compiler is no gcc")
  endif()
endif()

# require C++ 20
set(CMAKE_CXX_STANDARD 20 CACHE STRING "The C++ standard is cached for visibility in external tools." FORCE)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# don't automatically build and install all targets
set(CMAKE_SKIP_INSTALL_ALL_DEPENDENCY true)

# do not print install messages
set(CMAKE_INSTALL_MESSAGE NEVER)

# Colorize compilation output
set(CMAKE_COLOR_DIAGNOSTICS ON)

include(${CMAKE_ROOT}/Modules/ExternalProject.cmake)
include(GNUInstallDirs)

set(DEFAULT_BUILD_TYPE "Debug")
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
set    (CMAKE_BUILD_TYPE "${DEFAULT_BUILD_TYPE}" CACHE STRING "Choose the type of build." FORCE)
endif()

if (APPLE)
   file(RELATIVE_PATH REL_LIB_PATH 
        "${CMAKE_INSTALL_PREFIX}/${CMAKE_INSTALL_BINDIR}"
        "${CMAKE_INSTALL_PREFIX}/${CMAKE_INSTALL_LIBDIR}"
   )
   set(CMAKE_INSTALL_RPATH "@executable_path/${REL_LIB_PATH}")
else ()
   set(CMAKE_INSTALL_RPATH "${CMAKE_INSTALL_PREFIX}/${CMAKE_INSTALL_LIBDIR}")
endif ()

set(CMAKE_BUILD_WITH_INSTALL_RPATH ON)

file(GLOB subdirs RELATIVE "${PROJECT_SOURCE_DIR}" "${PROJECT_SOURCE_DIR}/*")
foreach(subdir ${subdirs})
  if(IS_DIRECTORY "${PROJECT_SOURCE_DIR}/${subdir}")
    if(${subdir} MATCHES "reactor-cpp-.*")
      string(SUBSTRING ${subdir} 12 -1 LF_REACTOR_CPP_SUFFIX)
      add_subdirectory("${subdir}")
    endif()
  endif()
endforeach()
foreach(subdir ${subdirs})
  if(IS_DIRECTORY "${PROJECT_SOURCE_DIR}/${subdir}")
    if(EXISTS "${PROJECT_SOURCE_DIR}/${subdir}/.lf-cpp-marker")
      if(NOT ${subdir} MATCHES "reactor-cpp-.*")
        add_subdirectory("${subdir}")
      endif()
    endif()
  endif()
endforeach()