# 自动发布

GitHub 仓库：<https://github.com/lubdbdbd/rog-status-light>

发布链路：完成本地软件更新和安装 ZIP → 本机自动同步源码 → GitHub 测试 → Windows 构建 → 发布完整 Release。

本机任务 `ROGStatusLight-AutoPublish` 每 5 分钟检查一次，ZIP 完成至少 2 分钟且包内文件与当前源码一致才会同步。同步只使用固定的软件文件清单和根目录下的测试文件。网络推送失败会在下次检查时重试。

本机状态位于 `%USERPROFILE%\.rog-status-light\publishing\status.json`。`published` 表示已推送，云端是否完成以仓库 Actions 和 Releases 为准。

仓库中直接开发时，完成后将改动推送到 `main` 即会触发发布。无需手动打标签；版本未提高时自动递增补丁号。同一提交的重试沿用原版本。测试或构建失败时保留上一个已发布版本。

```powershell
# 在原开发电脑重新启用本地同步
./scripts/Install-AutoPublish.ps1 -Workspace '原软件工作区路径'

# 暂停本机自动同步
Disable-ScheduledTask -TaskName ROGStatusLight-AutoPublish

# 恢复
Enable-ScheduledTask -TaskName ROGStatusLight-AutoPublish
```

`packaging/ROGStatusLight.cer` 是公开证书，`ROGStatusLight.msix` 是固定的已签名身份包；构建与发布不需要私钥。运行记录、聊天数据库、认证信息和本机发布配置均保留在电脑上。
