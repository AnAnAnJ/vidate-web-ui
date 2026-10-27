# Web UI 自动化测试

支持 PC Chrome、Android Chrome、iOS Chrome，使用共享用例目录并按平台执行权限、页面交互及会员状态检查。Allure 报告记录执行端、test/prod 环境、被测地址、步骤、预期、实际结果和截图。

源码位于 `web_ui/`。被测地址及平台规则由 `web_ui/web_ui_config.json` 配置，执行时可通过 `--url` 覆盖。默认沿用当前测试站点；prod 地址必须明确指定。

## 离线检查

以下检查不连接手机、不启动浏览器、不调用被测站点：

```bash
cd "web_ui"
python3 -m unittest test_web_ui_mobile test_web_ui_execution_rules test_web_ui_reliability test_web_ui_call_billing
node --test "test_web_ui_desktop.mjs" "test_web_ui_observation.mjs"
python3 "web_ui_case_index.py" --case M-13
node "web_ui_standalone/web_ui_run.mjs" --list
```

## PC Chrome

安装当前项目的 Node.js 依赖后，使用用例编号执行。实际测试需要本机 Chrome 和可用网络：

```bash
cd "web_ui/web_ui_standalone"
npm ci
node "web_ui_run.mjs" --plan --cases G-03,G-04,G-05
node "web_ui_run.mjs" --cases G-03,G-04,G-05 --batch-dir "../web_ui_artifacts/pc_run" --mode incremental
```

完整 test 回归使用 `--mode full`，须先按运行规则完成关联测试账号清理和设备记录重置，并将前置证据保存在独立批次。`incremental` 仅用于明确沿用当前数据的补测。

## Android / iOS

移动端使用现有 Appium 会话。需预先准备 Appium、对应平台驱动、设备调试或开发者授权；Android Chrome 还需匹配版本的 Chromedriver。源码不包含本机签名、设备会话或账号凭据。

```bash
cd "web_ui"
python3 "web_ui_mobile_session.py" devices
python3 "web_ui_mobile_run.py" --help
```

Android 和 iOS 分别指定 `--platform`、`--session-file`、`--batch-dir`，保存独立结果。会话文件放在仓库根目录的 `work/`，由本机设备连接阶段生成。

## Allure 报告

使用本轮独立批次生成报告：

```bash
cd "web_ui"
python3 "build_full_rerun_allure.py" --batch-dir "./web_ui_artifacts/本轮批次"
```

生成需要本机 Allure CLI / Java；截图预览使用可选的 Pillow。现有报告发布入口为 `publish_web_ui_report.py`，依赖本机已配置的报告服务和外部代理，目标可通过 `--destination`、`--external-url` 指定。

运行记录、浏览器资料、截图和生成报告由本地批次保存，不属于源码。协议复用要求本机保留可验证的来源证据；新环境缺少证据时不能将历史协议结果记为通过。

详细用例及平台差异见 `web_ui/WebUI_用例索引.md`、`web_ui/WebUI_桌面运行说明.md`、`web_ui/WebUI_移动端运行说明.md` 和 `web_ui/WebUI运行规则.md`。早期文档中的绝对路径为原执行机器示例，在本仓库使用 `web_ui/` 目录。
