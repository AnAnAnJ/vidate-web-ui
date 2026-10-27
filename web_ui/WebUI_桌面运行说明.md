# Chrome 桌面 Web UI

桌面使用 `web_ui_desktop_suite.mjs`，可由 CUA 绑定用户当前普通 Chrome，也可通过独立 Playwright 入口运行；iOS / Android 继续使用原 Appium 入口。S-10在所有端注释停用，备注：免费一分钟停用。手机有效目录63项，PC另按配置移除L-14、N-01、L-20、L-11、G-11、L-05后为57项，结果按平台和批次隔离。

网址从 `web_ui_config.json` 读取，也可用 `createDesktopSuite({url: ...})` 覆盖。prod 未配置地址时拒绝执行，不推测线上域名。更换网站须新建批次。

## 在 CUA 会话执行

先按 CUA 文档选择用户 Chrome 并绑定被测标签。传入的 `tab` / `browser` 必须来自该会话，不能用手机驱动或另起浏览器替代。

```js
const {createDesktopSuite,ChromePermissions}=await import('/Users/mac/Documents/Codex/2026-10-16/wo-sh/outputs/web_ui_desktop_suite.mjs');
const suite=await createDesktopSuite({
  tab:webTab,browser:chrome,
  batch:'/绝对路径/独立Web批次',
  environment:'test',
  permissions:new ChromePermissions(chromeApp)
});
await suite.execute('L-03');
```

执行前先按环境规则完成设备清理/追加后缀，保存 `preconditions.json`。桌面网页即使使用 `ios_deviceId` 存储键，平台仍记 `web`。已开始批次可以恢复，用户要求沿用数据时禁止重复重置。

按“游客→登录与未订阅→已订阅”顺序执行。`M-12` / `N-07` 连续执行启动与Exit，自动记录连接页结果；`M-13` / `N-08` 在同次通话完成英文问候和小窗交换，最后离开Match停止续配。长阶段设置 CUA 调用时间上限，并保留方法内有界观察；不能让整个调用超时后停在自动匹配页。

Google 登录需跟踪独立窗口。选择账号后继续读取该窗口，在必要的确认步骤完成后等待被测页真实回调；关闭窗口不代表成功。新老用户观察必须在点击Google前启动，错过30秒窗口记未知，不追溯当前历史消息。验证码或新协议确认由用户处理。

来电从真实回调开始与Chat观察同时进行，兼容Later/Join和Cancel/Answer，先保存来电弹窗再立即接听。PC的Official消息或本次欢迎来电均为新用户正证据；L-04接听首次来电并确认视频加载；PC移除L-05，不再等待第二通，不能等待身份判断。L-06仅老用户执行。连续观察使用可恢复的20秒片段，复用原登录时间，不在每段重新计时。独立运行器持续调用，能够在用户处理Google弹窗期间保持进程等待。

S-09核对普通通话按分钟向上取整扣80金币/分钟。S-10免费一分钟用例在所有端停用，不再执行免费计费用例。拨号等待不算通话时长；必须有双视频及唯一通话计时证据。PC的S-05余额不超过300且为20的整数倍时，用一轮匹配内的有价礼物有限消费准备0；其他余额按真实普通通话消费。每次准备后刷新Profile核对，不后台改金币。各批次是否取得实测证据以该批Allure为准。

Profile 等真实昵称加载后再读会员与余额，不能把初始 `Upgrade to Premium / 0` 占位数据当结果。首次沙箱订阅保持未订阅前置；每批每类支付最多一笔，订单检查点持久化，补证据不重付。沙箱先选Credit Card再点击购买，确认“测试模式／不会处理真实付款”，快速填充后取消保存测试卡信息。完成链接触发回站时立即观察成功提示。

会员到期后仍用当前用户接口 `response.data.uuid`，在test后台恢复864000秒；若后台询问是否增加金币，取消额外加币。刷新Profile确认有效期及原余额。prod禁止恢复和支付。

L-25（以及同一控件的L-26）每次点击前监听标签保存的XHR/fetch写请求，等待响应完整接收及HTTP成功，再核对高亮和n/6；请求完成后等待1.5秒再进行下一次点击。完成后刷新Profile核对持久化，逐次请求路径、状态和点击时刻附于报告。请求不明确时停止，不用界面乐观更新冒充已保存。

S-04在首次Video前清理当前被测源的LocalStorage业务状态、SessionStorage、CacheStorage和Service Worker，保留实际设备键以及认证Cookie/IndexedDB；身份复核的刷新仅对本标签绕过HTTP缓存。清理前后监听实际用户信息接口主对象data.uuid，并刷新Profile确认同一账号和有效会员。如果清理导致退出登录，先登录原账号，再沿用本批清理检查点复核，不重复清理、不提前点击Video。首次点击一旦消耗，不靠重复清缓存冒充首次结果。

独立Playwright入口自动绑定请求/缓存适配。CUA可通过`createDesktopSuite({site: ...})`注入等效适配，接口为`profileSave(action)`、`identity({origin,detailsPath})`和`clearSiteCache({origin,deviceKeys})`，必须使用CUA可用的原生DevTools操作取得真实证据；没有适配时L-25/S-04明确阻塞。当前未声称CUA原生缓存适配已经完成实测。用户信息接口路径后缀由`platform_rules.web.account_details_path`配置。

