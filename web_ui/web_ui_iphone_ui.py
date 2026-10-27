#!/usr/bin/env python3
"""在已连接的 iPhone Chrome 上运行游客 UI 检查。

前置：Appium XCUITest 与 WebDriverAgent 已运行。可用 --session-id 复用会话。
脚本不包含设备密码，也不启动会消耗匹配次数的会话。
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from web_ui_environment import TEST_URL


BASE = "http://127.0.0.1:4723/wd/hub"
OUT = Path(__file__).resolve().parent / "iphone_ui_artifacts"
ELEMENT_KEY = "element-6066-11e4-a52e-4f735466cecf"


class Driver:
    def __init__(self, session_id: str):
        """绑定历史iPhone会话的Appium地址和被测网址，不启动或重签驱动。"""
        self.session_id = session_id
        self.url = f"{BASE}/session/{session_id}"
        self.target_url = TEST_URL

    def request(self, path: str, data: dict | None = None, *, timeout=45) -> object:
        """向当前Appium会话发送W3C请求；data为空时读取，否则提交动作，协议错误抛异常。"""
        body = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(
            self.url + path,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST" if data is not None else "GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                result = json.load(response)
        except urllib.error.HTTPError as error:
            result = json.load(error)
        if isinstance(result.get("value"), dict) and result["value"].get("error"):
            raise RuntimeError(result["value"].get("message", result["value"]["error"]))
        return result["value"]

    def tree(self) -> ET.Element:
        """读取当前原生页面XML树，供控件定位及现场证据使用。"""
        return ET.fromstring(str(self.request("/source")))

    def visible(self, name: str, kind: str | None = None) -> list[ET.Element]:
        """按名称及可选控件类型筛选可见原生节点，返回匹配节点列表。"""
        matches = []
        for node in self.tree().iter():
            attrs = node.attrib
            if attrs.get("visible") != "true":
                continue
            if kind and node.tag != kind:
                continue
            if name in (attrs.get("name"), attrs.get("label"), attrs.get("value")):
                matches.append(node)
        return matches

    def find(self, name: str, index: int = 0) -> str:
        """按名称定位原生控件并返回WebDriver元素ID；index用于同名控件。"""
        elements = self.request("/elements", {"using": "accessibility id", "value": name})
        if not isinstance(elements, list) or len(elements) <= index:
            raise AssertionError(f"找不到控件：{name}，序号 {index}")
        return elements[index][ELEMENT_KEY]

    def click(self, name: str, index: int = 0) -> None:
        """点击指定名称的原生控件；定位失败时停止，避免误点其他元素。"""
        self.request(f"/element/{self.find(name, index)}/click", {})

    def tap(self, x: int, y: int) -> None:
        """在原生屏幕坐标执行真实触屏点击，调用方需先确认目标位置。"""
        self.request("/execute/sync", {"script": "mobile: tap", "args": [{"x": x, "y": y}]})

    def allow_site_permission(self) -> bool:
        """识别被测域名的音视频请求并点击允许，返回本次是否处理了请求。"""
        prompt = any(
            urlsplit(self.target_url).hostname in node.attrib.get("name", "")
            and "摄像头和麦克风" in node.attrib.get("name", "")
            for node in self.tree().iter()
        )
        if prompt:
            self.click("允许")
        return prompt

    def swipe_up(self) -> None:
        """执行一次从下向上的真实触屏滑动，用于列表下滑检查。"""
        self.request(
            "/actions",
            {"actions": [{
                "type": "pointer", "id": "finger1", "parameters": {"pointerType": "touch"},
                "actions": [
                    {"type": "pointerMove", "duration": 0, "x": 195, "y": 650},
                    {"type": "pointerDown", "button": 0},
                    {"type": "pointerMove", "duration": 500, "x": 195, "y": 240},
                    {"type": "pointerUp", "button": 0},
                ],
            }]},
        )

    def dismiss_login(self) -> None:
        """仅在登录弹窗存在时关闭，并核对弹窗已消失。"""
        if self.visible("Login, 网页对话框"):
            self.tap(355, 124)
            if self.visible("Login, 网页对话框"):
                raise AssertionError("登录弹窗未关闭")

    def ensure_discover(self) -> None:
        """进入Discover并等待标签与卡片控件，超时不把加载中页面判为成功。"""
        self.allow_site_permission()
        self.dismiss_login()
        if not self.visible("Text", "XCUIElementTypeButton"):
            self.click("Discover")
        for _ in range(10):
            if self.visible("Text", "XCUIElementTypeButton"):
                return
            time.sleep(0.3)
        if not self.visible("Text", "XCUIElementTypeButton"):
            raise AssertionError("无法进入 Discover 页面")

    def screenshot(self, name: str) -> str:
        """以name为证据名保存当前屏幕截图，返回图片绝对路径。"""
        OUT.mkdir(parents=True, exist_ok=True)
        path = OUT / f"{name}.png"
        path.write_bytes(base64.b64decode(str(self.request("/screenshot"))))
        return str(path)


def assert_visible(driver: Driver, name: str, kind: str | None = None) -> None:
    """断言指定原生控件实际可见；缺失时抛出可读的断言错误。"""
    if not driver.visible(name, kind):
        raise AssertionError(f"页面未显示：{name}")


def run() -> int:
    """执行该脚本定义的真机检查，保存截图和结果并返回退出码。"""
    global OUT
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", required=True, help="现有 Appium 会话 ID")
    parser.add_argument("--output-dir", type=Path, default=OUT, help="截图与报告目录")
    args = parser.parse_args()
    OUT = args.output_dir.resolve()
    driver = Driver(args.session_id)
    results = []

    def case(case_id: str, action) -> None:
        """执行单项动作，收集断言结果及证据，使后续独立用例可以继续。"""
        try:
            driver.dismiss_login()
            detail = action()
            status = "PASS"
        except Exception as error:  # noqa: BLE001 - 单例失败后继续收集真机结果
            detail = f"{type(error).__name__}: {error}"
            status = "FAIL"
        image = driver.screenshot(case_id)
        results.append({"case_id": case_id, "status": status, "detail": detail, "screenshot": image})
        print(f"{case_id} {status}: {detail}", flush=True)

    def tabs() -> str:
        """历史iPhone检查依次切换Discover标签，返回实际页面说明。"""
        failures = []
        for name, x in (("Video", 195), ("Text", 310), ("All", 80)):
            driver.dismiss_login()
            driver.tap(x, 143)
            time.sleep(0.5)
            buttons = driver.visible(name, "XCUIElementTypeButton")
            if not buttons or buttons[0].attrib.get("value") != "1":
                modal = "登录弹窗" if driver.visible("Login, 网页对话框") else "无登录弹窗"
                failures.append(f"{name} 未选中，出现{modal}")
        driver.dismiss_login()
        if failures:
            raise AssertionError("；".join(failures))
        return "All / Video / Text 均可切换"

    def cards() -> str:
        """历史iPhone卡片用例：读取Say Hi控件数量并保存当前列表证据。"""
        assert_visible(driver, "Say Hi", "XCUIElementTypeButton")
        return "卡片存在且 Say Hi 按钮可见"

    def scroll() -> str:
        """连续滑动列表五次，核对滑动后页面仍可操作并保留证据。"""
        before = {
            node.attrib.get("name", "") for node in driver.tree().iter()
            if node.tag == "XCUIElementTypeStaticText" and node.attrib.get("visible") == "true"
        }
        for _ in range(5):
            driver.swipe_up()
            time.sleep(0.3)
        assert_visible(driver, "Discover", "XCUIElementTypeButton")
        after = {
            node.attrib.get("name", "") for node in driver.tree().iter()
            if node.tag == "XCUIElementTypeStaticText" and node.attrib.get("visible") == "true"
        }
        if before == after:
            raise AssertionError("连续上滑后可见列表内容未变化")
        return "连续上滑 5 次，可见列表内容变化且页面仍可操作"

    def login_action(button: str) -> str:
        """游客点击指定用户操作，核对是否被登录弹窗拦截。"""
        driver.dismiss_login()
        driver.click(button)
        time.sleep(0.4)
        names = {node.attrib.get("name", "") for node in driver.tree().iter()}
        if "Login, 网页对话框" not in names or not any("Google" in name for name in names):
            raise AssertionError("未出现包含 Google 的登录入口")
        driver.dismiss_login()
        return f"{button} 打开登录弹窗"

    def profile() -> str:
        """历史游客Profile检查：核对点击后出现登录弹窗。"""
        driver.dismiss_login()
        driver.click("Profile")
        time.sleep(0.4)
        names = {node.attrib.get("name", "") for node in driver.tree().iter()}
        if "Login, 网页对话框" not in names or not any("Google" in name for name in names):
            raise AssertionError("Profile 未显示登录入口")
        return "Profile 打开登录弹窗"

    def match_tabs() -> str:
        """依次点击Match顶部选项，核对各选项实际选中状态。"""
        driver.dismiss_login()
        driver.click("Match")
        time.sleep(0.4)
        driver.allow_site_permission()
        assert_visible(driver, "Start matching", "XCUIElementTypeStaticText")
        for name in ("men", "All", "women"):
            driver.click(name)
            if driver.allow_site_permission():
                driver.click(name)
            time.sleep(0.2)
            switches = driver.visible(name, "XCUIElementTypeSwitch")
            if not switches or switches[0].attrib.get("value") != "1":
                raise AssertionError(f"Match 筛选 {name} 未选中")
        remaining = [
            node.attrib.get("name", "") for node in driver.tree().iter()
            if node.tag == "XCUIElementTypeStaticText"
            and re.fullmatch(r"\d+ Free matches left", node.attrib.get("name", ""))
        ]
        state = remaining[0] if remaining else "Log in to continue."
        if not remaining and not driver.visible("Log in to continue."):
            raise AssertionError("Match 页未显示游客匹配状态")
        return f"women / men / All 可切换，当前{state}"

    def match_discover() -> str:
        """点击Match中间Discover入口，核对路由及来源列表恢复。"""
        driver.allow_site_permission()
        driver.click("Discover")
        time.sleep(0.4)
        assert_visible(driver, "Text", "XCUIElementTypeButton")
        return "Match 中间 Discover 跳转到 Discover 列表"

    driver.ensure_discover()
    case("G-03", tabs)
    case("G-04", cards)
    case("G-05", scroll)
    case("G-06", lambda: login_action("Say Hi"))
    case("G-07", lambda: login_action("Video"))
    case("G-17", profile)
    case("M-09", match_tabs)
    case("M-10", match_discover)

    OUT.mkdir(parents=True, exist_ok=True)
    report = OUT / "report.json"
    report.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "device": "connected iPhone / Chrome",
        "results": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report)
    return 1 if any(item["status"] == "FAIL" for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(run())
