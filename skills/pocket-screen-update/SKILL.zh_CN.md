---
name: pocket-screen-update
description: 把主人对随身设备屏幕说的一句修改意见变成推到设备上的新固件，或者把设备换回以前的某一版。用于小幽转交过来的“界面不好看、想改成什么样、换回上一版”这类请求；经蓝牙更新，不插线烧录。
---

<p align="right"><strong>简体中文</strong> · <a href="SKILL.md">English</a></p>

# 改屏幕，推到设备上

主人多半不在键盘前：这件事是他对着设备说了一句话，小幽转交过来的。所以不反复
追问，按最小、最合理的理解去做，做完用两三句话说清楚改了什么、怎么撤销。

开始前在仓库根目录执行 `git status --short --branch`，阅读 `AGENTS.zh_CN.md` 和
`docs/claude-pocket.zh_CN.md` 的“经蓝牙换固件”一节。下面的 Runtime 命令都在
`runtime/` 目录里运行，配置文件默认是那里的 `config.json`。

## 只是换回某一版

主人说“换回上一版”“恢复第 5 版”“刚才那个不好看，退回去”时不用改代码：

```bash
python3 -m xiaoyou_runtime firmware list              # 有哪些版本，● 是设备现在跑的
python3 -m xiaoyou_runtime firmware restore previous  # 上一版；也可以写序号，例如 5
```

上一版还在设备的另一个槽位里时，设备几秒钟就切回去；更早的版本要重新传，一两分钟。

## 改界面

1. 读要改的地方。布局、颜色、间距在 `main/pocket_ui.c`；文案在 `main/pocket_text.h`；
   “现在显示哪个画面”在 `main/pocket_view.c`；小幽的造型在 `main/pocket_pet.c`；按键
   之后发生什么在 `main/buddy_state.c`。只改和这句话有关的部分。
2. 动手改。界面上的中文只写在 `main/pocket_text.h` 里；改了它就运行
   `python3 tools/gen_pocket_fonts.py` 重新生成字库。
3. 不要碰这些（这次改坏了，下一次修正就推不上去）：`main/pocket_update.c`、
   `main/pocket_update_core.*`、`main/buddy_ble.c` 里收数据的部分、`partitions.csv`、
   `sdkconfig.defaults` 里“经蓝牙换固件”那一段。换固件时的进度画面保留原样能用。
   确实要动这些，就停下来告诉主人这一次需要他在电脑前看着。
4. 运行 `./tools/validate.sh --static`。不过就修，修不好就照实说，不要推一个没过
   检查的版本。
5. 提交到当前的 feature 分支并推送（提交信息按 `docs/contribution/commit-and-pr.zh_CN.md`）。
6. 让新固件到设备上去，二选一：
   - 这台电脑没有 ESP-IDF（常见）：GitHub 会构建刚推上去的提交。让 Runtime 去等、
     去取、去推，这条命令马上返回：

     ```bash
     python3 -m xiaoyou_runtime firmware fetch --commit HEAD --wait 900 --push --detach \
         --note "一句话说明这一版改了什么"
     ```

   - 配置里有 `firmware.build_command`：`python3 -m xiaoyou_runtime firmware build --push --note "…"`。
7. 回话：改了什么；大约几分钟后设备会显示“在换新样子”，换好自己重启；不满意就说
   “换回上一版”。没有在真机上看过效果，这一点要说。

## 之后会发生什么

Runtime 把这一版记为“要推给设备的”，手机 App 连着设备时取走镜像、经蓝牙写进去。
设备核对通过后重启；手机连上去认可新固件，三分钟内没等到认可它就自己退回上一版。
构建失败、或者连着两次没装上，Runtime 会记下来，手机 App 的“设备固件”那一行看得到。
用 `python3 -m xiaoyou_runtime firmware status` 可以随时看进展。

每一版都留在 Runtime 的 `state/firmware/` 里，不会自动清理。

## 不做的事

不插线烧录，不改分区表，不用 `--force` 推“装上后只能插线换”的旧版本，不删除仓库里
的版本。这些都需要主人在场。
