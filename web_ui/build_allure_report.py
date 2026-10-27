#!/usr/bin/env python3
"""把已核验的游客真机记录导入 Allure，不重新执行网页操作。"""

from __future__ import annotations

import hashlib
import html
import json
import mimetypes
import re
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from web_ui_report_branding import apply_branding


ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = ROOT / "outputs"
ARTIFACTS = OUTPUTS / "iphone_ui_artifacts"
SOURCE = ARTIFACTS / "execution_summary.md"
RESULTS = OUTPUTS / "allure-results"
REPORT = ROOT / "allure-report"
SINGLE = OUTPUTS / "allure-single-file"
REMAINING = ARTIFACTS / "remaining_2026_10_19"
BASE_REPORT = ARTIFACTS / "guest_final_2026_10_19" / "report.json"
TITLE = "Web UI · 游客 UI 测试报告"
CASE_IDS = {1: "G-03", 2: "G-04", 3: "G-05", 4: "G-06", 5: "G-07", 6: "G-10", 7: "G-11", 8: "G-12", 9: "M-09", 10: "M-10", 11: "M-11", 12: "M-12", 13: "M-13", 14: "M-14", 17: "G-17"}
BASE_CASES = {1, 2, 3, 4, 5, 9, 10, 17}
OBSERVATIONS = {
    13: ("Q-01 额度刷新差异待确认", "第二次匹配前为 7 次，挂断并 Exit 后即时显示 6 次，随后刷新为 5 次。本轮主动启动匹配 2 次，最终消耗 3 次。现有 UI 证据不能区分延迟记账、挂断后自动续配或重复扣次，需要结合服务端匹配记录确认。"),
    6: ("Q-02 权限提示标题待优化", "点击不允许后，标题提示相机不可用或被其他应用占用，正文才说明需要权限。建议拒绝权限时直接说明权限被拒绝。"),
}


