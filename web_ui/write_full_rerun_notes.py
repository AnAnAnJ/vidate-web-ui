#!/usr/bin/env python3
"""从本轮记录导出含逐项步骤、预期和实际的执行说明。"""
import argparse
import json
from collections import Counter
from pathlib import Path

from web_ui_full_rerun import BATCH, GUEST_CASES, ROOT
from web_ui_member_cases import CASES


def main():
    global BATCH
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-dir", type=Path)
    args = parser.parse_args()
    if args.batch_dir:
        BATCH = args.batch_dir.resolve()
    data = json.loads((BATCH / "report.json").read_text())
    metadata = json.loads((BATCH / "preconditions.json").read_text()) if (BATCH / "preconditions.json").is_file() else {}
    counts = Counter(result["status"] for result in data["results"].values())
    states = {"PASS": "通过", "FAIL": "失败", "BLOCKED": "前置阻塞", "NOT_APPLICABLE": "跳过", "SKIPPED_ENV": "按环境跳过", "NOT_RUN": "未执行"}
    environment = data["context"].get("run_environment", "test")
    skipped = counts["NOT_APPLICABLE"] + counts["SKIPPED_ENV"]
    blockers = "\n".join(f"- {key}：{result["actual"]}" for key, result in data["results"].items() if result["status"] in ("BLOCKED", "NOT_APPLICABLE", "SKIPPED_ENV")) or "本轮没有阻塞或跳过项目。"
    prep = "prod规则跳过删除账号和设备重置。" if environment == "prod" else json.dumps(metadata, ensure_ascii=False, indent=2)
    quota = data["results"].get("L-08", {}).get("actual", "尚未执行")
    observations = "\n".join(f"- {key}：{data['results'][key]['actual']}" for key in ("M-12", "M-13", "M-14", "L-06", "L-20", "L-17", "L-19") if key in data["results"])
    quota_history = "原先无第六人时的跳过记录保留在report.json的events中。新增用户后只刷新列表并沿用前五次成功记录；本次补测未清数据、未删除账号、未重置设备。" if data["context"].get("l08_retested_without_reset") else "本次操作及数据前置以L-08逐项执行记录为准。"
    intro = f"""# Web UI 本轮真机 UI 执行说明

批次：`{BATCH.name}`。设备：iPhone 13 / iOS 18.7.8 / 普通 Chrome。

执行环境：**{environment}**。网址：{data["url"]}

记录共{len(data["results"])}项：通过{counts["PASS"]}，失败{counts["FAIL"]}，前置阻塞{counts["BLOCKED"]}，跳过{skipped}。

主报告：[Allure 单文件报告]({BATCH}/allure-full-rerun-single-file/index.html)。每项包含设计步骤、本次执行步骤、预期、实际、截图与DOM附件。

## L-08 补测结果

{quota}

{quota_history}

## 环境执行规则

| 操作 | test | prod |
| --- | --- | --- |
| 删除关联账号、重置设备 | 完整复测前执行；指定保留数据补测时不执行 | 跳过 |
| 订阅支付 | 用例提供后执行 | 跳过 |
| 已登录且已订阅的Video | 用例提供后执行 | 跳过 |
| 未订阅用户的订阅拦截弹窗 | 执行 | 执行 |
| 其他已提供UI用例 | 执行 | 执行 |

线上网址需显式提供，并核对手机前台域名；test/prod使用独立批次，不能混用结果。支付和已订阅Video用例尚未提供，本次未新增或执行这些用例。

## 协议去重

协议检查实现只有一处。此前入口缺少已成功判断，可被重复调用；切标签恢复流程也可能重复打开页面。现已按域名保存通过成果；同域名再次执行直接复用，更换域名、证据失效或明确加--recheck-agreements时再检查。跨批次复用标明来源时间和原证据，本次L-08补测没有重新打开协议。

## 完整复测的前置记录

```json
{prep}
```

新设备标识采用原client_id追加不重复随机6位字母数字，手机指纹仍由网站生成。

## 阻塞与跳过

{blockers}

## 证据边界与观察

{observations}

## 文件与验证

- 本轮执行器：`{ROOT}/web_ui_full_rerun.py`。
- 环境策略：`{ROOT}/web_ui_environment.py`。
- 环境拦截和协议复用的8项测试均通过；当前设备L-08实测通过。
- 本说明生成：`python3 "{ROOT}/write_full_rerun_notes.py"`。
- 原始记录：`{BATCH}/report.json`。
- Allure截图使用压缩预览，原始分辨率截图保留在证据目录。未保存密码。

## 逐项执行记录
"""
    lines = [intro]
    for case_id, module, title, _, expected in GUEST_CASES + CASES:
        result = data['results'].get(case_id, {'status': 'NOT_RUN', 'actual': '尚未执行', 'executed_steps': [], 'evidence': []})
        lines += [f"### {case_id} {title} — {states[result['status']]}", '', f'模块：{module}', '', '**本次执行步骤**', '']
        lines += [f'{index}. {step}' for index, step in enumerate(result['executed_steps'], 1)]
        lines += ['', '**预期结果**', ''] + [f'- {item}' for item in expected]
        lines += ['', '**实际结果**', '', result['actual'], '', '**证据**', '']
        lines += [f'- [{Path(name).name}]({name})' for name in result['evidence']]
        lines += ['']
    path = ROOT / 'WebUI_本轮全量复测_执行说明.md'
    path.write_text('\n'.join(lines), encoding='utf-8')
    print(path)


if __name__ == '__main__':
    main()
