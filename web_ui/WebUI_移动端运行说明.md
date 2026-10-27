# Web UI 移动端自动化

Android 与 iOS 复用已有62项用例、test/prod规则和Allure报告结构。Android驱动适配已完成本地校验，尚未连接Android真机执行；真机结果以新批次证据为准。旧 `web_ui_iphone_*.py` 保留兼容历史iPhone任务，新设备使用通用入口。

## 设备识别

读取系统、型号、版本及唯一设备标识。首次仅连接一台已授权Android时自动选择；多台时显式指定 `--udid`。成功连接后记住目标标识，目标断开时停止，不自动改测其他手机。Android与iOS使用不同的会话记录，不覆盖iPhone会话。

```bash
cd "/Users/mac/Documents/Codex/2026-10-16/wo-sh/outputs"
python3 "web_ui_mobile_session.py" devices
python3 "web_ui_mobile_session.py" connect --udid "实际Android序列号"
```

设备需连接USB、已开启USB调试并授权此电脑、安装普通Chrome且保持解锁。脚本不自动更改系统权限或VPN，不清Chrome资料，不使用无痕、模拟摄像头或预授予权限。Chromedriver需与设备Chrome主版本兼容；如Appium没有匹配版本，可用 `--chromedriver "/实际路径/chromedriver"` 指定，脚本不自动下载或全局安装。

只预览配置、不连接设备：

```bash
python3 "web_ui_mobile_session.py" connect --udid "ANDROID_SERIAL" --dry-run
```

## 独立批次与运行

每台手机、每次完整复测或更换网站使用独立批次，同一轮的后续阶段沿用同一路径。使用 `--url` 或 `web_ui_config.json` 更换网址；prod地址必须明确提供。

```bash
python3 "web_ui_mobile_run.py" inspect --platform android \
  --batch-dir "./web_ui_artifacts/android_本轮时间"
```

`inspect` 只核对当前Chrome网址、设备及client_id并保存证据，不执行匹配或支付。设备ID只读取配置列出的键，不能假定Android一定使用 `android_deviceId`；缺失或冲突时停止，禁止猜测重置ID。

完整test轮次先通过既有后台UI完成关联账号清理及设备记录重置，将证据保存在批次内。`device-preflight.json`须包含 `status=reset_complete`、`environment=test`、`website`、`device_udid`、`old_client_id`、`new_client_id`、`suffix`、关联用户数、匹配/修改数和证据路径。当前页面原client_id必须与记录一致，新ID为原值追加不重复六位字母数字，不能替换成六位。prod跳过这些动作。只有用户明确要求沿用数据补测时才传 `--mode incremental`。

按需执行已有阶段，例如：

```bash
python3 "web_ui_mobile_run.py" guest-basic --platform android \
  --batch-dir "./web_ui_artifacts/android_本轮时间"
python3 "web_ui_mobile_run.py" guest-permissions --platform android \
  --batch-dir "./web_ui_artifacts/android_本轮时间"
```

阶段入口可用 `--help` 查看。共享Suite中的详情、Say Hi、举报、拉黑等动作继续复用；该入口是分阶段执行器，不声称一次命令已完整覆盖62项。Google账号选择、Android原生权限样式和第三方收银台需拿到真机后核对实际页面。

游客按网页登录状态判定；游客匹配总计不超过8次，用例完成立即退出。关闭麦克风和摄像头不在范围内。已登录先刷新Profile判会员；订阅项在未标记项全部处理后执行；test会员到期先确认状态，再恢复沙箱订阅。prod跳过全部支付及已订阅Video。

## Android权限差异

Android可能先后出现Chrome站点请求、系统摄像头请求、系统麦克风请求。脚本仅点击已识别权限层的按钮，不把网页内普通Allow按钮当作授权按钮。已持久授权或拒绝导致本轮没有请求时记为阻塞，不能用历史授权替代拒绝测试。

授权后同时采集原生可见通知和只读权限状态。Android未显示与预期对应的通知时保留REVIEW，不套用iOS通知文案直接判通过。实际设备若有厂商差异，在此公共驱动内补充定位规则，业务用例无需复制。

## Allure

报告按实际设备写系统、型号、设备标识和自动化驱动。每项保留设计步骤、本次执行步骤、预期、实际和证据。生成时显式指定批次，发布继续使用原外部链接的逻辑：

```bash
python3 "build_full_rerun_allure.py" --batch-dir "./web_ui_artifacts/android_本轮时间"
python3 "publish_web_ui_report.py" --batch-dir "./web_ui_artifacts/android_本轮时间"
```

本地校验命令（不连接手机，不生成真机通过结论）：

```bash
python3 -m unittest "test_web_ui_mobile.py" "test_web_ui_execution_rules.py" -v
```
