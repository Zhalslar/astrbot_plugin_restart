# 更新日志

## v1.2.0

新特性：
- 为 QQ 官方 bot 平台（包括 webhook 适配器会话）提供重启完成通知支持。

错误修复：
- 当元数据缺失时，确保插件重载成功消息能回退到合理的显示名称。

增强改进：
- 引入基于 `ConfigNode` 的 `PluginConfig` 和 `RestartCache`，用于管理插件设置并更清晰地持久化重启状态。
- 简化 `RestartScheduler`，使其直接接受 cron 字符串，而不是整个 `AstrBotConfig`。
- 优化 `DashboardClient`，使其依赖本地生成的 Dashboard JWT 进行认证，而非用户名/密码登录。
- 新增重启开始、重启完成消息自定义，支持用 `{elapsed}` 和 `{memory}` 自定义重启完成的消息内容。

日常维护：
- 将内部模块迁移到 `core` 包下。

## v1.1.0

新功能：

- 在本地生成 JWT 用于仪表盘 API 的认证，而不是通过 HTTP 登录获取。

优化改进：

- 改进插件重载命令的匹配方式，支持对显示名称和内部名称的部分匹配。
