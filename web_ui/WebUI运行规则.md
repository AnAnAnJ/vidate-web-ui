# Web UI 运行规则

脚本目录：`/Users/mac/Documents/Codex/2026-10-16/wo-sh/outputs`。文件使用通用名称；实际网址写入每批报告。

## 网站与环境

`web_ui_config.json` 保存默认 `test_url`、可选 `prod_url`。不指定网址时沿用当前配置；可用 `--url` 覆盖。线上地址必须由用户提供，不能猜测。更换网站或环境时使用新的 `--batch-dir`，执行时核对手机前台域名。

| 操作 | test | prod |
| --- | --- | --- |
| 删除关联账号、重置设备 | 完整复测前执行；沿用数据补测时不执行 | 跳过 |
| 订阅及金币支付 | 按已提供用例执行测试支付 | 跳过 |
| 已登录已订阅 Video | 满足有效会员、金币等前置后执行 | 跳过 |
| 强关联未订阅用例 | 当前未订阅时执行 | 当前未订阅时执行；已订阅则跳过 |
| 资料编辑、卡片详情、普通 Match 等通用用例 | 按前置执行 | 按前置执行 |
| 测试会员到期后的恢复 | 首次真实订阅生效后，用当前用户信息接口的 data.uuid 在 test 后台恢复，Duration=864000；不计用例 | 禁止后台恢复及支付 |

test 完整复测：先在后台删除实际设备关联用户，再把设备改为“原 client_id + 不重复的随机6位字母数字后缀”。后台动作使用环境准入；本次增量补测沿用数据，不清理、不重置。

## 会员状态与到期

登录后先刷新 Profile。`Premium Member`、`Active until` 和非空有效期共同表示已订阅；`Upgrade to Premium` 表示未订阅；缺失或冲突为未知，阻塞身份关联动作。资料编辑等通用用例不要求未订阅。

test 支付会员有效5分钟。订阅关联用例前刷新核对当前卡片；超过5分钟后恢复未订阅属于到期行为，不写为订阅丢失。首次真实支付仍校验成功弹窗、金币增加300和会员卡生效；其后需要恢复时，从本手机真实用户信息响应读取主对象 `data.uuid`，核对当前昵称和域名，再在 `https://sj-app-down.jm.ijunj.com/#memberSubscription` 的 test 菜单填入 UUID、Duration=`864000`。后台提示该单位为秒，即10天。提交一次，记录返回结果，再刷新 Profile 确认会员及余额后继续。后台恢复不替代首次支付，不增加金币，也不单列会员到期用例。prod禁止此操作。

`web_ui_capture_identity.mjs` 监听真实响应，仅保存 UUID、昵称、接口路径、状态码和时间，不保存完整响应或令牌。Android 使用当前 Appium 目标对应的 CDP 连接；不能把兴趣标签等嵌套对象的 UUID 当用户 UUID。iOS 保留其原有网页和权限流程，不使用 Android 的 CDP 目标。完整套件在会员失效时生成 `membership-extension-request.json`，由浏览器UI执行后台恢复，再调用 `confirm_membership_extension()` 验证；不再自动重复沙箱充值。身份证据超过120秒须重新采集。

强关联未订阅项：L-08、L-09、L-10、L-11、L-13、L-27～L-30、N-10，以及需要从未订阅开始的 S-01。prod 已订阅时记 `SKIPPED_STATE`。关闭弹窗 L-10/L-28 使用30秒内的前置状态证据，避免导航破坏待关闭弹窗；证据过旧则阻塞。

prod 支付及已订阅 Video 优先记 `SKIPPED_ENV`，在调用动作前拦截。test 首次订阅后按上述后台恢复；只有状态未知、恢复失败或数据前置不满足时记 `BLOCKED`。

## 新老用户及来电

实际登录成功后立即进入 Chat（导航名 `Connect`，当前路由 `/lobby/sessions`），以30秒为窗口：有 `teamoffice` 消息为新用户，无消息为老用户。大小写及空格归一化匹配；页面未加载、错过窗口或观察不完整不能推定老用户。

登录观察阶段必须在本次 Google 登录前启动；脚本先看到游客登录入口，再等待真实回调，确认登录资料后自动观察 Chat。当前已登录账号不能启动此阶段来追溯新老身份。L-04/L-05 仅适用于本次窗口确认的新用户；老用户跳过。旧批次缺失的窗口证据保持缺失，不根据当前历史消息改判。

Android 原生登录观察入口 `web_ui_native_login_watch.py` 默认观察45秒。遇到指纹或两步验证立即保存 `login_checkpoint` 并输出原因；限定时间内未回站则退出，不持续空等。恢复时不能追溯错过的登录后30秒窗口。只保存验证阶段，不保存验证码、密码或无关账户列表。

## 中断恢复与支付防重

首次订阅用例必须先从真实 Profile 确认未订阅，该需求不变。付款前把会员前置和原余额落盘，再执行购买。`payment-attempts.json` 按准备、已提交、支付成功、到账核验四个节点保存，每批每种支付只允许一笔。提交状态先于 Pay 点击落盘；点击超时或截图失败都不能通过重新付款恢复。旧批次存在付款检查点时同样禁止新购，不能凭缺少新记录推定未付款。

