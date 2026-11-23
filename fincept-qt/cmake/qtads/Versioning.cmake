# QtADS includes a Versioning module from its nested src/CMakeLists.txt even
# when the parent has supplied ADS_VERSION.  Upstream's module ignores that
# override and runs `git describe` in CMAKE_SOURCE_DIR, which is Fincept's
# repository when QtADS is fetched as a dependency.  A tagless source archive
# then prints a false fatal diagnostic, and Windows shared builds can receive
# empty resource-version fields.
#
# Keep the dependency version deterministic: the QtADS commit and ADS_VERSION
# are pinned together in the top-level CMakeLists.txt.  This module is placed
# before QtADS' own module path only while QtADS is configured.

if(NOT ADS_VERSION MATCHES "^([0-9]+)\\.([0-9]+)\\.([0-9]+)$")
    message(FATAL_ERROR "ADS_VERSION must be a pinned major.minor.patch value; got '${ADS_VERSION}'")
endif()

set(PROJECT_VERSION_MAJOR "${CMAKE_MATCH_1}")
set(PROJECT_VERSION_MINOR "${CMAKE_MATCH_2}")
set(PROJECT_VERSION_PATCH "${CMAKE_MATCH_3}")
set(PROJECT_VERSION_STRING
    "${PROJECT_VERSION_MAJOR}.${PROJECT_VERSION_MINOR}.${PROJECT_VERSION_PATCH}")
set(PROJECT_GIT_HASH "")
set(PROJECT_GIT_HASH_SHORT "")

# Match the variables exported by QtADS' module for callers outside src/.
set(PROJECT_VERSION_MAJOR "${PROJECT_VERSION_MAJOR}" PARENT_SCOPE)
set(PROJECT_VERSION_MINOR "${PROJECT_VERSION_MINOR}" PARENT_SCOPE)
set(PROJECT_VERSION_PATCH "${PROJECT_VERSION_PATCH}" PARENT_SCOPE)
set(PROJECT_VERSION_STRING "${PROJECT_VERSION_STRING}" PARENT_SCOPE)
set(PROJECT_GIT_HASH "" PARENT_SCOPE)
set(PROJECT_GIT_HASH_SHORT "" PARENT_SCOPE)
set(PROJECT_AUTO_VERSION "${PROJECT_VERSION_STRING}" PARENT_SCOPE)

# QtADS only calls this for a shared Windows build.  Fincept currently builds
# QtADS statically, but retaining the function keeps the override safe for
# downstream packaging variants.
function(add_windows_version_resources target)
    if(NOT WIN32)
        return()
    endif()
    if(NOT TARGET "${target}")
        message(FATAL_ERROR "add_windows_version_resources: target '${target}' not found")
    endif()

    set(_rc_in "${QtADS_SOURCE_DIR}/cmake/modules/FileVersionInfo.rc.in")
    if(NOT EXISTS "${_rc_in}")
        message(FATAL_ERROR "QtADS FileVersionInfo.rc.in missing at '${_rc_in}'")
    endif()
    set(_rc_out "${CMAKE_CURRENT_BINARY_DIR}/${target}_version.rc")
    configure_file("${_rc_in}" "${_rc_out}" @ONLY)
    target_sources(${target} PRIVATE "${_rc_out}")
endfunction()
