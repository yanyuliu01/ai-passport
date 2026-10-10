# The product's updater, compiled in unchanged, plus the test that drives it.
set(APP "${CMAKE_CURRENT_LIST_DIR}/../../../main")
idf_component_register(
    SRCS "test_main.c" "${APP}/pocket_update.c" "${APP}/pocket_update_core.c"
    INCLUDE_DIRS "." "${APP}"
    REQUIRES app_update esp_app_format esp_partition esp_timer mbedtls nvs_flash
             bootloader_support spi_flash
)
