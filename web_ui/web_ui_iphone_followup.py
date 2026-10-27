#!/usr/bin/env python3
"""检查已登录 iPhone Chrome 的匹配和消息入口。

前置：Chrome 已登录测试账号，Appium 与 WebDriverAgent 已启动。
额度不足时记录阻塞，避免把未发生的匹配和通话标为通过。
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone

from web_ui_iphone_ui import Driver, OUT


def names(driver: Driver) -> set[str]:
    """读取可见原生控件名称集合，供平台提示和页面状态判断使用。"""
    return {
        node.attrib.get("name", "")
        for node in driver.tree().iter()
        if node.attrib.get("visible") == "true"
    }


def result(case_id: str, status: str, detail: str, screenshot: str = "") -> dict:
    """构造单项结果字典，包含编号、状态、实际说明及截图路径。"""
    return {"case_id": case_id, "status": status, "detail": detail, "screenshot": screenshot}


def run() -> int:
    """执行该脚本定义的真机检查，保存截图和结果并返回退出码。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", required=True)
    args = parser.parse_args()
    driver = Driver(args.session_id)
    results = []

    current = names(driver)
    if "Back" in current:
        driver.click("Back")
    if "Match" not in names(driver):
        raise RuntimeError("无法定位 Match 导航；请先返回主页面")

    driver.click("Match")
    time.sleep(0.5)
    if driver.visible("允许", "XCUIElementTypeButton"):
        driver.click("允许")
        time.sleep(0.3)
    current = names(driver)
    for _ in range(10):
        if any("Membership expired" in name or "Free matches left" in name for name in current):
            break
        time.sleep(0.5)
        current = names(driver)
    if "Membership expired. Renew Premium for more matches & exclusive features." in current:
        driver.tap(190, 610)
        time.sleep(0.5)
        current = names(driver)
        image = driver.screenshot("A-03-matching-limit")
        if "You've reached the free matching limit." not in current:
            results.append(result("A-03", "FAIL", "显示会员过期，但点击后未出现预期额度提示", image))
        else:
            results.append(result("A-03", "BLOCKED", "当前账号匹配额度已用尽；点击 Start matching 显示 Get Premium，无法进入匹配", image))
        for _ in range(3):
            time.sleep(0.7)
            driver.tap(362, 123)
            if "Connect" in names(driver):
                break
        else:
            raise RuntimeError("付费页关闭后仍无法返回底部导航")
    else:
        results.append(result("A-03", "NEEDS_REVIEW", "账号未显示额度限制；需在有额度账号上继续验证匹配流程", driver.screenshot("A-03-match-entry")))

    if results[0]["status"] == "BLOCKED":
        for case_id, detail in (
            ("A-04", "未进入匹配中，无法验证 Exit 及次数扣减"),
            ("A-05", "未匹配成功，无法验证消息发送"),
            ("A-06", "未匹配成功，无法验证视频视角切换"),
        ):
            results.append(result(case_id, "BLOCKED", detail))

    driver.click("Connect")
    time.sleep(0.5)
    current = names(driver)
    if "假视频用户001" in current:
        driver.click("假视频用户001")
        time.sleep(0.5)
        current = names(driver)
        image = driver.screenshot("A-05-existing-chat-limit")
        if "You've reached the free message limit." in current:
            results.append(result("A-05-existing-chat", "BLOCKED", "已有测试会话显示免费消息次数已用尽，输入区不可用", image))
        else:
            results.append(result("A-05-existing-chat", "NEEDS_REVIEW", "已有测试会话可打开；发送能力需单独验证", image))

    OUT.mkdir(parents=True, exist_ok=True)
    report = OUT / "authenticated_report.json"
    report.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "device": "connected iPhone / Chrome",
        "results": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report)
    for item in results:
        print(f"{item['case_id']} {item['status']}: {item['detail']}")
    return 1 if any(item["status"] == "FAIL" for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(run())
