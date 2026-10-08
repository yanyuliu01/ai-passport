// main/pocket_view.h —— 界面用到的纯逻辑：显示哪个视图、首页状态词、数字与时间格式化。
// 不依赖 ESP-IDF / LVGL，可在主机上直接测试。
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "buddy_types.h"
#include "pocket_pet.h"

// 当前应该占据屏幕的视图。数值越小优先级越高：需要用户处理的事压过普通页面。
typedef enum {
    POCKET_VIEW_CONFIRM,   // 取消配对 / 恢复出厂 的二次确认
    POCKET_VIEW_PAIRING,   // 配对码或“正在建立安全连接”
    POCKET_VIEW_VOICE,     // 按住确认键说话
    POCKET_VIEW_APPROVAL,  // Claude 请求权限
    POCKET_VIEW_PAGE,      // 普通页面（由 snapshot->page 决定）
} pocket_view_t;

// 首页中央显示的状态。
typedef enum {
    POCKET_HOME_BLE_OFF,    // 蓝牙被用户关闭
    POCKET_HOME_WAITING,    // 蓝牙开着，但没有电脑连上来
    POCKET_HOME_LINKING,    // 已连上，还没收到 Claude 的状态
    POCKET_HOME_IDLE,       // 已连接，没有任务在跑
    POCKET_HOME_BUSY,       // 有会话正在生成
    POCKET_HOME_PENDING,    // 有会话在等权限确认，但请求不在本机上
    POCKET_HOME_APPROVED,   // 刚在本机允许了一次
    POCKET_HOME_MILESTONE,  // token 里程碑
} pocket_home_t;

pocket_view_t pocket_view_for(const buddy_ui_snapshot_t *snapshot);
pocket_home_t pocket_home_for(const buddy_ui_snapshot_t *snapshot);
// 小幽此刻的表情：跟随当前视图（审批、配对、确认）或首页状态。
pocket_pet_mood_t pocket_pet_for(const buddy_ui_snapshot_t *snapshot);

// 以下格式化函数都保证 out 以 NUL 结尾（size 为 0 时除外），返回写入的字节数。
// token 数：小于一万用千分位，之后用“万”“亿”，保留一位小数。
size_t pocket_format_tokens(uint64_t tokens, char *out, size_t size);
// 时钟 “HH:MM”。电脑没同步过时间（epoch<=0）时返回 0 并写入空串。
size_t pocket_format_clock(int64_t epoch_seconds, int32_t timezone_offset_seconds,
                           uint64_t time_received_ms, uint64_t now_ms,
                           char *out, size_t size);
// 运行时长：“不到1分钟” / “12分钟” / “3小时05分” / “2天4小时”。
size_t pocket_format_uptime(uint64_t uptime_ms, char *out, size_t size);
// 配对码 “123 456”；超过六位的值按六位截取低位。
size_t pocket_format_passkey(uint32_t passkey, char *out, size_t size);
