#!/usr/bin/env python3
"""在 iPhone Chrome 的网页游客状态运行一轮匹配检查。"""

from __future__ import annotations

import argparse
import json
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from web_ui_iphone_ui import Driver, ELEMENT_KEY


GREETINGS = ("Hi!", "Hello!", "Hey there!", "How are you?")


def visible_names(driver: Driver) -> set[str]:
    """提取当前页面可见原生节点名称，避免按隐藏控件判断状态。"""
    return {
        node.attrib.get("name", "")
        for node in driver.tree().iter()
        if node.attrib.get("visible") == "true"
    }


def match_count(names: set[str]) -> int | None:
    """从可见文案提取剩余免费匹配次数；未显示数字时返回None。"""
    for name in names:
        found = re.fullmatch(r"(\d+) Free matches left", name)
        if found:
            return int(found.group(1))
    return None


def record(results: list[dict], case_id: str, status: str, detail: str, screenshot: str = "") -> None:
    """向本轮历史iPhone结果列表追加编号、状态、实际说明和截图路径。"""
    results.append({"case_id": case_id, "status": status, "detail": detail, "screenshot": screenshot})
    print(f"{case_id} {status}: {detail}", flush=True)


def exit_matching(driver: Driver) -> bool:
    """尝试退出搜索或通话并核对停止状态，返回是否已退出。"""
    for _ in range(2):
        names = visible_names(driver)
        if "Start matching" in names:
            return True
        if "Login, 网页对话框" in names:
            driver.dismiss_login()
            continue
        if "Searching for you..." in names:
            driver.tap(190, 665)
        else:
            driver.tap(358, 145)
        time.sleep(0.2)
    return "Start matching" in visible_names(driver)


def run() -> int:
    """执行该脚本定义的真机检查，保存截图和结果并返回退出码。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent / "iphone_ui_artifacts" / "guest_match")
    args = parser.parse_args()
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    driver = Driver(args.session_id)
    results: list[dict] = []

    if "Match" in visible_names(driver):
        driver.click("Match")
    if driver.visible("允许", "XCUIElementTypeButton"):
        driver.click("允许")
    names = visible_names(driver)
    if "Start matching" not in names:
        raise RuntimeError("请先在网页 Match 页面运行游客匹配脚本")
    if "Connect" in names:
        raise RuntimeError("网页当前为登录态；请通过网页 Log Out 后再运行游客脚本")

    before = match_count(names)
    if before is None or before == 0:
        detail = "游客页未显示可用匹配次数" if before is None else "游客匹配次数为 0"
        if "Log in to continue." in names:
            detail += "；页面显示 Log in to continue"
        for case_id in ("M-11", "M-12", "M-13", "M-14"):
            record(results, case_id, "BLOCKED", detail)
    else:
        greeting = random.choice(GREETINGS)
        try:
            driver.click("Start matching")
            deadline = time.monotonic() + 12
            field = None
            entered = False
            while time.monotonic() < deadline:
                names = visible_names(driver)
                entered |= "Searching for you..." in names
                fields = driver.request("/elements", {"using": "class name", "value": "XCUIElementTypeTextView"})
                if fields:
                    field = fields[0][ELEMENT_KEY]
                    entered = True
                    break
                time.sleep(0.2)
            record(results, "M-11", "PASS" if entered else "FAIL", f"游客启动匹配；开始前剩余 {before} 次；进入匹配={entered}")

            if field is None:
                record(results, "M-13", "BLOCKED", "本轮未进入可输入消息的通话")
                record(results, "M-14", "BLOCKED", "本轮未进入双视频通话")
            else:
                import web_ui_iphone_ui as iphone_ui

                iphone_ui.OUT = out
                driver.screenshot("M-14-before")
                driver.tap(320, 280)
                after_image = driver.screenshot("M-14-after")
                record(results, "M-14", "REVIEW", "已点击本机小窗；需比对切换前后截图", after_image)

                driver.request(f"/element/{field}/value", {"text": greeting, "value": list(greeting)})
                rect = driver.request(f"/element/{field}/rect")
                driver.tap(340, int(rect["y"] + rect["height"] / 2))
                sent_image = driver.screenshot("M-13-sent")
                sent = any(greeting in name for name in visible_names(driver))
                status = "PASS" if sent else "REVIEW"
                detail = f"发送英文问候 {greeting!r}" + ("；页面已显示消息" if sent else "；请核对发送截图")
                record(results, "M-13", status, detail, sent_image)
        finally:
            exited = exit_matching(driver)
            after = match_count(visible_names(driver))
            status = "PASS" if exited and after == before - 1 else "REVIEW" if exited and after is None else "FAIL"
            record(results, "M-12", status, f"退出后返回 Match={exited}；剩余次数 {before} -> {after}；预期仅扣 1 次")

    report = out / "report.json"
    report.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "website guest in regular Chrome tab",
        "results": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report)
    return 1 if any(item["status"] == "FAIL" for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(run())
