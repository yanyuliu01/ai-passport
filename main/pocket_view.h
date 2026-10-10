// main/pocket_view.h —— 界面用到的纯逻辑：显示哪个视图、第一屏此刻是什么情形、
// 一件事里怎么翻、帮手用什么颜色、数字与时间格式化。
// 不依赖 ESP-IDF / LVGL，可在主机上直接测试。
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "buddy_types.h"
#include "pocket_pet.h"

// 当前应该占据屏幕的视图。数值越小优先级越高：需要用户处理的事压过普通页面。
typedef enum {
    POCKET_VIEW_UPDATE,    // 正在经蓝牙换固件：这时别的都让路，按键也不管用
    POCKET_VIEW_CONFIRM,   // 取消配对 / 恢复出厂 的二次确认
    POCKET_VIEW_PAIRING,   // 配对码或“正在建立安全连接”
    POCKET_VIEW_VOICE,     // 按住确认键说话
    POCKET_VIEW_APPROVAL,  // 有件事要你点头
    POCKET_VIEW_PAGE,      // 普通页面（由 snapshot->page 决定）
} pocket_view_t;

// 第一屏（形象）此刻的情形。后面五种是有一轮对话正在进行或刚说完；前四种没有。
typedef enum {
    POCKET_HOME_BLE_OFF,   // 蓝牙被用户关闭
    POCKET_HOME_WAITING,   // 蓝牙开着，但没有人连上来
    POCKET_HOME_LINKING,   // 已连上，还没收到对面的状态
    POCKET_HOME_QUIET,     // 连着，但还没聊过
    POCKET_HOME_SENT,      // 录音发出去了，还不知道听成了什么
    POCKET_HOME_THINKING,  // 她在想
    POCKET_HOME_HELPER,    // 她把活交给了别的代理
    POCKET_HOME_ANSWERED,  // 她答完了
    POCKET_HOME_FAILED,    // 这一轮没成
} pocket_home_t;

pocket_view_t pocket_view_for(const buddy_ui_snapshot_t *snapshot);
pocket_home_t pocket_home_for(const buddy_ui_snapshot_t *snapshot);
// 这种情形下是不是有一轮对话在屏幕上（正在进行或刚说完）。
bool pocket_home_is_talk(pocket_home_t home);

// 第二屏（对话）一屏只放一件事。事里的话比窗口长时在里面翻：content 是这件事的
// 总高，view 是窗口的高，step 是一次翻多少，单位都是像素。返回翻完之后窗口在哪，
// 不会翻出这件事。已经在头上还往那个方向按，*edge 记下方向（-1 上、1 下），意思是
// “该换到上一件 / 下一件了”；否则是 0。direction 为 0 只是把 y 收回合法范围。
int pocket_card_scroll(int y, int direction, int content, int view, int step, int *edge);

// 第三屏（任务）里一件事已经做了多少秒：中枢说的秒数加上从那以后过去的时间。
uint32_t pocket_task_seconds(uint32_t reported, uint64_t since_ms, uint64_t now_ms);

// 第三屏的单子在屏幕上是一行一行的：一件事一行；做完的事按天分组，每换一天
// 前面多一行写着哪天的小标题。item 是这一行（小标题则是它下面那一行）在单子里
// 的位置，先数还在做的（tasks），再数做完的（past）。
typedef struct {
    bool header;
    uint8_t item;
} pocket_task_line_t;
#define POCKET_TASK_LINES_MAX (BUDDY_TASK_COUNT + 2 * BUDDY_PAST_COUNT)
// 把单子排成行，写进 lines（最多 max 行），返回行数。
unsigned pocket_task_lines(const buddy_ui_snapshot_t *snapshot, pocket_task_line_t *lines,
                           unsigned max);
// 窗口只有 window 像素高，放不下整张单子：返回该从第几行画起，选中的那件事才整行
// 都在窗口里。top 是窗口原来从第几行画起，能不动就不动；选中的是它那一天的头一件
// 时，把那天的小标题也带上。row 和 header 是一件事、一个小标题各占多高。
unsigned pocket_task_window(const pocket_task_line_t *lines, unsigned count,
                            unsigned selected, unsigned top, int row, int header,
                            int window);

// 一共几屏轮着切：中枢发过用量就是四屏（多一屏用量），否则三屏。
int pocket_screen_count(const buddy_ui_snapshot_t *snapshot);
// 现在在第几屏（0 起）；不在这几屏上（菜单之类）是 -1。从第三屏进到一件任务里时
// 仍算第三屏。
int pocket_screen_index(const buddy_ui_snapshot_t *snapshot);
// 设备现在的时间（Unix 秒）：对面同步过来的时间加上从那以后过去的。没同步过是 0。
int64_t pocket_now_epoch(const buddy_ui_snapshot_t *snapshot);
// 第四屏（用量）里一个窗口什么时候重置：out 里写当地的 “HH:MM”。返回星期几
// （1 到 7，周一是 1）；就在今天、或者不知道今天是哪天（now_epoch 为 0）时返回 0。
// at 为 0（不知道）或者已经过了时返回 -1，写入空串。
int pocket_format_reset(uint32_t at, int32_t timezone_offset_seconds, int64_t now_epoch,
                        char *out, size_t size);
// 用量里的数是多少分钟之前的：中枢说的秒数加上从那以后过去的时间。
uint32_t pocket_usage_minutes(uint32_t reported_seconds, uint64_t since_ms, uint64_t now_ms);
// 进度条 cells 格里亮几格：剩得越多亮得越多；还剩一点就至少亮一格，一点不剩才全灭。
// left 不在 0 到 100 之间（不知道）时返回 -1。
int pocket_usage_cells(int left, int cells);

// 换固件这件事现在要不要占着屏幕：正在收、正在核对、马上重启。
// 收到一半连接断了（等手机回来）时不占，照常显示别的。
bool pocket_update_shown(pocket_update_phase_t phase);
// 小幽此刻的表情：跟随当前视图（换固件、审批、配对、确认、说话）或第一屏的情形。
pocket_pet_mood_t pocket_pet_for(const buddy_ui_snapshot_t *snapshot);
// message 里那行字现在要不要显示。连的是手机中枢时它只是一条提示，出现几秒就收起；
// 连的是 Claude 桌面端时它是对面的状态行，一直显示。
bool pocket_notice_visible(const buddy_ui_snapshot_t *snapshot);

// 颜色只回答一个问题：现在是谁在做事。小幽自己一个颜色，每个帮手一个颜色。
// 认识的名字有固定的颜色，其余按名字算出来，同一个名字永远同一个颜色。
// 名字不分大小写；空名字返回小幽自己的颜色。返回 0xRRGGBB。
#define POCKET_COLOR_XIAOYOU 0xB9A7FFu
uint32_t pocket_helper_color(const char *name);

// 以下格式化函数都保证 out 以 NUL 结尾（size 为 0 时除外），返回写入的字节数。
// 时钟 “HH:MM”。对面没同步过时间（epoch<=0）时返回 0 并写入空串。
size_t pocket_format_clock(int64_t epoch_seconds, int32_t timezone_offset_seconds,
                           uint64_t time_received_ms, uint64_t now_ms,
                           char *out, size_t size);
// 经过的时间 “M:SS”，超过 99 分钟显示 “99:59”。since 在 now 之后时按 0 算。
size_t pocket_format_elapsed(uint64_t since_ms, uint64_t now_ms, char *out, size_t size);
// 配对码 “123 456”；超过六位的值按六位截取低位。
size_t pocket_format_passkey(uint32_t passkey, char *out, size_t size);
