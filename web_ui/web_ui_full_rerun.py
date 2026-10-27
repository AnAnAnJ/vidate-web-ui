#!/usr/bin/env python3
"""复用现有真机会话，独立保存本轮游客与已登录未订阅记录。"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

import web_ui_iphone_ui as base
import web_ui_member_runner as runner
import web_ui_member_suite as member
from web_ui_environment import ExecutionPolicy, EnvironmentExcluded, environment_plan, origin
from web_ui_mobile_driver import MobileSessionUnavailable
from web_ui_platform_cases import platform_pause_reason, disabled_case_reason
from web_ui_account_state import (
    ACTIVE_MEMBER_CASES, UNSUBSCRIBED_CASES, StateUnconfirmed, StateExcluded,
    Membership, membership_from_profile, require_case_state, test_membership_due,
)

ROOT = Path(__file__).resolve().parent
POINTER = ROOT.parent / 'work/current-ui-rerun.json'
# 新检出源码尚无本机运行记录时，允许离线查询用例及执行规则检查。
BATCH = Path(json.loads(POINTER.read_text())['batch']) if POINTER.is_file() else ROOT/'web_ui_artifacts/pending'
base.OUT = runner.OUT = member.OUT = BATCH
GUEST_CASES = [
    ('G-03', '游客 Discover', 'All / Video / Text 切换', ['以网页未登录游客打开 Discover', '依次切换 Video、Text、All，核对选中状态和页面可操作性'], ['三个标签均能切换，无异常弹窗或页面错误']),
    ('G-04', '游客 Discover', '用户卡片存在', ['打开游客 Discover', '检查用户卡片和操作入口'], ['存在用户卡片，可见用户信息、Say Hi 和 Video']),
    ('G-05', '游客 Discover', '下滑列表五次', ['在 Discover 连续向上滑动列表五次', '核对列表内容、末尾和导航'], ['五次滑动正常，列表可操作，数据用尽时展示末尾状态']),
    ('G-06', '游客 Discover', '卡片 Say Hi 触发登录', ['游客打开 Discover 用户列表', '点击任意用户卡片上的 Say Hi'], ['弹出登录弹窗']),
    ('G-07', '游客 Discover', '卡片 Video 触发登录', ['游客打开 Discover 用户列表', '点击任意用户卡片上的 Video'], ['弹出登录弹窗']),
    ('G-10', '游客 Match 权限', '拒绝音视频权限', ['进入 Match，触发浏览器音视频权限请求', '点击不允许'], ['显示需要开启权限的提示和 Try Again']),
    ('G-11', '游客 Match 权限', 'Try Again 重新请求权限', ['在拒绝后的提示中点击 Try Again'], ['页面刷新，再次显示权限请求']),
    ('G-12', '游客 Match 权限', '允许权限后的下落通知', ['在权限请求中点击允许', '立即观察 Chrome 顶部通知'], ['出现已授予摄像头和麦克风使用权的浏览器内置下落通知']),
    ('M-09', '游客 Match', '顶部三个标签切换', ['在 Match 依次切换 women、men、All'], ['对应选项被选中，页面可操作']),
    ('M-10', '游客 Match', '中间 Discover 跳转', ['在 Match 点击中间 Discover'], ['进入 Discover 列表']),
    ('M-11', '游客 Match', '启动匹配进入连接页', ['确认游客有可用匹配次数', '点击 Start matching 一次'], ['进入匹配连接页']),
    ('M-12', '游客 Match', '搜索中 Exit 与扣次', ['记录匹配前次数', '启动匹配后在搜索中点击 Exit', '检查返回和剩余次数'], ['返回 Match，剩余次数减一']),
    ('M-13', '游客通话', '发送简短英文问候', ['游客匹配成功进入通话', '输入简短英文问候并发送'], ['消息成功显示在通话聊天区']),
    ('M-14', '游客通话', '点击自己的小窗切换视频', ['游客匹配成功进入双视频通话', '记录两路视频位置，点击自己的小窗'], ['自己与对方视频的大小位置交换']),
    ('G-17', '游客 Profile', 'Profile 触发登录', ['游客点击 Profile'], ['弹出登录弹窗']),
]
runner.CATALOG.update({c[0]:c for c in GUEST_CASES})


class Suite(member.Suite):
    def __init__(self, session_id=None, environment=None, url=None, session_file=None):
        """加载共用执行器并绑定test/prod策略，已有结果不能跨环境或跨域名混用。"""
        super().__init__(session_id, session_file=session_file)
        selected = environment or self.data['context'].get('run_environment', 'test')
        previous = self.data['context'].get('run_environment', 'test')
        if self.data['results'] and selected != previous:
            raise ValueError('test/prod 必须使用独立批次，请指定新的 --batch-dir')
        self.policy = ExecutionPolicy.create(selected, url or (self.data['url'] if selected == previous else None))
        if self.data['results'] and origin(self.data['url']) != origin(self.policy.base_url):
            raise ValueError('更换被测网址必须使用新的独立批次')
        self.d.target_url = self.policy.base_url

    def run_case(self, case_id, steps, action, operation=None, *, check_membership=True):
        """执行一项用例并区分通过、产品断言失败、环境跳过和数据阻塞，立即保存结果。"""
        if disabled_case_reason(case_id):
            raise StateExcluded(f'{case_id}已在所有端停用：{disabled_case_reason(case_id)}')
        try:
            self.policy.require(operation or 'ui_case')
        except EnvironmentExcluded as error:
            self.record(case_id, 'SKIPPED_ENV', str(error), [], int(time.time()*1000), [])
            return 'SKIPPED_ENV'
        paused=platform_pause_reason(case_id,self.d.platform)
        if paused:
            self.record(case_id,'SKIPPED_STATE',paused,[],int(time.time()*1000),[])
            return 'SKIPPED_STATE'
        blocker=getattr(self,'data',{}).get('context',{}).get('session_blocker')
        if blocker:
            self.record(case_id,'BLOCKED',blocker['reason'],[],int(time.time()*1000),[])
            return 'BLOCKED'
        def checked_action():
            """在业务动作前刷新或复用近期会员证据，确认用例身份条件后才执行。"""
            self.d.assert_no_system_permission_dialog()
            if check_membership and case_id in ACTIVE_MEMBER_CASES | UNSUBSCRIBED_CASES:
                if case_id in {'L-10', 'L-28'}:
                    cached = self.data['context'].get('membership')
                    if not cached or time.time() - cached['checked_at'] > 30:
                        raise StateUnconfirmed('关闭弹窗用例缺少30秒内的会员前置证据；保留当前弹窗，不导航破坏测试现场')
                    state = Membership(**cached)
                else:
                    state = self.profile_membership(refresh=True, restore=True)
                state = self.prepare_case_membership(case_id, state)
                require_case_state(case_id, state, self.policy.environment)
            result = action()
            self.d.assert_no_system_permission_dialog()
            return result
        return super().run_case(case_id, steps, checked_action)

    def prepare_case_membership(self, case_id, state):
        """会员前置扩展点，默认返回原状态；到期恢复由完整套件覆写实现。"""
        return state

    def wait_account_details(self, timeout=15):
        """等待本页真实用户信息响应完成，避免把Profile默认占位值当真实身份或余额。"""
        end=time.monotonic()+timeout
        previous=None
        while time.monotonic()<end:
            snapshot=self.js("""const entries=performance.getEntriesByType('resource').filter(e=>new URL(e.name).pathname.endsWith('/accounts/details'));
                const last=entries.at(-1);return {timeOrigin:performance.timeOrigin,end:last?.responseEnd||0,httpStatus:last?.responseStatus||0,
                age:last?performance.now()-last.responseEnd:0,text:document.body?.innerText||''};""")
            # Appium会把顶层status误认作旧版协议状态码；业务HTTP状态使用独立字段。
            if snapshot['end'] and snapshot['httpStatus'] not in (0,200):
                raise StateUnconfirmed('用户信息接口返回HTTP'+str(snapshot['httpStatus'])+'，不能读取占位会员及余额。')
            stable=(snapshot['timeOrigin'],snapshot['end'],snapshot['text'])
            ready=snapshot['end']>0 and snapshot['age']>=300 and not re.search(r'Loading failed|Network error',snapshot['text'],re.I)
            if ready and stable==previous:return
            previous=stable if ready else None
            time.sleep(.15)
        raise StateUnconfirmed('用户信息接口尚未完成，不能把Profile默认未订阅和0余额判为实际数据')

    def profile_membership(self, refresh=True, restore=False):
        """刷新Profile识别会员并保存证据；restore为真时返回原来源页面。"""
        before = self.js('return location.href')
        profile_url = self.policy.base_url + 'lobby/profile'
        navigated = self.js('return location.pathname') != '/lobby/profile'
        if navigated:
            self.d.request('/url', {'url':profile_url})
        elif refresh:
            self.d.request('/refresh', {})
        try:
            self.wait_text('Edit Info')
            self.wait_text('Gems Store')
            self.wait_account_details()
            end = time.monotonic() + 10
            state = membership_from_profile(self.text())
            while state.state == 'unknown' and time.monotonic() < end:
                time.sleep(.2)
                state = membership_from_profile(self.text())
            evidence = self.web_capture('membership-' + str(int(time.time() * 1000)))
            context = self.data['context']
            context['membership'] = state.to_dict()
            payment_at = context.get('payment_confirmed_at')
            due = test_membership_due(self.policy.environment, payment_at, time.time())
            self.event('刷新Profile核对实际会员状态', **state.to_dict(),
                       test_five_minutes_elapsed=due, evidence=evidence)
        finally:
            if restore and navigated:
                self.d.request('/back', {})
                end = time.monotonic() + 15
                while time.monotonic() < end:
                    if self.js('return location.href') == before and self.js('return document.readyState') == 'complete':
                        break
                    time.sleep(.2)
                else:
                    raise StateUnconfirmed('会员核对后未恢复原来源页面，停止后续动作')
        return state

    def wait_login(self, timeout=180):
        """在操作 Google 登录前启动此阶段；以首次观察到登录资料为成功时点。"""
        deadline = time.monotonic() + timeout
        armed = False
        callback_at = None
        while time.monotonic() < deadline:
            try:
                self.web()
                body = self.text()
                if 'Continue with Google' in body:
                    armed = True
                if 'Edit Info' in body and 'Gems Store' in body:
                    if not armed:
                        raise StateUnconfirmed('启动时账号已登录；不能追溯登录后30秒的新老用户窗口')
                    login_at = callback_at or time.time()
                    if self.data['context'].get('login_classification'):
                        raise StateUnconfirmed('此批次已有登录分类，不能再次追溯旧登录')
                    result = self.observe_login_success(login_at)
                    self.profile_membership(refresh=True)
                    return result
                if 'Continue with Google' not in body and self.js("return !!Array.from(document.querySelectorAll('button')).find(e=>e.innerText.trim()==='Profile')"):
                    if armed and callback_at is None:
                        callback_at = time.time()
                    self.click_text('Profile')
            except StateUnconfirmed:
                raise
            except MobileSessionUnavailable:
                raise
            except (RuntimeError, AssertionError):
                self.data['context'].pop('web_context', None)
            time.sleep(.5)
        raise member.Blocked('未在等待时间内确认Google登录成功回站')

    def profile(self):
        """检查Profile中的登录身份和会员呈现，返回实际结果与证据。"""
        state = self.profile_membership(refresh=True)
        if state.state == 'unknown':
            raise StateUnconfirmed('已登录Profile会员状态无法识别')
        return 'Profile 已登录；实际会员状态：' + state.state + '；有效期：' + (state.active_until or '无'), self.web_capture('L-02-profile')

    def execute_operation(self, operation, action):
        # 清理、重置、后续支付与已订阅Video入口均经此处判断后再执行。
        """统一通过test/prod操作策略执行动作，返回动作结果。"""
        return self.policy.execute(operation, action)

    def video(self):
        """点击Discover卡片Video，未订阅时核对订阅拦截并保存证据。"""
        if self.policy.environment == 'prod':
            state = self.profile_membership(refresh=True, restore=True)
            require_case_state('L-09', state, self.policy.environment)
        return super().video()

    def web_capture(self, label):
        # 路由DOM先于手机画面更新，给入场动画留出时间再保存同页证据。
        """保存当前网页文本和可见控件，再采集原生截图，返回证据路径。"""
        time.sleep(.6)
        return super().web_capture(label)

    def wait_premium(self):
        # 本轮要求校验订阅弹窗，不依赖具体套餐按钮文案。
        """等待订阅面板及必要内容加载，避免将空壳弹窗视为成功。"""
        self.wait_css('[data-purchase-panel][aria-label="Get Premium"]')
        self.wait_text('Get Premium')

    def web(self):
        """选择当前批次的Chrome网页上下文，核对前台状态及网址后再执行后续动作。"""
        context = self.data['context'].get('web_context')
        page = {'context': context, 'window': self.data['context'].get('web_window')}
        if context:
            try:
                self.d.focus_page(page)
            except MobileSessionUnavailable:
                raise
            except RuntimeError:
                context = None
        if not context:
            page = self.d.select_page(self.policy.base_url)
            if not page:
                raise RuntimeError('手机中没有所选环境的被测网页')
            self.data['context']['web_context'] = page['context']
            self.data['context']['web_window'] = page.get('window')
            self.save()
        if self.js('return document.visibilityState') != 'visible':
            raise RuntimeError('被测网页不在手机前台')
        self.policy.verify_url(self.js('return location.href'))
        self.data['context']['run_environment'] = self.policy.environment
        self.data['url'] = self.policy.base_url
        self.save()

    def refresh_list(self):
        """真正刷新Discover，等待新页面及卡片加载完成，保留账号及Say Hi用量。"""
        if self.js('return location.pathname') != '/lobby/anchor':
            self.d.request('/url', {'url':self.policy.base_url+'lobby/anchor'})
            self.wait_css('body')
        before = self.js('return performance.timeOrigin')
        self.d.request('/refresh', {})
        end = time.monotonic()+30
        while time.monotonic() < end:
            state = self.js('return {timeOrigin:performance.timeOrigin,ready:document.readyState,body:!!document.body}')
            if state['timeOrigin'] != before and state['body'] and state['ready'] == 'complete':
                self.list_page()
                return
            time.sleep(.2)
        raise member.Blocked('刷新后30秒内未完成新页面加载')

    def quota_retest(self):
        """沿用本轮前五次Say Hi记录，先刷新Discover再测试第六次边界。"""
        self.refresh_list()
        self.event('第六次Say Hi补测', cleared_data=False, candidates=self.cards(True))
        return self.run_case('L-08', ['保留当前账号及前五次成功记录', '刷新Discover，等待新页面和列表加载完成', '对第六个不同用户点击Say Hi，核对订阅拦截'], self.quota)

    def dismiss_login(self):
        """仅在登录弹窗存在时关闭，并核对弹窗已消失。"""
        if self.js('return !!document.querySelector(\'[role="dialog"][aria-label="Login"]\')'):
            self.click_css('[role="dialog"][aria-label="Login"] button:has(use[href="#icon-common/close"])')
            self.wait_css_absent('[role="dialog"][aria-label="Login"]')

    def wait_css_absent(self, selector, timeout=10):
        """在timeout秒内等待指定CSS控件消失，超时抛出断言错误。"""
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            if not self.js('return !!document.querySelector(arguments[0])',selector):return
            time.sleep(.1)
        raise AssertionError('控件未消失：'+selector)

    def native_names(self):
        """读取当前原生页面可见名称，兼容iOS名称与Android文本、描述和资源ID。"""
        self.d.request('/context',{'name':'NATIVE_APP'})
        return self.d.names()

    def guest(self):
        """根据网页Connect入口确认未登录游客态，避免按浏览器无痕状态判断身份。"""
        self.dismiss_login()
        assert not self.js("return Array.from(document.querySelectorAll('button')).some(e=>e.innerText.trim()==='Connect')"),'当前仍为已登录状态'

    def tabs(self):
        """依次切换Discover三个标签并核对选中状态，返回结果和证据。"""
        self.guest();self.list_page()
        states=[];evidence=[]
        for label in ('Video','Text','All'):
            self.click_text(label)
            end=time.monotonic()+4
            while time.monotonic()<end:
                state=self.js("return Array.from(document.querySelectorAll('button')).find(e=>e.innerText.trim()===arguments[0])?.getAttribute('aria-selected')",label)
                if state=='true':break
                time.sleep(.1)
            assert state=='true', f'{label}未变为选中状态'
            assert not self.js('return !!document.querySelector(\'[role="dialog"][aria-label="Login"]\')')
            states.append(label+' 已选中')
            evidence+=self.web_capture('G-03-'+label)
        return '；'.join(states),evidence

    def card_case(self):
        """确认游客Discover存在用户卡片，返回当前卡片清单和证据。"""
        self.guest();self.list_page()
        return '游客用户卡片：'+json.dumps(self.cards(),ensure_ascii=False),self.web_capture('G-04-cards')

    def scroll_case(self):
        """真实滑动游客列表五次，比较滚动位置并核对卡片未异常消失。"""
        self.guest();self.list_page()
        before=self.js("return Array.from(document.querySelectorAll('*')).filter(e=>e.scrollHeight>e.clientHeight+50&&e.clientHeight>100).map(e=>({tag:e.tagName,top:e.scrollTop,height:e.scrollHeight}))")
        for _ in range(5):
            self.d.request('/context',{'name':'NATIVE_APP'});self.d.swipe_up()
            self.web()
        assert self.cards(),'滑动后卡片消失'
        after=self.js("return Array.from(document.querySelectorAll('*')).filter(e=>e.scrollHeight>e.clientHeight+50&&e.clientHeight>100).map(e=>({tag:e.tagName,top:e.scrollTop,height:e.scrollHeight}))")
        assert before!=after,'五次滑动后列表位置未变化'
        return '已连续滑动五次；前后滚动状态：'+json.dumps([before,after],ensure_ascii=False)+'；末尾：'+str('No more users' in self.text()),self.web_capture('G-05-scroll')

    def guest_action(self,label):
        """游客点击指定卡片动作，核对登录弹窗后恢复来源列表。"""
        self.guest();self.list_page();target=self.cards()[0]['name']
        self.click_css(member.VIDEO) if label=='Video' else self.click_text(label)
        self.wait_text('Continue with Google')
        result=(f'游客点击 {target} 卡片上的 {label} 后出现登录弹窗。',self.web_capture('G-06-sayhi' if label=='Say Hi' else 'G-07-video'))
        self.dismiss_login()
        return result

    def guest_profile(self):
        """游客点击Profile，核对登录弹窗和协议入口后关闭。"""
        self.guest();self.click_text('Profile');self.wait_text('Continue with Google')
        result=('游客点击 Profile 后出现登录弹窗，包含 Continue with Google 及协议入口。',self.web_capture('G-17-profile'))
        self.dismiss_login();return result

    def basic(self):
        """依次执行游客标签、卡片、五次滑动及登录拦截，不启动匹配。"""
        for case_id,action in [('G-03',self.tabs),('G-04',self.card_case),('G-05',self.scroll_case),('G-06',lambda:self.guest_action('Say Hi')),('G-07',lambda:self.guest_action('Video')),('G-17',self.guest_profile)]:
            self.run_case(case_id,runner.CATALOG[case_id][3],action)
            self.dismiss_login()

    def match_nav(self):
        """游客切换Match三个选项并检查中间Discover跳转，逐项保存结果。"""
        self.guest();self.click_text('Match');self.wait_text('Start matching')
        start=int(time.time()*1000);evidence=[]
        for label in ('women','men','All'):
            self.click_text(label)
            end=time.monotonic()+3
            while time.monotonic()<end:
                selected=self.js("return Array.from(document.querySelectorAll('button')).find(e=>e.innerText.trim()===arguments[0])?.getAttribute('aria-pressed')",label)
                if selected=='true':break
                time.sleep(.1)
            assert selected=='true',label+'未选中'
            evidence+=self.web_capture('M-09-'+label)
        self.record('M-09','PASS','women、men、All 的 aria-pressed 均按点击变为 true，页面可操作。',runner.CATALOG['M-09'][3],start,evidence)
        start=int(time.time()*1000);self.click_css('button[aria-label="Discover"]');self.wait_text('Say Hi')
        assert self.js('return location.pathname')=='/lobby/anchor'
        self.record('M-10','PASS','点击 Match 中间 Discover 后进入 /lobby/anchor 用户列表。',runner.CATALOG['M-10'][3],start,self.web_capture('M-10-discover'))
        self.click_text('Match');self.wait_text('Start matching')
        self.click_text('women')

    def remaining(self):
        """从当前页面提取剩余免费次数；没有可见次数时返回None。"""
        m=re.search(r'(\d+) Free matches left',self.text())
        return int(m[1]) if m else None

    def match_exit(self):
        """历史匹配退出检查，记录启动前后次数并核对Exit返回结果。"""
        self.guest();before=self.remaining()
        if not before:raise member.Blocked('游客无可用次数')
        start=int(time.time()*1000);self.click_label('Start matching');end=time.monotonic()+10
        while time.monotonic()<end:
            body=self.text()
            if 'Searching for you' in body or 'Exit' in body:break
            time.sleep(.05)
        evidence=self.web_capture('M-11-searching')
        self.record('M-11','PASS' if 'Searching for you' in body or 'Exit' in body else 'FAIL','点击 Start matching 前次数 '+str(before)+'；启动后的页面：'+body,runner.CATALOG['M-11'][3],start,evidence)
        start=int(time.time()*1000)
        if 'Exit' not in self.text():raise member.Blocked('未停留在可点击 Exit 的连接页面')
        self.click_label('Exit');self.wait_text('Start matching');after=self.remaining()
        end=time.monotonic()+4
        while after!=before-1 and time.monotonic()<end:
            time.sleep(.1);after=self.remaining()
        self.data['context']['guest_matching_starts']=1;self.save()
        self.record('M-12','PASS' if after==before-1 else 'FAIL',f'搜索中点击 Exit 后回到 Match；剩余次数 {before} -> {after}，预期 {before-1}。',runner.CATALOG['M-12'][3],start,self.web_capture('M-12-exit'))


def main():
    """解析命令行参数并执行所选阶段，将执行结果保存到对应批次。"""
    p=argparse.ArgumentParser();p.add_argument('phase',choices=['plan','inspect','agreements','await-login','membership','basic','matchnav','matchexit','memberbasic','details','sayhi','quota','report','block','calls']);p.add_argument('--target');p.add_argument('--single-target');p.add_argument('--env',choices=['test','prod']);p.add_argument('--url');p.add_argument('--batch-dir',type=Path);p.add_argument('--recheck-agreements',action='store_true');args=p.parse_args()
    if args.phase=='plan':
        print(json.dumps(environment_plan(args.env or 'test'),ensure_ascii=False,indent=2));return
    if args.batch_dir:
        global BATCH
        BATCH=args.batch_dir.resolve();BATCH.mkdir(parents=True,exist_ok=True)
        base.OUT=runner.OUT=member.OUT=BATCH
    s=Suite(environment=args.env,url=args.url)
    if args.phase=='await-login':
        s.wait_login();return
    s.web()
    if args.phase=='inspect':
        print(json.dumps({'text':s.text(),'url':s.js('return location.href'),'buttons':s.js("return Array.from(document.querySelectorAll('button')).map(e=>({text:e.innerText,html:e.outerHTML.slice(0,350)}))")},ensure_ascii=False,indent=2));return
    if args.phase=='agreements':s.agreements(force=args.recheck_agreements)
    elif args.phase=='membership':print(json.dumps(s.profile_membership().to_dict(),ensure_ascii=False))
    elif args.phase=='basic':s.basic()
    elif args.phase=='matchnav':s.match_nav()
    elif args.phase=='matchexit':s.match_exit()
    elif args.phase=='memberbasic':
        for c,a in [('L-02',s.profile),('L-03',s.discover),('L-09',s.video),('L-10',s.close_case),('L-11',lambda:s.masked('L-11'))]:s.run_case(c,runner.CATALOG[c][3],a)
    elif args.phase=='details':
        actions=[('L-12',lambda:s.detail_case(args.target)),('L-13',lambda:s.masked('L-13')),('L-14',s.menu),('L-15',s.block_dialog),('L-18',s.block_cancel),('L-20',s.carousel),('L-22',s.back_case)]
        for c,a in actions:s.run_case(c,runner.CATALOG[c][3],a)
        if args.single_target:
            s.enter(args.single_target);s.run_case('L-21',runner.CATALOG['L-21'][3],lambda:s.carousel(True))
    elif args.phase=='sayhi':
        s.sayhi()
        if len(s.data['context'].get('sayhi_completed_targets',[]))==5 and not s.cards(True):
            s.record('L-08','NOT_APPLICABLE','前五个不同用户已发送成功；列表显示 No more users，无第六个未打招呼用户，按用户指示跳过第六次订阅边界，不判定通过或失败。',runner.CATALOG['L-08'][3],int(time.time()*1000),s.web_capture('L-08-no-sixth'))
        else:s.run_case('L-08',runner.CATALOG['L-08'][3],s.quota)
    elif args.phase=='quota':s.quota_retest()
    elif args.phase=='report':
        s.enter(args.target)
        for c,a in [('L-16',s.report_reasons),('L-17',s.report_submit)]:s.run_case(c,runner.CATALOG[c][3],a)
    elif args.phase=='block':s.run_case('L-19',runner.CATALOG['L-19'][3],lambda:s.block_submit(args.target))
    elif args.phase=='calls':s.calls()


if __name__=='__main__':main()
