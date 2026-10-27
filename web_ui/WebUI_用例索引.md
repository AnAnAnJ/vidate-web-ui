# Web UI 用例与源码索引

手机目录63项；PC有效57项。源码行号由脚本实时解析。页面通过按钮名称、角色和CSS定位，原生操作按Android/iOS/Web分别适配。

S-10 已在 Android / iOS / PC 全部注释停用，备注：免费一分钟停用。不计入有效目录；可用 `--case S-10` 查看备注及原定义注释位置。

| 编号 | 手机原始用例 | PC用例 | 定义 | Web实现 | 手机实现 |
| --- | --- | --- | --- | --- | --- |
| G-03 | All / Video / Text 切换 | All / Video / Text 切换 | web_ui_full_rerun.py:28 | web_ui_desktop_suite.mjs:700 | web_ui_full_rerun.py:289 |
| G-04 | 用户卡片存在 | 用户卡片存在 | web_ui_full_rerun.py:29 | web_ui_desktop_suite.mjs:700 | web_ui_full_rerun.py:306 |
| G-05 | 下滑列表五次 | 下滑列表五次 | web_ui_full_rerun.py:30 | web_ui_desktop_suite.mjs:700 | web_ui_full_rerun.py:311 |
| G-06 | 卡片 Say Hi 触发登录 | 卡片 Say Hi 触发登录 | web_ui_full_rerun.py:31 | web_ui_desktop_suite.mjs:700 | web_ui_full_rerun.py:323 |
| G-07 | 卡片 Video 触发登录 | 卡片 Video 触发登录 | web_ui_full_rerun.py:32 | web_ui_desktop_suite.mjs:700 | web_ui_full_rerun.py:323 |
| G-10 | 拒绝音视频权限 | PC拒绝音视频权限后的说明引导 | web_ui_full_rerun.py:33 | web_ui_desktop_suite.mjs:700 | web_ui_complete_suite.py:217 |
| G-11 | Try Again 重新请求权限 | 已移除 | web_ui_full_rerun.py:34 | PC已移除 | web_ui_complete_suite.py:217 |
| G-12 | 允许权限后的下落通知 | PC允许权限后的重新加载提示 | web_ui_full_rerun.py:35 | web_ui_desktop_suite.mjs:700 | web_ui_complete_suite.py:217 |
| M-09 | 顶部三个标签切换 | 顶部三个标签切换 | web_ui_full_rerun.py:36 | web_ui_desktop_suite.mjs:700 | web_ui_full_rerun.py:344 |
| M-10 | 中间 Discover 跳转 | 中间 Discover 跳转 | web_ui_full_rerun.py:37 | web_ui_desktop_suite.mjs:700 | web_ui_full_rerun.py:344 |
| M-11 | 启动匹配进入连接页 | 启动匹配进入连接页 | web_ui_full_rerun.py:38 | web_ui_desktop_suite.mjs:700 | web_ui_complete_suite.py:412 |
| M-12 | 搜索中 Exit 与扣次 | 搜索中 Exit 与扣次 | web_ui_full_rerun.py:39 | web_ui_desktop_suite.mjs:700 | web_ui_complete_suite.py:412 |
| M-13 | 发送简短英文问候 | 发送简短英文问候 | web_ui_full_rerun.py:40 | web_ui_desktop_suite.mjs:700 | web_ui_complete_suite.py:448 |
| M-14 | 点击自己的小窗切换视频 | 点击自己的小窗切换视频 | web_ui_full_rerun.py:41 | web_ui_desktop_suite.mjs:700 | web_ui_complete_suite.py:448 |
| G-17 | Profile 触发登录 | Profile 触发登录 | web_ui_full_rerun.py:42 | web_ui_desktop_suite.mjs:700 | web_ui_full_rerun.py:332 |
| L-01 | 登录弹窗的两个协议正文非空 | 登录弹窗的两个协议正文非空 | web_ui_member_cases.py:4 | web_ui_standalone/web_ui_run.mjs:49 | web_ui_member_runner.py:370 |
| L-02 | Google 登录并确认实际会员状态 | Google 登录并确认实际会员状态 | web_ui_member_cases.py:5 | web_ui_desktop_suite.mjs:460 | web_ui_full_rerun.py:154 |
| L-03 | 已登录 Discover 存在用户卡片 | 已登录 Discover 存在用户卡片 | web_ui_member_cases.py:6 | web_ui_desktop_suite.mjs:700 | web_ui_member_suite.py:109 |
| L-04 | 新注册三分钟内收到来电并取消 | 新用户接听首次欢迎来电 | web_ui_member_cases.py:7 | web_ui_desktop_suite.mjs:519 | web_ui_exception_rerun.py:13 |
| L-05 | 新注册第二次来电接听 | 已移除 | web_ui_member_cases.py:8 | PC已移除 | web_ui_exception_rerun.py:13 |
| L-06 | 非新注册用户无欢迎来电 | 非新注册用户无欢迎来电 | web_ui_member_cases.py:9 | web_ui_desktop_suite.mjs:519 | web_ui_exception_rerun.py:13 |
| L-07 | Say Hi 成功下落通知 | Say Hi 成功下落通知 | web_ui_member_cases.py:10 | web_ui_desktop_suite.mjs:214 | web_ui_member_suite.py:258 |
| L-08 | 给五个不同用户打招呼后第六次触发订阅 | 给五个不同用户打招呼后第六次触发订阅 | web_ui_member_cases.py:11 | web_ui_desktop_suite.mjs:700 | web_ui_member_suite.py:282 |
| L-09 | 卡片 Video 触发订阅 | 卡片 Video 触发订阅 | web_ui_member_cases.py:12 | web_ui_desktop_suite.mjs:700 | web_ui_member_suite.py:115 |
| L-10 | 关闭订阅弹窗 | 关闭订阅弹窗 | web_ui_member_cases.py:13 | web_ui_desktop_suite.mjs:700 | web_ui_member_suite.py:122 |
| L-11 | 卡片打码标签触发订阅 | 已移除 | web_ui_member_cases.py:14 | PC已移除 | web_ui_member_suite.py:130 |
| L-12 | 卡片进入用户详情 | 卡片进入用户详情 | web_ui_member_cases.py:15 | web_ui_desktop_suite.mjs:700 | web_ui_member_suite.py:153 |
| L-13 | 详情打码标签触发订阅 | 详情打码标签触发订阅 | web_ui_member_cases.py:16 | web_ui_desktop_suite.mjs:700 | web_ui_member_suite.py:130 |
| L-14 | 更多菜单包含拉黑举报取消 | 已移除 | web_ui_member_cases.py:17 | PC已移除 | web_ui_member_suite.py:158 |
| L-15 | 拉黑打开二次确认 | 拉黑打开二次确认 | web_ui_member_cases.py:18 | web_ui_desktop_suite.mjs:700 | web_ui_member_suite.py:166 |
| L-16 | 举报显示原因列表 | 举报显示原因列表 | web_ui_member_cases.py:19 | web_ui_desktop_suite.mjs:700 | web_ui_member_suite.py:197 |
| L-17 | 提交举报显示成功通知 | 提交举报显示成功通知 | web_ui_member_cases.py:20 | web_ui_desktop_suite.mjs:700 | web_ui_member_suite.py:205 |
| L-18 | 取消拉黑二次确认 | 取消拉黑二次确认 | web_ui_member_cases.py:21 | web_ui_desktop_suite.mjs:700 | web_ui_member_suite.py:175 |
| L-19 | 确认拉黑显示成功通知 | 确认拉黑显示成功通知 | web_ui_member_cases.py:22 | web_ui_desktop_suite.mjs:700 | web_ui_member_suite.py:212 |
| L-20 | 多图自动轮播 | 已移除 | web_ui_member_cases.py:23 | PC已移除 | web_ui_member_suite.py:222 |
| L-21 | 单图不轮播 | 单图不轮播 | web_ui_member_cases.py:24 | web_ui_desktop_suite.mjs:700 | web_ui_member_suite.py:222 |
| L-22 | 详情返回来源页面 | 详情返回来源页面 | web_ui_member_cases.py:25 | web_ui_desktop_suite.mjs:700 | web_ui_member_suite.py:248 |
| L-23 | 随机英文昵称保存及回显 | 随机英文昵称保存及回显 | web_ui_extended_cases.py:8 | web_ui_desktop_suite.mjs:700 | web_ui_extended_suite.py:174 |
| L-24 | 随机英文励志签名保存及回显 | 随机英文励志签名保存及回显 | web_ui_extended_cases.py:9 | web_ui_desktop_suite.mjs:700 | web_ui_extended_suite.py:197 |
| L-25 | 选择标签高亮及计数增加 | 选择标签并逐次等待接口保存 | web_ui_extended_cases.py:10 | web_ui_desktop_suite.mjs:700 | web_ui_extended_suite.py:233 |
| L-26 | 取消标签及计数减少 | 取消标签及计数减少 | web_ui_extended_cases.py:11 | web_ui_desktop_suite.mjs:700 | web_ui_extended_suite.py:263 |
| L-27 | 未订阅点击订阅卡片 | 未订阅点击订阅卡片 | web_ui_extended_cases.py:12 | web_ui_desktop_suite.mjs:700 | web_ui_extended_suite.py:282 |
| L-28 | 订阅弹窗返回来源页 | 订阅弹窗返回来源页 | web_ui_extended_cases.py:13 | web_ui_desktop_suite.mjs:700 | web_ui_extended_suite.py:287 |
| L-29 | 未订阅点击金币入口 | 未订阅点击金币入口 | web_ui_extended_cases.py:14 | web_ui_desktop_suite.mjs:700 | web_ui_extended_suite.py:295 |
| L-30 | 订阅套餐档位切换 | 订阅套餐档位切换 | web_ui_extended_cases.py:15 | web_ui_desktop_suite.mjs:700 | web_ui_extended_suite.py:302 |
| N-01 | 拒绝音视频权限 | 已移除 | web_ui_extended_cases.py:16 | PC已移除 | web_ui_complete_suite.py:217 |
| N-02 | Try Again 刷新并重新申请 | PC权限说明点击Enjoy完成引导 | web_ui_extended_cases.py:17 | web_ui_desktop_suite.mjs:700 | web_ui_complete_suite.py:217 |
| N-03 | 允许权限及 Chrome 通知 | PC匹配确认音视频已授权 | web_ui_extended_cases.py:18 | web_ui_desktop_suite.mjs:700 | web_ui_complete_suite.py:217 |
| N-04 | 顶部三个标签切换 | 顶部三个标签切换 | web_ui_extended_cases.py:19 | web_ui_desktop_suite.mjs:700 | web_ui_extended_suite.py:326 |
| N-05 | 中间 Discover 跳转 | 中间 Discover 跳转 | web_ui_extended_cases.py:20 | web_ui_desktop_suite.mjs:700 | web_ui_extended_suite.py:337 |
| N-06 | 有免费次数时启动匹配 | 有免费次数时启动匹配 | web_ui_extended_cases.py:21 | web_ui_desktop_suite.mjs:700 | web_ui_complete_suite.py:412 |
| N-07 | 连接中 Exit 及次数减一 | 连接中 Exit 及次数减一 | web_ui_extended_cases.py:22 | web_ui_desktop_suite.mjs:700 | web_ui_complete_suite.py:412 |
| N-08 | 发送简短英文问候 | 发送简短英文问候 | web_ui_extended_cases.py:23 | web_ui_desktop_suite.mjs:700 | web_ui_complete_suite.py:448 |
| N-09 | 本人小窗切换视频位置 | 本人小窗切换视频位置 | web_ui_extended_cases.py:24 | web_ui_desktop_suite.mjs:700 | web_ui_complete_suite.py:448 |
| N-10 | 未订阅送礼按余额判断 | PC未订阅送礼按余额判断 | web_ui_extended_cases.py:25 | web_ui_desktop_suite.mjs:700 | web_ui_exception_rerun.py:63 |
| S-01 | Week 卡支付成功及增加300金币 | Week 卡支付成功及增加300金币 | web_ui_extended_cases.py:26 | web_ui_desktop_suite.mjs:700 | web_ui_complete_suite.py:887 |
| S-04 | 首次 Video 显示金币余额 | 清理本站缓存后首次Video显示余额 | web_ui_extended_cases.py:27 | web_ui_desktop_suite.mjs:700 | web_ui_complete_suite.py:784 |
| S-02 | 已订阅金币页七个档位 | 已订阅金币页七个档位 | web_ui_extended_cases.py:28 | web_ui_desktop_suite.mjs:700 | web_ui_complete_suite.py:734 |
| S-03 | 金币充值成功及余额增加 | 金币充值成功及余额增加 | web_ui_extended_cases.py:29 | web_ui_desktop_suite.mjs:700 | web_ui_complete_suite.py:825 |
| S-06 | 余额足够拨号及主播离线切换 | 余额足够拨号及主播离线切换 | web_ui_extended_cases.py:30 | web_ui_desktop_suite.mjs:700 | web_ui_complete_suite.py:797 |
| S-07 | 有金币发送礼物成功 | 有金币发送礼物成功 | web_ui_extended_cases.py:31 | web_ui_desktop_suite.mjs:700 | web_ui_complete_suite.py:869 |
| S-09 | 普通通话每分钟扣除80金币 | 普通视频通话按分钟向上取整扣费 | web_ui_extended_cases.py:32 | web_ui_desktop_suite.mjs:700 | web_ui_call_billing.py:76 |
| S-05 | 非首次 Video 且无金币 | PC非首次Video且无金币 | web_ui_extended_cases.py:35 | web_ui_desktop_suite.mjs:700 | web_ui_call_billing.py:174 |