`verify-subscription` / `verify-topup` 只补核验已保存成功节点的原订单，不点击 Pay。订阅补核验必须保留原订单付款前未订阅证据，当前已订阅仅是付款后的结果，不能代替首次购买前置。到账成功但自动回站或成功提示缺证据，仍记 `BLOCKED`。记录里的 `verified` 仅表示到账核验，不代表整个用例通过。没有支付成功节点的原订单保持阻塞，需先核对原收银台现场。

```bash
python3 "web_ui_complete_suite.py" verify-subscription --env test --batch-dir "/绝对路径/原批次" --session-file "/绝对路径/原设备连接.json"
python3 "web_ui_complete_suite.py" verify-topup --env test --batch-dir "/绝对路径/原批次" --session-file "/绝对路径/原设备连接.json"
```

阶段入口先检查设备连接，再操作页面；同设备阶段使用互斥锁，避免两个进程同时点击。驱动断开或命令超时后不重发未知动作，后续用例直接阻塞。恢复连接后需健康检查成功，才清除批次的连接阻塞。失败现场最多补采一次，补采失败仍保留原始结论。报告和支付记录采用原子替换写入，防止中断留下半份JSON。

Profile 必须等本页用户信息请求完成、界面连续稳定后才读取会员和金币；HTTP错误或加载失败会阻塞，不能把默认未订阅和0余额作为真实数据。页面查找优先使用已核对的上下文和窗口，失效后才重新发现。

## 执行入口

```bash
cd "/Users/mac/Documents/Codex/2026-10-16/wo-sh/outputs"
python3 "web_ui_full_rerun.py" plan --env prod
python3 "web_ui_extended_suite.py" membership --env test
python3 "web_ui_extended_suite.py" profile --env test
python3 "web_ui_full_rerun.py" quota --env test
```

新网站使用：

```bash
python3 "web_ui_extended_suite.py" inspect --env test --url "https://another-test.example/" --batch-dir "/绝对路径/新的独立批次"
```

在游客登录页操作 Google 登录前启动：

```bash
python3 "web_ui_extended_suite.py" await-login --env test --batch-dir "/绝对路径/本次独立批次"
```

Google 账号通过已存在的浏览器账号选择或交互输入；脚本不持久保存密码。协议已有同域名成功证据时不重复打开。

`quota` 保留前五人记录，先刷新 Discover 并等待列表真正加载，再检查第六个不同用户；没有第六人则跳过边界。

## 范围、顺序及报告

原 L-01～L-22 保留编号。新增业务用例按当前有效目录统计（到期恢复不单列用例），先处理18项未标记用例，再处理订阅项。失败、数据阻塞、环境跳过及身份跳过均明确记录；不能用旧通过结果代替本轮实测。

S-10 在 Android、iOS、PC 全部注释停用，备注：免费一分钟停用。原定义保留于源码注释，不列入执行计划及有效用例统计；旧编号或免费通话入口在设备动作前停止。

关闭麦克风、关闭摄像头继续移除。游客匹配最多8次，满足用例后退出自动匹配。聊天发送简短英文问候；未订阅且余额足够时送礼成功并扣除礼物金额，余额不足时发送失败并提示金币不足。多图只确认自动轮播，3秒可接受。

M-13/M-14在游客阶段、Google登录前执行。游客通话补测检测到已登录时保留账号状态，记为阻塞，不执行退出账号、清认证存储或重新注册来切换身份；报告将历史补测准备与标准测试步骤分开记录。通话完成后的退出只指挂断和停止续配。

Android补充：M-13按聊天区消息显示和输入框清空断言，M-14按两路窗口位置及尺寸互换断言，不额外要求视频解码完全就绪；原生权限遮挡仍单独处理。已完成的断言不因之后补测前置不足而覆盖为阻塞。证据同名时保存为新文件，避免游客截图被登录截图覆盖。

Android的S-04、S-05、S-06暂缓，保留目录并记录跳过，不执行缓存清理、拨号或消费准备；iOS与PC保持自身规则。S-09没有对应类型主播，或有限尝试后没有主播接听则跳过；网页请求异常不能冒充“全部主播离线”。首次Video前置随S-04暂缓且尚未完成时，本轮计费用例按依赖未满足跳过。

通用脚本包含环境及身份拦截、资料编辑、登录观察、权限、通话、沙箱支付、金币页及送礼动作。后台会员恢复仍需通过后台UI执行并回站确认。脚本能力与每批实测结论分开记录；离线检查通过不代表真机用例通过，也不更新历史Allure为本轮通过。

```bash
python3 -m unittest test_web_ui_mobile test_web_ui_execution_rules test_web_ui_reliability
python3 "build_extended_allure.py"
```

Allure 每项包含设计步骤、实际执行步骤、预期、实际和证据。`PASS` 为通过，`FAIL` 为观察到预期偏差，`BLOCKED` 显示阻塞，`SKIPPED_ENV` / `SKIPPED_STATE` 显示跳过。

协议实现只有 `web_ui_member_runner.py` 的 `agreements()` 一处；同域名成功证据保存于 `web_ui_agreement_cache.json`，保留原时间及来源。更换域名、证据失效或明确要求复检时才重新检查。
