idf_component_register(
    SRCS "main.c"
         "pocket_ui.c"
         "pocket_view.c"
         "pocket_pet.c"
         "buddy_orchestrator.c"
         "buddy_line.c"
         "buddy_ble.c"
         "buddy_ble_store.c"
         "buddy_ble_lifecycle.c"
         "buddy_app_logic.c"
         "buddy_protocol.c"
         "buddy_state.c"
         "buddy_settings.c"
         "../assets/fonts/pocket_font_14.c"
         "../assets/fonts/pocket_font_16.c"
         "../assets/fonts/pocket_font_22.c"
         "../assets/fonts/pocket_font_num_44.c"
    INCLUDE_DIRS "."
    REQUIRES bsp bt esp_hw_support esp_timer json nvs_flash
)

# ESP-IDF 5.5.3 only applies WRITE_ENC to generated CCCDs. Wrap the private
# registration boundary so protected CCCDs also receive READ_ENC.
target_link_libraries(${COMPONENT_LIB} INTERFACE "-Wl,--wrap=ble_att_svr_register")
target_link_libraries(${COMPONENT_LIB} INTERFACE "-Wl,-u,__wrap_ble_att_svr_register")
