#!/usr/bin/env python3
"""在普通 Chrome 的网页游客状态补测退出和消息，每项只主动启动一次。"""

from __future__ import annotations

import argparse
import json
import random
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import web_ui_iphone_ui as ui
from web_ui_iphone_guest_match import match_count


def main() -> int:
    """解析命令行参数并执行所选阶段，将执行结果保存到对应批次。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--case", choices=("permission", "exit", "message"), required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    ui.OUT = args.output_dir.resolve()
    ui.OUT.mkdir(parents=True, exist_ok=True)
    driver = ui.Driver(args.session_id)
    events = []
    result = {"case": args.case, "status": "INCOMPLETE", "events": events}

    def log(action: str, **details) -> None:
        """追加带时间戳的执行事件，立即输出以便跟踪现场。"""
        event = {"time": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(), "action": action, **details}
        events.append(event)
        print(json.dumps(event, ensure_ascii=False), flush=True)

    def state():
        """同时读取原生页面树和可见文案，供后续动作使用同一份现场状态。"""
        tree = driver.tree()
        names = {n.attrib.get("name", "") for n in tree.iter() if n.attrib.get("visible") == "true"}
        return tree, names

    def shot(name: str) -> str:
        """保存当前页面截图并返回证据路径。"""
        path = driver.screenshot(name)
        log("截图", path=path)
        return path

    def stop() -> set[str]:
        """退出当前搜索或通话，返回退出后可见的页面名称集合。"""
        _, names = state()
        if "Start matching" in names:
            return names
        if "Searching for you..." in names:
            driver.tap(190, 665)
            log("点击匹配页 Exit", x=190, y=665)
        else:
            driver.tap(358, 145)
            log("点击通话挂断", x=358, y=145)
            # 挂断后可能立即回到搜索页，直接退出，避免继续消耗次数。
            time.sleep(0.2)
            driver.tap(190, 665)
            log("点击自动返回搜索页的 Exit", x=190, y=665)
        _, names = state()
        if "Login, 网页对话框" in names:
            driver.dismiss_login()
            _, names = state()
        return names

    _, names = state()
    before = match_count(names)
    if "Start matching" not in names or "Connect" in names or before is None or not 1 <= before <= 8:
        raise RuntimeError(f"前置条件不满足：游客次数={before}，当前页面={sorted(names)}")
    result["before"] = before
    log("检查游客前置条件", remaining=before, authenticated=False)
    if args.case == "permission":
        def wait_for(name: str) -> set[str]:
            """在限定时间内轮询指定文案，超时抛异常并保留失败原因。"""
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                tree, current = state()
                if name in current:
                    return current
                time.sleep(0.2)
            raise AssertionError(f"15 秒内未出现：{name}")

        try:
            driver.click("kLegacyToolbarToolsMenuButtonIdentifier")
            driver.click("kToolsMenuReload")
            wait_for("PermissionsDialogAccessibilityIdentifier")
            shot("permission-request")
            driver.click("不允许")
            wait_for("Try Again")
            shot("permission-denied")
            log("拒绝权限后出现开启权限说明和 Try Again")
            driver.click("Try Again")
            wait_for("PermissionsDialogAccessibilityIdentifier")
            shot("permission-retry-request")
            tree, _ = state()
            (ui.OUT / "permission-retry.xml").write_text(ui.ET.tostring(tree, encoding="unicode"), encoding="utf-8")
            log("点击 Try Again 后页面刷新并再次出现系统权限请求")
            driver.click("允许")
            for index in range(5):
                time.sleep(0.25)
                shot(f"permission-allowed-{index}")
            _, names = state()
            after = match_count(names)
            result.update(status="REVIEW", after=after, returned_to_match="Start matching" in names)
            log("已允许权限，截图待核对顶部通知", before=before, after=after)
        except Exception as error:
            result["error"] = f"{type(error).__name__}: {error}"
            log("权限检查异常", error=result["error"])
        report = ui.OUT / "permission-report.json"
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(report, flush=True)
        return 0 if result["status"] == "REVIEW" else 1

    shot(f"{args.case}-before")
    active = False
    try:
        driver.click("Start matching")
        active = True
        log("点击 Start matching 一次")
        if args.case == "exit":
            time.sleep(0.25)
            shot("exit-searching")
            driver.tap(190, 665)
            log("立即点击 Exit", x=190, y=665)
            _, names = state()
            after = match_count(names)
            result.update(after=after, returned_to_match="Start matching" in names)
            result["status"] = "PASS" if "Start matching" in names and after == before - 1 else "INCOMPLETE"
            shot("exit-result")
            active = "Start matching" not in names
        else:
            deadline = time.monotonic() + 18
            field = None
            while time.monotonic() < deadline:
                fields = driver.request("/elements", {"using": "class name", "value": "XCUIElementTypeTextView"})
                if fields:
                    field = fields[0][ui.ELEMENT_KEY]
                    break
                time.sleep(0.15)
            if field is None:
                log("18 秒内未发现通话消息输入框")
            else:
                greeting = random.choice(("Hi!", "Hey!", "Hello!"))
                driver.request(f"/element/{field}/value", {"text": greeting, "value": list(greeting)})
                rect = driver.request(f"/element/{field}/rect")
                driver.tap(340, int(rect["y"] + rect["height"] / 2))
                log("输入并点击发送", greeting=greeting, send_x=340, send_y=int(rect["y"] + rect["height"] / 2))
                result["greeting"] = greeting
                time.sleep(0.2)
                shot("message-sent")
                tree, names = state()
                (ui.OUT / "message-sent.xml").write_text(ui.ET.tostring(tree, encoding="unicode"), encoding="utf-8")
                messages = [name for name in names if greeting in name]
                fields_after = [n.attrib for n in tree.iter() if n.tag == "XCUIElementTypeTextView"]
                result.update(message_texts=messages, fields_after=fields_after)
                result["status"] = "PASS" if messages and all(greeting not in f.get("value", "") for f in fields_after) else "REVIEW"
                log("核对发送结果", visible_messages=messages, input_values=[f.get("value") for f in fields_after])
    except Exception as error:
        result["error"] = f"{type(error).__name__}: {error}"
        log("执行异常", error=result["error"])
    finally:
        if active:
            names = stop()
            result.update(after=match_count(names), returned_to_match="Start matching" in names)
            shot(f"{args.case}-stopped")
        log("结束", status=result["status"], before=before, after=result.get("after"), returned_to_match=result.get("returned_to_match"))
        report = ui.OUT / f"{args.case}-report.json"
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(report, flush=True)
    return 0 if result["status"] in ("PASS", "REVIEW") else 1


if __name__ == "__main__":
    raise SystemExit(main())
