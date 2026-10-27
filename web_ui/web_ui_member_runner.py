#!/usr/bin/env python3
"""iPhone Chrome 增量执行器；结果即时落盘，失败或缺失前置条件保留真实状态。

复用现有 Appium/WDA 会话，不包含账号、密码，不重建签名环境。
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import web_ui_iphone_ui as base
from web_ui_member_cases import CASES, write_markdown
from web_ui_environment import origin, TEST_URL
from web_ui_account_state import classify_login_window
from web_ui_mobile_driver import MobileDriver
from web_ui_execution_state import atomic_json

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "iphone_ui_artifacts" / "member_2026_10_19"
OUT.mkdir(parents=True, exist_ok=True)
base.OUT = OUT
CATALOG = {item[0]: item for item in CASES}


class Runner:
    def __init__(self, session_id=None, session_file=None):
        """加载指定会话及独立结果文件，读取真实设备资料并阻止跨平台或跨手机覆盖结果。"""
        connection = {}
        if session_id is None:
            session_path = Path(session_file) if session_file else ROOT.parent / "work/current-appium-session.json"
            connection = json.loads(session_path.read_text())
            session_id = connection['session_id']
        self.d = MobileDriver(session_id, endpoint=connection.get('appium_url', base.BASE), output_dir=OUT)
        self.path = OUT / "report.json"
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {
            "environment": "移动设备 / 普通 Chrome / 已登录未订阅",
            "url": TEST_URL,
            "results": {}, "events": [], "context": {},
        }
        device = self.d.device_info()
        connected=connection.get('device',{})
        if connected.get('udid') and connected['udid']==device.get('udid') and connected.get('platform')==device.get('platform'):
            for key in ('name','version','chrome_version'):
                if not device.get(key) and connected.get(key):device[key]=connected[key]
        previous = self.data.get('device')
        if previous and any(previous.get(key) and device.get(key) and previous[key] != device[key]
                            for key in ('platform', 'udid')):
            raise ValueError('更换平台或手机必须使用独立 --batch-dir，不能覆盖已有真机结果')
        if not previous and self.data['results'] and 'iPhone' in self.data.get('environment', '') and device['platform'] != 'ios':
            raise ValueError('该批次已有 iPhone 结果，Android 必须使用独立 --batch-dir')
        self.data['device'] = device

    def save(self):
        """把当前批次结果原样写入report.json，避免中断后丢失已确认步骤。"""
        atomic_json(self.path,self.data)

    def event(self, message, **fields):
        """追加带时间戳的审计事件并即时落盘、输出，供报告追踪动作依据。"""
        item = {"time": int(time.time() * 1000), "message": message, **fields}
        self.data["events"].append(item)
        self.save()
        print(json.dumps(item, ensure_ascii=False), flush=True)

    def tree(self):
        """读取当前原生页面XML树，供控件定位及现场证据使用。"""
        return self.d.tree()

    def web(self):
        """选择当前批次的Chrome网页上下文，核对前台状态及网址后再执行后续动作。"""
        context = self.data["context"].get("web_context")
        if not context:
            page = self.d.select_page(self.data['url'])
            if not page:
                raise RuntimeError('未找到前台Chrome被测页面')
            context = page['context']
            self.data["context"]["web_context"] = context
            self.data["context"]["web_window"] = page.get('window')
            self.save()
        self.d.focus_page({'context':context, 'window':self.data['context'].get('web_window')})
        if self.js("return document.visibilityState") != "visible":
            raise RuntimeError("被测网页在后台；请在 Chrome 前台选中对应标签页再执行")
        if origin(self.js('return location.href')) != origin(self.data['url']):
            raise RuntimeError('当前Chrome标签页不属于被测域名，停止执行')

    def js(self, script, *args):
        """在当前网页执行WebDriver脚本，参数通过结构化args传入。"""
        return self.d.request("/execute/sync", {"script":script,"args":list(args)})

    def text(self):
        """读取当前页面可见正文，供断言使用，不读取账号密码或令牌。"""
        return self.js("return document.body?.innerText || ''")

    def wait_text(self, text, timeout=12):
        """在timeout秒内等待预期文案出现，超时附当前页面摘要便于排查。"""
        end = time.monotonic()+timeout
        while time.monotonic()<end:
            body = self.text()
            if text in body:
                return body
            time.sleep(.2)
        raise AssertionError(f"等待页面文案超时：{text}；实际：{self.text()[:1000]}")

    def wait_absent(self, text, timeout=10):
        """在timeout秒内等待指定文案消失，避免关闭动作尚未完成就继续。"""
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            if text not in self.text():
                return
            time.sleep(.15)
        raise AssertionError(f"等待文案消失超时：{text}")

    def find_css(self, selector):
        """通过CSS定位网页元素并返回W3C元素ID，供真实点击或输入。"""
        return self.d.request("/element", {"using":"css selector","value":selector})[base.ELEMENT_KEY]

    def click_css(self, selector):
        """选择CSS匹配中的可见元素并执行WebDriver点击，不调用业务接口。"""
        self.d.request(f"/element/{self.find_css(selector)}/click", {})

    def wait_css(self, selector, timeout=12):
        """轮询等待CSS控件出现，超时抛异常，不把未加载当作已通过。"""
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            if self.js("return !!document.querySelector(arguments[0])", selector):
                return
            time.sleep(.15)
        raise AssertionError("等待控件超时："+selector)

    def click_text(self, text):
        # XPath 文本定位执行真实 WebDriver 点击，不调用业务接口。
        """按按钮完整文本定位并执行WebDriver点击，避免直接改变页面业务状态。"""
        value = json.dumps(text, ensure_ascii=False)
        element = self.d.request("/element", {"using":"xpath", "value":f"//button[normalize-space(.)={value}]"})[base.ELEMENT_KEY]
        self.d.request(f"/element/{element}/click", {})

    def click_label(self, text):
        """按可见完整标签文本定位元素并点击，缺失时报告明确错误。"""
        element = self.js("""let es=Array.from(document.querySelectorAll('body *')).filter(e=>e.getBoundingClientRect().height>0 && e.textContent.trim()===arguments[0]);return es.filter(e=>{let r=e.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight}).at(-1)??es[0]""", text)
        if not element:
            raise AssertionError(f"未找到可见标签：{text}")
        self.d.request(f"/element/{element[base.ELEMENT_KEY]}/click", {})

    def poll_incoming(self, body=None):
        """处理当前已出现的模拟来电，先取消再接听，并按真实登录分类记录结果。"""
        body = self.text() if body is None else body
        context = self.data['context']
        if not re.search(r'inviting you to (?:a )?private call|incoming (?:video |private )?call', body, re.I):
            context['incoming_popup_open'] = False
            return False
        labels = self.js("return Array.from(document.querySelectorAll('button')).filter(e=>e.getBoundingClientRect().height>0).map(e=>e.textContent.trim())")
        cancel = next((t for t in ('Later','Cancel','Decline','Reject') if t in labels), None)
        answer = next((t for t in ('Join','Answer','Accept') if t in labels), None)
        if not cancel or not answer:
            return False
        events = context.setdefault('incoming_events', [])
        pending = next((e for e in events if e.get('pending_answer')), None)
        classification = context.get('login_classification', {})
        if context.get('incoming_popup_open') and not (pending and classification.get('state') == 'new'):
            return True
        context['incoming_popup_open'] = True
        state = context.get("incoming_cancelled", False)
        classification = self.data['context'].get('login_classification', {})
        login_at = classification.get('login_at')
        confirmed_new = classification.get('state') == 'new'
        action = answer if state else cancel
        start = int(time.time()*1000)
        label = "L-05" if state else "L-04"
        item = pending or {'seen_at':time.time(), 'evidence':self.web_capture(label+'-incoming'), 'action':None}
        if not pending:
            events.append(item)
        evidence = item['evidence']
        if state and not confirmed_new:
            item['pending_answer'] = True
            self.save()
            return True
        item['pending_answer'] = False
        item['action'] = 'answer' if state else 'cancel'
        item['clicked_at'] = time.time()
        self.save()
        self.click_label(action)
        self.event("模拟来电操作", action=action, body=body[-600:])
        if not state:
            time.sleep(.3)
            evidence += self.web_capture(label+"-after-cancel")
            closed = "is inviting you to private call." not in self.text()
            self.data["context"]["incoming_cancelled"] = closed
            context['incoming_popup_open'] = not closed
            item['closed'] = closed
            self.save()
            in_time = confirmed_new and login_at is not None and start / 1000 - login_at <= 180
            self.record("L-04", "PASS" if in_time and closed else "BLOCKED",
                        "观察到来电并点击 Later，取消后消息"+("已关闭。" if closed else "仍存在。")+
                        (f'Chat 官方消息证据确认新用户；登录后 {start / 1000 - login_at:.1f} 秒收到来电。' if in_time else '未覆盖有效的新用户判定或登录后三分钟窗口，不能判时限通过。'),
                        ['核对登录后30秒 Chat 官方消息分类证据', "在前台捕获模拟来电", "点击 Later 并核对关闭结果"], start, evidence)
        else:
            end=time.monotonic()+12
            while time.monotonic()<end:
                body=self.text()
                if "is inviting you to private call." not in body:
                    break
                time.sleep(.2)
            media=[]
            end=time.monotonic()+30
            while time.monotonic()<end:
                media=self.js("return Array.from(document.querySelectorAll('video')).filter(e=>e.getBoundingClientRect().height>20).map(e=>({readyState:e.readyState,paused:e.paused,width:e.videoWidth,height:e.videoHeight}))")
                if any(x["readyState"]>=2 for x in media):
                    break
                time.sleep(.2)
            evidence+=self.web_capture("L-05-after-join")
            self.data["context"]["incoming_joined"] = True
            self.data["context"]["after_join_text"] = self.text()
            self.save()
            self.event("接听后的页面", body=self.text())
            connected=any(x["readyState"]>=2 for x in media)
            item.update(closed='is inviting you to private call.' not in self.text(), connected=connected, media=media)
            context['incoming_popup_open'] = not item['closed']
            self.save()
            self.record("L-05", "PASS" if connected and confirmed_new else "BLOCKED",
                        "首次取消后第二次来电点击 Join；可见视频控件状态："+json.dumps(media,ensure_ascii=False)+("；已展示通话画面。" if connected else "；未观察到已加载视频画面，需复核连接状态。")+('' if confirmed_new else '缺少本次登录后30秒的Chat官方消息新用户证据。'),
                        ["首次来电点击 Later 取消", "再次来电点击 Join", "等待并核对可见通话视频画面"],start,evidence)
        return True

    def reconcile_incoming(self):
        """身份窗口完成后按已捕获的早期来电证据核验，不重复拒接或追溯登录时间。"""
        context = self.data['context']
        classification = context.get('login_classification', {})
        if classification.get('state') != 'new':
            return
        login_at = classification['login_at']
        for item in context.get('incoming_events', []):
            if not login_at <= item['seen_at'] <= login_at+180:
                continue
            action = item.get('action')
            success = item.get('closed') and (action == 'cancel' or item.get('connected'))
            if action not in ('cancel','answer') or not success:
                continue
            case_id = 'L-04' if action == 'cancel' else 'L-05'
            if self.data['results'].get(case_id, {}).get('status') == 'PASS':
                continue
            self.record(case_id, 'PASS',
                        f'本次登录后{item["seen_at"]-login_at:.1f}秒已捕获来电；先保存弹窗证据再'+('取消并确认关闭' if action=='cancel' else '接听并确认加载视频')+'；30秒Chat窗口取得官方消息新用户证据。',
                        ['登录观察同时捕获来电弹窗并保存截图','按首次取消、再次接听顺序操作','结合本次30秒Chat证据核验身份和时限'],
                        int(item['seen_at']*1000), item['evidence']+classification.get('evidence', []))

    def observe_login_success(self, login_at):
        """仅在实际登录回调确认后立即调用，不用当前页面追溯旧登录。"""
        if time.time() - login_at >= 30:
            raise RuntimeError('登录后30秒窗口已错过，不能追溯判定新老用户')
        context = self.data['context']
        context['login_classification'] = {'state':'unknown','login_at':login_at}
        self.save()
        observations = []
        if self.d.platform == 'android':
            # Android原生树可能不导出WebView边界，使用已核对的网页按钮执行真实点击。
            self.click_text('Connect')
        elif hasattr(self, 'tap_current_label'):
            self.tap_current_label('Connect')
        else:
            self.click_text('Connect')
        self.wait_text('Chats')
        if '/lobby/sessions' != self.js('return location.pathname'):
            raise RuntimeError('未进入 Chat / Connect 页面，不能判定老用户')
        evidence = self.web_capture('login-chat-start')
        window_valid = True
        while True:
            body = self.text()
            now = time.time()
            visible = self.js('return document.visibilityState') == 'visible'
            on_chat = self.js('return location.pathname') == '/lobby/sessions'
            healthy = visible and on_chat and not re.search(r'Loading failed|Please try again|Network error', body, re.I)
            window_valid = window_valid and healthy
            if healthy:
                observations.append((now, body))
            state = classify_login_window(login_at, observations, now, False)
            # Chat判定期间同时处理来电，避免前30秒弹窗被分阶段观察遗漏。
            if re.search(r'inviting you to (?:a )?private call|incoming (?:video |private )?call', body, re.I):
                self.poll_incoming(body)
            if state == 'new':
                break
            if now >= login_at + 30:
                state = classify_login_window(login_at, observations, now, window_valid)
                break
            time.sleep(min(.4, login_at + 30 - now))
        evidence += self.web_capture('login-chat-classified')
        result = {'state':state,'login_at':login_at,'observed_until':now,
                  'chat_path':self.js('return location.pathname'),'chat_window_valid':window_valid,'evidence':evidence}
        context['login_classification'] = result
        self.reconcile_incoming()
        self.event('按登录后30秒Chat的teamoffice消息或Official非零角标分类', **result)
        return result

    def calls(self):
        """依据本次登录Chat分类观察来电；老用户跳过新用户项，身份未知时阻塞。"""
        self.web()
        classification = self.data['context'].get('login_classification', {})
        if classification.get('state') not in ('new', 'existing'):
            self.event('来电用例阻塞', reason='缺少实际登录后30秒Chat的teamoffice分类证据；不追溯当前账号')
            return
        if classification['state'] == 'existing':
            for cid in ('L-04', 'L-05'):
                self.record(cid, 'SKIPPED_STATE', '登录后30秒Chat无teamoffice消息，按老用户跳过新注册欢迎来电。',
                            ['核对本次登录的30秒Chat分类证据'], int(time.time()*1000), classification.get('evidence', []))
            return
        start=time.monotonic()
        while time.monotonic()-start<180:
            self.poll_incoming()
            if self.data["context"].get("incoming_joined"):
                break
            time.sleep(.25)
        self.event("来电观察结束", seconds=round(time.monotonic()-start,1))

    def web_capture(self, label):
        """保存当前网页文本和可见控件，再采集原生截图，返回证据路径。"""
        if (OUT/(label+'-ui.json')).exists() or (OUT/(label+'.png')).exists():
            label+='-'+str(time.time_ns())
        data = self.js("""return {url:location.href,text:document.body.innerText,controls:Array.from(document.querySelectorAll('button,a,[role=dialog],input')).filter(e=>e.getBoundingClientRect().height>0).map(e=>({tag:e.tagName,text:e.innerText,aria:e.getAttribute('aria-label'),role:e.getAttribute('role')}))}""")
        path = OUT / (label+"-ui.json")
        path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
        self.d.request("/context", {"name":"NATIVE_APP"})
        image = self.d.screenshot(label)
        self.web()
        return [image,str(path)]

    def nodes(self, tree=None):
        """从给定或当前原生XML树提取可见节点，供证据和定位共用。"""
        return [n for n in (tree if tree is not None else self.tree()).iter() if n.attrib.get("visible") == "true"]

    def names(self, tree=None):
        """读取可见原生控件名称集合，供平台提示和页面状态判断使用。"""
        return {n.attrib.get("name", "") for n in self.nodes(tree)}

    def inspect(self):
        """输出可见控件的名称、值和边界，供首次设备适配时查看。"""
        for n in self.nodes():
            if n.attrib.get("name") or n.attrib.get("value"):
                print(n.tag, {k:n.attrib[k] for k in ("name", "value", "x", "y", "width", "height") if k in n.attrib})

    def capture(self, label):
        """保存当前原生截图及XML；该基础方法的调用方负责切换和恢复上下文。"""
        image = self.d.screenshot(label)
        xml = OUT / f"{label}.xml"
        xml.write_text(ET.tostring(self.tree(), encoding="unicode"), encoding="utf-8")
        return [image, str(xml)]

    def switch_site(self):
        # 通过配置网址选择上下文，不依赖网站标题。
        """清除缓存的上下文和窗口标识，重新发现被测站点页面。"""
        self.data['context'].pop('web_context', None)
        self.data['context'].pop('web_window', None)
        self.web()

    def record(self, case_id, status, actual, steps, start, evidence=None):
        """保存单项真实结果、执行步骤和证据，已有结果作为历史事件保留。"""
        item = CATALOG[case_id]
        previous = self.data['results'].get(case_id)
        if previous:
            self.data['events'].append({'time': int(time.time()*1000), 'message': '用例重新执行，保留前次记录', 'previous_result': previous})
        result = {"case_id": case_id, "module": item[1], "title": item[2], "status": status,
                  "planned_steps": item[3], "expected": item[4], "executed_steps": steps,
                  "actual": actual, "start": start, "stop": int(time.time()*1000), "evidence": evidence or []}
        self.data["results"][case_id] = result
        self.save()
        print(f"{case_id} {status}: {actual}", flush=True)

    def agreements(self, force=False):
        """同域名协议成功后复用来源证据；force或缺少有效证据时才重新打开核对。"""
        site = origin(self.data['url'])
        cache_path = ROOT / 'web_ui_agreement_cache.json'
        cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
        local = self.data['results'].get('L-01', {})
        verified = local if local.get('status') == 'PASS' and local.get('verification_mode') != 'reused' else cache.get(site, {})
        if (not force and verified.get('status') == 'PASS' and verified.get('evidence')
                and all(Path(name).is_file() for name in verified['evidence'])):
            if local.get('status') != 'PASS':
                result = copy.deepcopy(verified)
                result['verification_mode'] = 'reused'
                result['source_start'] = verified.get('start', verified['stop'])
                result['source_stop'] = verified['stop']
                result['start'] = result['stop'] = int(time.time()*1000)
                result['executed_steps'] = ['读取同域名已通过的两份协议校验成果，确认来源证据存在；本轮未重新打开协议']
                result['actual'] = f"复用 {site} 已通过的协议校验成果，原检查时间戳 {verified['stop']}；本次未重新打开协议。" + verified['actual']
                self.data['results']['L-01'] = result
            cache[site] = verified
            cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2))
            self.event('协议校验成果复用', origin=site, source_stop=verified['stop'], reopened=False)
            return self.data['results']['L-01']
        start = int(time.time()*1000)
        self.d.dismiss_login()
        self.d.click("Profile")
        time.sleep(.4)
        evidence = self.capture("L-01-login")
        found = []
        for label in ("Terms of Service", "Privacy Policy"):
            self.d.click(label)
            time.sleep(.6)
            tree = self.tree()
            # Chrome 的长协议页可视性标记有误，结合正文节点及截图判断。
            texts = [n.attrib.get("name", "") for n in tree.iter() if n.tag == "XCUIElementTypeStaticText"]
            evidence += self.capture("L-01-" + label.replace(" ", "-"))
            found.append({"agreement": label, "texts": texts})
            self.event("打开协议", agreement=label, texts=texts)
            self.switch_site()
            if "Login, 网页对话框" not in self.names():
                self.d.click("Profile")
        nonempty = all(len(" ".join(x["texts"])) > 150 for x in found)
        self.record("L-01", "PASS" if nonempty else "FAIL", json.dumps(found, ensure_ascii=False),
                    ["关闭已有登录窗，从 Profile 重新打开登录弹窗", "依次打开 Terms of Service 与 Privacy Policy，读取正文并用浏览器返回"], start, evidence)
        if nonempty:
            cache[site] = self.data['results']['L-01']
            cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2))
        return self.data['results']['L-01']


def main():
    """解析命令行参数并执行所选阶段，将执行结果保存到对应批次。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["design", "inspect", "agreements", "calls"])
    parser.add_argument('--recheck-agreements', action='store_true')
    args = parser.parse_args()
    if args.phase == "design":
        write_markdown(ROOT / "WebUI_已登录未订阅_测试用例.md")
        print(f"设计用例：{len(CASES)}")
        return
    runner = Runner()
    if args.phase == 'agreements':
        runner.agreements(force=args.recheck_agreements)
    else:
        getattr(runner, args.phase)()


if __name__ == "__main__":
    main()