def dump(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_cases(markdown: str) -> list[dict]:
    cases = []
    for match in re.finditer(r"^### (\d+)、([^\n]+)\n(.*?)(?=^### |^## |\Z)", markdown, re.M | re.S):
        number, title, body = int(match[1]), match[2], match[3]
        fields = {}
        for label in ("执行步骤", "预期结果", "实际结果", "结果"):
            found = re.search(rf"^- {label}：(.+)$", body, re.M)
            if not found:
                raise ValueError(f"用例 {number} 缺少 {label}")
            fields[label] = found[1]
        if not fields["结果"].startswith("通过"):
            raise ValueError(f"用例 {number} 尚未通过，需显式映射实际状态")
        evidence = [(label, Path(path)) for label, path in re.findall(r"\[([^\]]+)\]\((/[^)]+)\)", body)]
        if number in BASE_CASES and BASE_REPORT not in [path for _, path in evidence]:
            evidence.append(("游客基础检查原始记录", BASE_REPORT))
        if number == 8:
            evidence.append(("权限流程原始记录（通知已根据截图复核）", REMAINING / "permission-report.json"))
        if number == 13:
            evidence.extend((label, REMAINING / name) for label, name in (("退出后即时页面", "message-stopped.png"), ("刷新后最终页面", "final-state.png")))
        cases.append({"number": number, "title": title, "fields": fields, "evidence": evidence})
    if [case["number"] for case in cases] != list(CASE_IDS):
        raise ValueError("导入的用例编号与原始 15 项不一致")
    return cases


def attachment(label: str, path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(path)
    name = hashlib.sha256(path.read_bytes()).hexdigest()[:24] + "-attachment" + path.suffix
    shutil.copyfile(path, RESULTS / name)
    content_type = mimetypes.guess_type(path.name)[0] or "text/plain"
    return {"name": label, "source": name, "type": content_type}


def event_range(name: str, first: int = 0, last: int = -1) -> tuple[int, int]:
    events = json.loads((REMAINING / f"{name}-report.json").read_text())["events"]
    return tuple(int(datetime.fromisoformat(events[index]["time"]).timestamp() * 1000) for index in (first, last))


def timing(case: dict) -> tuple[int, int, str]:
    number = case["number"]
    if number == 11:
        return (*event_range("exit", 2, 3), "原始启动匹配日志")
    if number == 12:
        return (*event_range("exit", 3), "原始 Exit 执行日志")
    if number == 13:
        return (*event_range("message"), "原始执行日志")
    if number in (6, 7, 8):
        first, last = {6: (1, 3), 7: (3, 5), 8: (5, -1)}[number]
        return (*event_range("permission", first, last), "原始权限流程对应步骤日志")
    image = next(path for _, path in case["evidence"] if path.suffix == ".png")
    timestamp = int(image.stat().st_mtime * 1000)
    return timestamp, timestamp, "只记录证据采集时点；界面 0ms 表示未记录精确耗时"


def group(number: int) -> str:
    if number <= 5:
        return "01 Discover 浏览与游客拦截"
    if number <= 8:
        return "02 Match 权限流程"
    if number <= 14:
        return "03 Match 导航、匹配与通话"
    return "04 Profile 游客拦截"


def description(case: dict, batch: str, timing_note: str) -> str:
    fields = case["fields"]
    actions = fields["执行步骤"].rstrip("。").split("；")
    steps = "".join(f"<li>{html.escape(action)}</li>" for action in actions)
    value = (
        f"<p><strong>原始编号：</strong>{case['number']}　<strong>身份：</strong>网页未登录游客　<strong>证据批次：</strong>{batch}</p>"
        f"<h3>执行步骤</h3><ol>{steps}</ol>"
        f"<h3>预期结果</h3><p>{html.escape(fields['预期结果'])}</p>"
        f"<h3>实际结果</h3><p>{html.escape(fields['实际结果'])}</p>"
        f"<h3>判定说明</h3><p>{html.escape(fields['结果'])}</p>"
    )
    if case["number"] in OBSERVATIONS:
        title, detail = OBSERVATIONS[case["number"]]
        value += f"<h3>{html.escape(title)}</h3><blockquote>{html.escape(detail)}</blockquote>"
    value += f"<p><small>时间记录：{html.escape(timing_note)}。本报告从已有实测证据导入，不代表重新运行了一轮测试。</small></p>"
    return value


def write_result(case: dict) -> None:
    number = case["number"]
    case_id = CASE_IDS[number]
    batch = "同日游客基础复测" if number in BASE_CASES else "同日游客视角实测" if number == 14 else "本次额度重置后补测"
    start, stop, timing_note = timing(case)
    labels = [
        {"name": key, "value": value}
        for key, value in (("suite", group(number)), ("feature", group(number)), ("story", case["title"]), ("severity", "normal"), ("framework", "Appium XCUITest"), ("language", "python"), ("host", "iPhone 13"), ("tag", "网页游客"), ("tag", batch))
    ]
    if number in OBSERVATIONS:
        labels.append({"name": "tag", "value": OBSERVATIONS[number][0]})
    steps = [{"name": f"{index}、{action}", "status": "passed"} for index, action in enumerate(case["fields"]["执行步骤"].rstrip("。").split("；"), 1)]
    steps.append({"name": "核对预期与实际结果", "status": "passed", "parameters": [{"name": key, "value": case["fields"][key]} for key in ("预期结果", "实际结果")]})
    result = {
        "uuid": str(uuid.uuid5(uuid.NAMESPACE_URL, "randomvideochat-2026-10-19-guest-" + case_id)),
        "historyId": hashlib.sha256(("randomvideochat.guest." + case_id).encode()).hexdigest(),
        "testCaseId": case_id,
        "fullName": "randomvideochat.guest." + case_id,
        "name": f"{number:02d}｜{case['title']}",
        "status": "passed", "stage": "finished", "start": start, "stop": stop,
        "descriptionHtml": description(case, batch, timing_note),
        "labels": labels,
        "links": [{"type": "custom", "name": "被测站点", "url": "https://test.randomvideochat-talk.pages.dev/"}],
        "parameters": [{"name": "用例编号", "value": case_id}, {"name": "执行批次", "value": batch}, {"name": "时间依据", "value": timing_note, "mode": "hidden"}],
        "steps": steps,
        "attachments": [attachment(label, path) for label, path in case["evidence"]],
    }
    if number in OBSERVATIONS:
        title, detail = OBSERVATIONS[number]
        note = RESULTS / f"{case_id}-observation-attachment.txt"
        note.write_text(title + "\n\n" + detail, encoding="utf-8")
        result["attachments"].append({"name": title, "source": note.name, "type": "text/plain"})
    dump(RESULTS / f"{result['uuid']}-result.json", result)


def main() -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    cases = parse_cases(SOURCE.read_text(encoding="utf-8"))
    for case in cases:
        write_result(case)
    environment = {
        "测试身份": "网页未登录游客 · 普通 Chrome",
        "执行范围": "15 项累计实测；其中 5 项为最后补测",
        "本轮额度": "8 → 5；最终消耗 3 次",
        "待确认观察": "Q-01 额度刷新差异（用例 13）；Q-02 权限标题（用例 6）",
        "测试设备": "iPhone 13 / iOS 18.7.8",
        "测试日期": "2026-10-19",
        "测试地址": "https://test.randomvideochat-talk.pages.dev/",
        "时间说明": "既有记录导入；旧用例 0ms 表示缺少精确耗时",
    }
    properties = "\n".join(f"{key.replace(' ', '_')}={value}" for key, value in environment.items())
    properties = "".join(f"\\u{ord(char):04x}" if ord(char) > 127 else char for char in properties)
    (RESULTS / "environment.properties").write_text(properties, encoding="ascii")
    dump(RESULTS / "executor.json", {"name": "iPhone 真机 · 游客 UI 回归", "type": "local", "buildName": "2026-10-19 · 原始记录导入", "reportName": TITLE})
    for single_file, destination in ((False, REPORT), (True, SINGLE)):
        with tempfile.TemporaryDirectory(prefix="randomvideochat-allure-") as temp:
            generated = Path(temp) / "report"
            command = ["allure", "generate", str(RESULTS), "-o", str(generated), "--lang", "zh", "--name", TITLE]
            if single_file:
                command.append("--single-file")
            subprocess.run(command, check=True)
            apply_branding(generated, OUTPUTS)
            shutil.copytree(generated, destination, dirs_exist_ok=True)
    print(f"Allure 用例数：{len(cases)}")
    print(f"交互报告：{REPORT / 'index.html'}")
    print(f"单文件报告：{SINGLE / 'index.html'}")


if __name__ == "__main__":
    main()
