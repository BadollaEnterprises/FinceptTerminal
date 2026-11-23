# md4c 0.5.2 still declares CMake 3.5 as both its minimum and policy
# version.  Current CMake warns that compatibility below 3.10 is being
# removed.  Fincept itself requires CMake 3.27, so let the pinned dependency
# keep its stated 3.5 minimum while declaring policies through 3.10 tested.
# The exact-prefix guard makes an upstream source change fail visibly instead
# of rewriting an unrelated file.

if(NOT DEFINED FILE)
    message(FATAL_ERROR "md4c_cmake_compat: FILE variable not set")
endif()
if(NOT EXISTS "${FILE}")
    message(FATAL_ERROR "md4c_cmake_compat: '${FILE}' does not exist")
endif()

set(_old "cmake_minimum_required(VERSION 3.5)")
set(_new "cmake_minimum_required(VERSION 3.5...3.10)")
file(READ "${FILE}" _content)

# The release-0.5.2 file has a leading blank line. Allow whitespace before the
# declaration, but keep rejecting any other prefix so an upstream source
# change cannot be silently rewritten.
foreach(_candidate IN ITEMS "${_new}" "${_old}")
    string(FIND "${_content}" "${_candidate}" _position)
    if(NOT _position EQUAL -1)
        string(SUBSTRING "${_content}" 0 ${_position} _leading)
        if(NOT _leading MATCHES "^[ \t\r\n]*$")
            message(FATAL_ERROR
                "md4c_cmake_compat: unexpected content before the version declaration in '${FILE}'")
        endif()
        if(_candidate STREQUAL _new)
            message(STATUS "md4c_cmake_compat: already patched")
            return()
        endif()
        string(LENGTH "${_old}" _old_length)
        math(EXPR _after "${_position} + ${_old_length}")
        string(SUBSTRING "${_content}" ${_after} -1 _remainder)
        file(WRITE "${FILE}" "${_leading}${_new}${_remainder}")
        message(STATUS "md4c_cmake_compat: declared CMake policy compatibility through 3.10")
        return()
    endif()
endforeach()

message(FATAL_ERROR
    "md4c_cmake_compat: expected '${_old}' at the start of '${FILE}'")
