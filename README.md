# ROG 状态灯

[![Windows build](https://github.com/lubdbdbd/rog-status-light/actions/workflows/release.yml/badge.svg)](https://github.com/lubdbdbd/rog-status-light/actions/workflows/release.yml)
[下载最新版](https://github.com/lubdbdbd/rog-status-light/releases/latest)

让 ROG 魔导士 RX Low Profile 随 ChatGPT Work / Codex 的任务状态切换灯效，关闭窗口后继续驻留托盘。

| 状态 | 灯效 |
| --- | --- |
| 空闲 | 原色常亮 |
| 正常工作 | Esc → Ins 数字行播放带呼吸起伏和渐变拖尾的海浪，其余按键常亮 |
| 等待回答、确认或额度不足 | 原色呼吸提醒 |
| 任务完成或新通知 | 短暂呼吸后恢复 |

USB、原装 Omni 2.4GHz 和蓝牙均已在本机键盘实测。蓝牙海浪使用快速发送和按变化优先更新，提醒使用键盘内置呼吸。暂停或退出会恢复原色常亮。

## 安装

1. 在 [Releases](https://github.com/lubdbdbd/rog-status-light/releases/latest) 下载 `ROGStatusLight-*-Windows-x64.zip`，完整解压。
2. 运行“安装.cmd”，完成 Windows 权限确认。
3. 打开“ROG 状态灯”，点击“自动联动”。程序会在登录后启动，关闭窗口后继续在托盘运行。

系统要求：Windows 11 x64（22H2 起）。此版本的灯位映射适用于 ROG Falchion RX Low Profile / 魔导士 RX Low Profile（M603）。

USB / 2.4GHz 需要开启 Windows“动态光效”，并在**实际键盘或接收器设备卡片**自己的“背景光控件”中，将“ROG 状态灯”排在第一位。接收器换 USB 口后需检查该设备的排序。

蓝牙无需 Windows 动态光效。安装包中的蓝牙预设保留本机实测绿色；首次使用可以先点击“蓝牙测试”或“完整测试”。蓝牙专项测试会暂停自动联动，结束后点击“自动联动”继续。

## 使用

- “暂停联动”：恢复原色常亮。
- “取消提醒”：清除已记录的额度提示和短暂通知。
- “完整测试”：常亮、海浪、呼吸各 8 秒，便于观察。
- “退出”：从托盘退出并恢复常亮。
- “卸载.cmd”：移除软件、自启入口、灯控注册和本软件添加的事件配置。

配置和运行记录保存在 `%USERPROFILE%\.rog-status-light`。任务状态来自本机任务数据库与事件；软件不会上传聊天内容，蓝牙控制只订阅灯光命令回复。

## 构建与自动发布

```powershell
py -3.12 -m pip install -r requirements-build.txt
py -3.12 -m unittest discover -s tests -p 'test_*.py' -v
py -3.12 scripts/build_release.py
```

产物位于 `release/`，包含 Windows 安装 ZIP 和 SHA-256 校验文件。构建使用 `packaging/` 中已签名的固定外部位置身份包，后续更新只替换外部程序，保持 Windows 灯控身份连续。

推送软件改动到 `main` 后，GitHub Actions 自动运行测试、构建安装包并发布 Release。首次使用源码声明的版本号；没有手动改版本时，后续发布自动递增补丁号。重试同一提交沿用原版本，不重复发布。发布先创建草稿，完整上传安装包和校验值后再公开。

本机自动同步以**安装 ZIP 更新完成且源码与包内源码一致**为触发条件，避免正在编辑的文件被提前发布。维护说明见 [docs/publishing.md](docs/publishing.md)。
