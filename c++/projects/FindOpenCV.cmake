if (NOT DEFINED Find_OpenCV_CMAKE_OPTION)
  set(Find_OpenCV_CMAKE_OPTION ON)

  if(NOT DEFINED OpenCV_FOUND)
    find_package(OpenCV REQUIRED)
    message(STATUS "OpenCV version: ${OpenCV_VERSION}")
  endif()

  include_directories(
    ${OpenCV_INCLUDE_DIRS}
  )

  set(WITH_OPENCV_WORLD OFF CACHE BOOL "with opencv_world")
  if(WITH_OPENCV_WORLD)
    set(CORE_LIBS
      ${CORE_LIBS}
      opencv_world
    )
    else()
    set(CORE_LIBS
      ${CORE_LIBS}
      ${OpenCV_LIBS}
    )
  endif()

  # Register OpenCV DLLs so copy_dll_to() copies them next to each executable on Windows.
  if(WIN32)
    set(_OCV_DLL_DIR "")
    if(DEFINED OpenCV_DIR AND EXISTS "${OpenCV_DIR}/../bin")
      get_filename_component(_OCV_DLL_DIR "${OpenCV_DIR}/../bin" ABSOLUTE)
    endif()
    if(_OCV_DLL_DIR AND EXISTS "${_OCV_DLL_DIR}")
      file(GLOB _OCV_DLLS
        "${_OCV_DLL_DIR}/opencv_world*.dll"
        "${_OCV_DLL_DIR}/opencv_videoio_ffmpeg*.dll"
      )
      if(_OCV_DLLS)
        list(APPEND EXTRA_DLL_DEPS ${_OCV_DLLS})
        set(EXTRA_DLL_DEPS ${EXTRA_DLL_DEPS} CACHE INTERNAL "Extra runtime DLLs to copy next to executables")
        message(STATUS "OpenCV runtime DLLs: ${_OCV_DLLS}")
      endif()
    endif()
  endif()
endif()
