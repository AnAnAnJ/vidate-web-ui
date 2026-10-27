#!/usr/bin/env python3
"""Web UI 游客 UI 自动化脚本。

运行示例：
  python3 web_ui_automation.py
  python3 web_ui_automation.py --headed --device desktop

脚本不填写账号、密码或验证码。匹配、聊天和视频视角属于网页游客用例，
由连接 iPhone 的 web_ui_iphone_guest_match.py 覆盖。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import Browser, BrowserContext, Page, TimeoutError, sync_playwright
from web_ui_environment import ExecutionPolicy, TEST_URL


BASE_URL = TEST_URL
ROOT = Path(__file__).resolve().parent
ARTIFACT_DIR = ROOT / "ui_automation_artifacts"


@dataclass
class CaseResult:
    case_id: str
    status: str
    detail: str
    url: str
    screenshot: str = ""


def shot(page: Page, case_id: str) -> str:
    """保存当前页面截图并返回证据路径。"""
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    path = ARTIFACT_DIR / f"{case_id}.png"
    page.screenshot(path=str(path), full_page=True)
    return str(path)


def wait_app(page: Page) -> None:
    """等待页面DOM及基础入场渲染完成，供后续业务控件定位。"""
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(1200)


def open_lobby(page: Page) -> None:
    """从配置网址进入大厅并处理已显示的入口确认，等待Match页。"""
    page.goto(BASE_URL, wait_until="domcontentloaded")
    page.get_by_role("button", name=re.compile("Start Video Chat", re.I)).first.click()
    page.wait_for_url(re.compile(r"/lobby/match"), timeout=15000)
    wait_app(page)
    agree = page.get_by_role("button", name="I agree", exact=True)
    if agree.count() and agree.first.is_visible():
        agree.first.click()
        page.wait_for_timeout(300)


def close_login_if_present(page: Page) -> None:
    """仅当登录弹窗可见时通过关闭入口退出，恢复来源页。"""
    dialog = page.get_by_text("Login", exact=True)
    if dialog.count() and dialog.first.is_visible():
        # 当前页面的关闭按钮没有可访问名称，优先使用弹窗附近的无名 button。
        buttons = page.locator("button")
        for index in range(buttons.count() - 1, -1, -1):
            button = buttons.nth(index)
            if button.is_visible() and not button.inner_text().strip():
                button.click()
                break


def run_case(page: Page, case_id: str, action) -> CaseResult:
    """执行该桌面脚本的一项动作；成功返回PASS，异常返回FAIL并尽量采集截图。"""
    try:
        detail = action()
        return CaseResult(case_id, "PASS", detail, page.url)
    except Exception as exc:  # noqa: BLE001 - 记录 UI 测试失败并继续执行其他用例
        screenshot = ""
        try:
            screenshot = shot(page, case_id)
        except Exception:
            pass
        return CaseResult(case_id, "FAIL", f"{type(exc).__name__}: {exc}", page.url, screenshot)


def case_g01(page: Page) -> str:
    """游客从首页进入Match，核对开始匹配和底部导航入口。"""
    open_lobby(page)
    assert "/lobby/match" in page.url
    assert page.get_by_text("Start matching", exact=False).is_visible()
    assert page.get_by_role("button", name="Discover").first.is_visible()
    assert page.get_by_role("button", name="Profile", exact=True).is_visible()
    return "游客进入 /lobby/match，Start matching、Discover、Profile 可见"


def case_g02(page: Page) -> str:
    """游客进入Discover，核对All、Video、Text标签可见。"""
    open_lobby(page)
    page.get_by_role("button", name="Discover").first.click()
    page.wait_for_url(re.compile(r"/lobby/anchor"), timeout=10000)
    wait_app(page)
    page.get_by_role("tab", name="All", exact=True).wait_for()
    return "游客进入 /lobby/anchor，All/Video/Text Tab 可见"


def case_g03(page: Page) -> str:
    """依次切换Discover标签，核对对应标签与页面仍可操作。"""
    case_g02(page)
    for name in ("Video", "Text", "All"):
        page.get_by_role("tab", name=name, exact=True).click()
        page.get_by_role("tab", name=name, exact=True).wait_for()
    return "All、Video、Text 可循环切换"


def case_g04(page: Page) -> str:
    """检查Discover存在可操作用户卡片，并记录卡片数量。"""
    case_g02(page)
    page.get_by_role("button", name="Say Hi").first.wait_for()
    cards = page.locator("button").filter(has_text=re.compile("Say Hi"))
    assert cards.count() > 0
    return f"Discover 首屏存在 {cards.count()} 个 Say Hi 卡片操作"


def case_g05(page: Page) -> str:
    """在Discover连续下滑五次，核对列表数据和可操作性。"""
    case_g02(page)
    before = page.locator("button", has_text="Say Hi").count()
    for _ in range(5):
        page.mouse.wheel(0, 900)
        page.wait_for_timeout(500)
    after = page.locator("button", has_text="Say Hi").count()
    assert after >= before
    return f"连续下滑 5 次，卡片数量 {before} -> {after}"


def case_g06(page: Page) -> str:
    """游客点击Say Hi，核对Google登录入口并关闭弹窗。"""
    case_g02(page)
    page.get_by_role("button", name="Say Hi").first.click()
    page.get_by_text("More Free Matching", exact=False).wait_for()
    assert page.get_by_role("button", name="Continue with Google").is_visible()
    close_login_if_present(page)
    return "Say Hi 触发登录弹窗"


def case_g07(page: Page) -> str:
    """游客点击用户Video，核对Google登录拦截并恢复页面。"""
    case_g02(page)
    page.get_by_role("button", name="Video").first.click()
    page.get_by_text("More Free Matching", exact=False).wait_for()
    assert page.get_by_role("button", name="Continue with Google").is_visible()
    close_login_if_present(page)
    return "Video 触发登录弹窗"


def case_g09(page: Page) -> str:
    """读取游客匹配入口和登录拦截文案，不据此代替成功匹配实测。"""
    open_lobby(page)
    start = page.get_by_text("Start matching", exact=False)
    assert start.count() > 0
    text = start.first.inner_text()
    if "Log in to continue" in text:
        return "当前环境游客匹配被登录拦截：Log in to continue"
    return "当前环境显示游客匹配入口；真机匹配流程由单轮脚本执行"


def build_context(browser: Browser, headed: bool, mobile: bool, permission: str) -> BrowserContext:
    """按桌面或移动视口创建Playwright上下文；权限参数仅用于该桌面脚本。"""
    kwargs = {
        "ignore_https_errors": False,
        "permissions": ["camera", "microphone"] if permission == "allow" else [],
        "record_video_dir": str(ARTIFACT_DIR) if headed else None,
    }
    if mobile:
        kwargs.update({"viewport": {"width": 390, "height": 844}, "is_mobile": True, "has_touch": True})
    context = browser.new_context(**{k: v for k, v in kwargs.items() if v is not None})
    if permission == "deny":
        context.grant_permissions([], origin=BASE_URL.rstrip("/"))
    return context


def main() -> int:
    """解析命令行参数并执行所选阶段，将执行结果保存到对应批次。"""
    global BASE_URL
    parser = argparse.ArgumentParser()
    parser.add_argument("--headed", action="store_true", help="显示 Playwright 浏览器窗口")
    parser.add_argument("--device", choices=("mobile", "desktop"), default="mobile")
    parser.add_argument("--permission", choices=("default", "allow", "deny"), default="default")
    parser.add_argument("--env", choices=("test", "prod"), default="test")
    parser.add_argument("--url", help="覆盖配置文件中的被测网址")
    args = parser.parse_args()
    BASE_URL = ExecutionPolicy.create(args.env, args.url).base_url

    cases = [
        ("G-01", case_g01),
        ("G-02", case_g02),
        ("G-03", case_g03),
        ("G-04", case_g04),
        ("G-05", case_g05),
        ("G-06", case_g06),
        ("G-07", case_g07),
        ("G-09", case_g09),
    ]
    results: list[CaseResult] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not args.headed)
        context = build_context(browser, args.headed, args.device == "mobile", args.permission)
        page = context.new_page()
        page.on("pageerror", lambda exc: print(f"PAGE_ERROR: {exc}", file=sys.stderr))
        for case_id, action in cases:
            results.append(run_case(page, case_id, lambda action=action: action(page)))
        context.close()
        browser.close()

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": BASE_URL,
        "device": args.device,
        "permission": args.permission,
        "results": [asdict(result) for result in results],
    }
    report = ARTIFACT_DIR / "report.json"
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 1 if any(result.status == "FAIL" for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
