# 独立源码实验构建维护说明

- 本分支为 `cloud-build-lg6851f-pro`，属于 `adian2020/LG6851F-Pro`；不是另一个仓库的 `cloud-build`，不要覆盖 main 或干扰另一个仓库正在运行的编译。
- 先阅读 README.md、docs/06-源码与编译.md、docs/releases/V2.10-source.md 和 docs/独立源码云编译.md。V2.10 精确重打包依赖已删除 Release 的 release-runtime，本分支从现有源码重编，不能冒称原版复现。
- 提交标题、修改摘要和发布说明使用简体中文，更新 RELEASE_NOTES.md。
- 保持固定 feeds、6.18.44、厂商源码修复、A/B 槽门禁及签名校验。旧内核模块和旧 APK 数据库不得覆盖新构建结果；不能跳过指令、ABI/来源或槽位检查来制造成功产物。
- 首轮和后续报错保留完整日志、当前配置及诊断；源码重编不等于实机冷启动或跨设备验收通过，不直接刷写或重启实机。
- 本仓库原有两个 hwifi Python 文件因 CRLF 与 gitattributes 不一致，在新克隆中就显示修改。除非任务确需修改，不把这种无关状态混入提交。