## 本轮已确认的桌面差异

| 项目 | 桌面实际行为 | 当前判定规则 |
| --- | --- | --- |
| 游客个人入口 | 顶栏Log in；登录后右上角头像进入Profile | 记录入口差异并校验登录弹窗 |
| 拒绝权限 | Important Tips → How Can I Provide Permission? → Next → Enjoy | PC已移除N-01；N-02点击Enjoy完成引导，不要求Try Again；手机预期保留 |
| 更多菜单 | 拉黑、举报，没有Cancel | PC已移除L-14，Android/iOS保留 |
| 详情多图 | 并排照片网格 | PC已移除L-20，Android/iOS保留轮播检查 |
| Discover打码标签 | 卡片无入口，详情有锁定标签 | PC已移除L-11，详情L-13继续保留 |
| 免费次数 | Exit后异步更新 | 等待真实扣次数值，不能读取瞬时旧值 |

每批单独保存Discover数据、拨号与礼物证据。礼物成功须有会话区“You have sent a”和列表外礼物图片，余额变动另行核对；单凭礼物面板图片不算成功。G-12使用Chrome完整站点设置页恢复允许，再返回被测页采集重载通知。

## 离线验证与报告

```bash
node --test "test_web_ui_desktop.mjs" "test_web_ui_observation.mjs"
python3 -m unittest test_web_ui_mobile test_web_ui_execution_rules test_web_ui_reliability
python3 build_full_rerun_allure.py --batch-dir "/绝对路径/独立Web批次"
python3 publish_web_ui_report.py --batch-dir "/绝对路径/独立Web批次"
```

离线回归只检查脚本规则，不能代替浏览器实测。Allure每项保留设计步骤、本次操作、预期、实际及截图；复用协议注明原来源。外部报告沿用已有 `/allure/` 发布方式。

## 不通过Codex独立执行

“独立执行”是脱离Codex运行，实际网站测试仍需要网络。脚本通过可访问角色、按钮名称和CSS找到控件，Android/iOS原生权限与触屏位置由各自驱动计算；页面结构改变时保存失败证据，不猜坐标继续点击。

在`web_ui_standalone`目录执行：

```bash
npm install
node web_ui_run.mjs --list
node web_ui_run.mjs --plan --cases G-03,G-04,G-05
node web_ui_run.mjs --cases G-03,G-04,G-05 --batch-dir "/独立批次目录" --mode incremental
```

网址默认取通用配置，可通过`--url`覆盖。独立入口使用系统Chrome的专用测试资料目录，不操作手机、VPN或用户其他Chrome窗口。首次使用这个资料目录时需要在弹出的Chrome完成网页登录；身份验证、验证码和协议确认由使用者操作，代码不保存密码。

新老用户和来电必须从未登录状态连贯选择：

```bash
node web_ui_run.mjs --cases L-02,L-04,L-06 --batch-dir "/新的登录观察批次" --mode incremental
```

看到“登录观察已启动”后再点击Google并在独立弹窗确认。进程会继续跟踪真实回调和原三分钟窗口，不能关闭终端后用当前账号追溯。若启动时已经登录，L-02可检查实际会员，但关联来电分类会记阻塞。

独立入口按传入编号顺序执行，已记录结果不会无提示重复执行。不同数据状态的用例需按索引满足前置，例如未订阅拦截在支付前、首次Video在消费前。完整test轮次使用`--mode full`，必须已有匹配的`device-preflight.json`清理重置证据；本入口不会绕过环境前置自动清理数据。PC的G-10/G-12仍需原生适配或人工证据；N-02直接完成网页Enjoy引导，N-03改用实际匹配检查。独立入口不通过授权API冒充弹窗实测。

2026-10-26起PC权限接受标准：实际进入匹配连接页或视频会话即可证明授权。N-03改为一次真实匹配检查，不要求捕获浏览器下落通知；N-10同样记录匹配授权证据：PC未订阅发送礼物提示Sending Gifts is exclusive to Premiums.为正确（用户最新确认）；报告注明实际余额。G-10检查Important Tips与权限引导；G-12检查Chrome更改权限后的重新加载提示。六项PC专属排除和全平台停用的S-10不会列入PC执行计划及Allure统计，Android/iOS保留各自平台专属范围。

手机阶段继续运行`web_ui_mobile_run.py --platform android|ios`。有效计费阶段为`billing-paid`、`empty-video`，`billing-free`随S-10停用。手机S-04/S-06分别有`first_video`/`subscribed_video_dial`封装入口，执行前遵守平台暂缓及会员前置规则。当前还不能称为三端全部用例一条命令无人值守完成。

查看某项定义和源码位置无需连接设备：

```bash
python3 web_ui_case_index.py --case S-05
python3 web_ui_case_index.py --export
```

输出包含用例步骤、预期、定义位置以及Web/Android/iOS的实现文件和实时行号；Allure详情和前置附件也包含来源索引。报告Logo由`web_ui_config.json`的`report_branding.logo`配置，普通和单文件报告均内嵌同一图片，换网站不必改文件名。
