# Host-side renderer for the Claude Pocket UI: draws every view into PPM images
# with the real LVGL sources and fonts, and dumps each font's glyph coverage.
# Development aid only; not part of the firmware or of tools/validate.sh.
#
#   cmake -S tools/ui_preview -B /tmp/ui_preview -DLVGL_DIR=<lvgl 9.5.0 checkout>
#   cmake --build /tmp/ui_preview && /tmp/ui_preview/ui_preview <output-dir>
#
# LVGL_DIR defaults to the managed component fetched by an ESP-IDF build.
cmake_minimum_required(VERSION 3.16)
project(pocket_ui_preview C)

set(REPO "${CMAKE_CURRENT_LIST_DIR}/../..")
set(LVGL_DIR "${REPO}/managed_components/lvgl__lvgl" CACHE PATH "LVGL 9.5.0 sources")
if(NOT EXISTS "${LVGL_DIR}/lvgl.h")
    message(FATAL_ERROR "LVGL sources not found at ${LVGL_DIR}; pass -DLVGL_DIR=...")
endif()

file(GLOB_RECURSE LVGL_SOURCES "${LVGL_DIR}/src/*.c")
add_library(lvgl STATIC ${LVGL_SOURCES})
target_include_directories(lvgl PUBLIC "${LVGL_DIR}" "${CMAKE_CURRENT_LIST_DIR}")
target_compile_definitions(lvgl PUBLIC LV_CONF_INCLUDE_SIMPLE)

add_executable(ui_preview
    preview_main.c
    "${REPO}/main/pocket_ui.c"
    "${REPO}/main/pocket_view.c"
    "${REPO}/main/pocket_pet.c"
    "${REPO}/assets/fonts/pocket_font_14.c"
    "${REPO}/assets/fonts/pocket_font_16.c"
    "${REPO}/assets/fonts/pocket_font_22.c"
    "${REPO}/assets/fonts/pocket_font_num_44.c")
target_include_directories(ui_preview PRIVATE "${REPO}/main")
target_compile_options(ui_preview PRIVATE -Wall -Wextra)
target_link_libraries(ui_preview PRIVATE lvgl m)
