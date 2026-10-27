#!/usr/bin/env python3
"""已登录增量真机执行；不清数据，订阅项在未订阅项处理后执行。"""
from __future__ import annotations

import argparse
import json
import random
import re
import time
from datetime import datetime
from pathlib import Path

import web_ui_full_rerun as full
import web_ui_iphone_ui as base
import web_ui_member_runner as runner
import web_ui_member_suite as member
from web_ui_extended_cases import NEW_CASES, EXISTING_CASES, PAYMENT_IDS, SUBSCRIBED_IDS, VIDEO_IDS
from web_ui_account_state import StateUnconfirmed, StateExcluded, require_case_state, test_membership_due
from web_ui_platform_cases import disabled_case_reason
from web_ui_environment import EnvironmentExcluded

ROOT = Path(__file__).resolve().parent
CATALOG = {c[0]: c for c in NEW_CASES}
runner.CATALOG.update(CATALOG)
POINTER = ROOT.parent / 'work/current-extended-rerun.json'


def configure_batch(path=None, update_pointer=True):
    """设置本轮证据目录；update_pointer控制是否更新原有批次指针。"""
    if path is None:
        if POINTER.exists():
            path = Path(json.loads(POINTER.read_text())['batch'])
        else:
            path = ROOT / 'iphone_ui_artifacts' / ('member_extended_' + datetime.now().strftime('%Y_%m_%d_%H%M%S'))
    path = Path(path).resolve()
    path.mkdir(parents=True, exist_ok=True)
    base.OUT = runner.OUT = member.OUT = full.BATCH = path
    if update_pointer:
        POINTER.write_text(json.dumps({'batch': str(path)}))
    return path


class Suite(full.Suite):
    def __init__(self, session_id=None, environment='test', url=None, session_file=None):
        """初始化共用套件，按真实设备生成环境描述，并保留完整复测或补测模式。"""
        super().__init__(session_id, environment, url, session_file=session_file)
        self.data['context'].setdefault('incremental_without_reset', True)
        self.set_execution_mode(self.data['context']['incremental_without_reset'])

    def set_execution_mode(self, incremental):
        """设置完整复测或沿用数据补测；已有结果不能中途换模式，并同步报告环境描述。"""
        previous=self.data['context'].get('incremental_without_reset')
        if self.data['results'] and previous is not None and previous!=incremental:
            raise ValueError('批次已有结果，不能在完整复测和沿用数据补测之间切换')
        self.data['context']['incremental_without_reset']=incremental
        mode='增量补测' if incremental else '全量复测'
        device=self.data['device']
        self.data['environment']=f"{device['name']} / {device['platform']} {device['version']} / 普通 Chrome / {mode}"
        self.save()

    def run_new(self, case_id, action):
        """按新增编号执行用例，先检查订阅阶段顺序及支付或Video环境限制。"""
        if disabled_case_reason(case_id):
            raise StateExcluded(f'{case_id}已在所有端停用：{disabled_case_reason(case_id)}')
        if case_id in SUBSCRIBED_IDS:
            required = NEW_CASES
            if self.data.get('context', {}).get('incremental_without_reset', True) is False:
                required = full.GUEST_CASES + EXISTING_CASES + NEW_CASES
            selected = self.data.get('context', {}).get('selected_case_ids')
            if selected:
                required = [case for case in required if case[0] in selected]
            missing = [c[0] for c in required if c[0] not in SUBSCRIBED_IDS and c[0] not in self.data['results']]
            if missing:
                raise RuntimeError('未标记用例尚未处理：' + ', '.join(missing))
        operation = 'subscription_payment' if case_id in PAYMENT_IDS else 'subscribed_video' if case_id in VIDEO_IDS else 'ui_case'
        return self.run_case(case_id, CATALOG[case_id][3], action, operation=operation)

    def click_css(self, selector):
        """选择CSS匹配中的可见元素并执行WebDriver点击，不调用业务接口。"""
        element=self.js("return Array.from(document.querySelectorAll(arguments[0])).find(e=>e.getBoundingClientRect().height>0&&e.getBoundingClientRect().width>0&&getComputedStyle(e).visibility!=='hidden')",selector)
        if not element:raise AssertionError('未找到可见控件：'+selector)
        self.d.request(f'/element/{element[base.ELEMENT_KEY]}/click',{})

    def wait_premium(self):
        """等待订阅面板及必要内容加载，避免将空壳弹窗视为成功。"""
        self.wait_text('Get Premium')
        self.wait_css('[data-premium-content]')

    def close_premium(self):
        """使用当前订阅页面的关闭或返回入口退出，等待订阅内容消失。"""
        if self.js("return !!document.querySelector('[data-purchase-panel]')"):
            self.click_css(member.CLOSE)
        else:self.click_css(member.BACK)
        self.wait_absent('Get Premium')

    def profile_page(self):
        """从当前业务页面返回Profile，并等待资料及金币入口可用。"""
        if self.js("return !!document.querySelector('[data-purchase-panel]')"):
            self.close_premium()
        if self.d.platform=='android' and self.js('return location.pathname')!='/lobby/profile':
            self.d.request('/url',{'url':self.policy.base_url+'lobby/profile'})
            self.wait_text('Gems Store')
            return
        if self.js('return location.pathname') != '/lobby/profile':
            if self.js("return !!Array.from(document.querySelectorAll('button')).find(e=>e.innerText.trim()==='Profile')"):
                self.click_text('Profile')
            elif self.js("return !!document.querySelector('.custom-page-header-back,button:has(use[href=\"#icon-common/back\"])')"):
                self.click_css('.custom-page-header-back,'+member.BACK)
                self.wait_text('Edit Info')
        self.wait_text('Edit Info')
        self.wait_text('Gems Store')

    def balance(self):
        """从Profile可见金币入口解析唯一余额，格式不明确时阻塞，不猜测数值。"""
        self.wait_account_details()
        text = self.js("return Array.from(document.querySelectorAll('button')).find(e=>e.textContent.includes('Gems Store'))?.innerText")
        if not text:
            raise member.Blocked('Profile 金币入口未加载，无法读取余额')
        numbers = re.findall(r'(?<![a-zA-Z])(\d[\d,]*)(?![a-zA-Z])', text)
        if len(numbers) != 1:
            raise member.Blocked('金币入口余额格式未识别：' + text)
        return int(numbers[0].replace(',', ''))

    def require_unsubscribed(self):
        """刷新Profile并确认未订阅，身份不符时禁止执行相关拦截用例。"""
        state = self.profile_membership(refresh=True)
        require_case_state('N-10', state, self.policy.environment)

    def require_subscribed(self):
        """刷新Profile并确认会员有效，状态不足时阻塞订阅关联动作。"""
        state = self.profile_membership(refresh=True)
        require_case_state('S-02', state, self.policy.environment)

    def reload_profile(self):
        """真正刷新Profile并核对timeOrigin变化，等待资料重新加载完成。"""
        before = self.js('return performance.timeOrigin')
        self.d.request('/refresh', {})
        end = time.monotonic() + 25
        while time.monotonic() < end:
            state = self.js('return {ready:document.readyState,body:!!document.body,time:performance.timeOrigin}')
            if state['ready'] == 'complete' and state['body'] and state['time'] != before:
                self.wait_text('Gems Store')
                self.wait_account_details()
                return
            time.sleep(.2)
        raise member.Blocked('Profile 刷新未完成')

    def fill(self, selector, value):
        """通过平台对应的WebDriver输入方式清空并填写控件，保留真实输入事件。"""
        element = self.find_css(selector)
        if self.d.platform == 'android':
            self.d.fill_web(element, value)
            return
        self.d.request(f'/element/{element}/click', {})
        length=self.js('let e=document.querySelector(arguments[0]);return (e.value??e.innerText).length',selector)
        # iOS WebView 的 clear 不触发受控输入更新；用键盘退格执行真实清空。
        if length:self.d.request(f'/element/{element}/value', {'text':'\ue010'+'\ue003'*(length+1)})
        self.d.request(f'/element/{element}/value', {'text': value})

    def save_profile_editor(self):
        """Android收起键盘后按实时网页按钮保存，避免原生坐标随键盘变化而误点；iOS不变。"""
        if self.d.platform=='android':
            self.d.request('/context',{'name':'NATIVE_APP'})
            try:
                if self.d.request('/appium/device/is_keyboard_shown'):
                    self.d.request('/appium/device/hide_keyboard',{})
            finally:
                self.web()
            self.click_text('Save')
        else:
            self.click_label('Save')
        self.wait_text('Gems Store',25)

    def nickname(self):
        """保存随机英文姓名，返回Profile并刷新，核对两次回显与输入一致。"""
        if self.js('return location.pathname') != '/lobby/edit':
            self.profile_page()
            if self.d.platform=='android':
                self.wait_account_details()
                self.click_text('Edit Info')
            else:self.click_label('Edit Info')
        self.wait_css('input[type="text"]')
        old = self.js('return document.querySelector(\'input[type="text"]\').value')
        separator='' if self.d.platform=='android' else ' '
        name = random.choice(('Ethan', 'Oliver', 'Lucas', 'Noah', 'Henry')) + separator + random.choice(('Reed', 'Brooks', 'Hayes', 'Lane'))
        self.fill('input[type="text"]', name)
        actual=self.js('return document.querySelector(\'input[type="text"]\').value')
        assert actual==name,f'昵称输入不一致，期望{name}，实际{actual}'
        self.save_profile_editor();self.wait_text(name)
        evidence = self.web_capture('L-23-saved')
        self.reload_profile();self.wait_text(name)
        evidence += self.web_capture('L-23-refreshed')
        self.data['context']['member_identity'] = name
        self.save()
        return f'原昵称 {old}；保存随机英文昵称 {name}，返回 Profile 及刷新后均显示新昵称。', evidence

    def signature(self):
        """保存随机英文励志签名，核对保存返回及刷新后的实际回显。"""
        self.profile_page()
        if 'Quit' not in self.text():self.click_label('Edit')
        self.wait_css('[contenteditable=true]')
        phrase = random.choice(('Keep growing, one day at a time.', 'Dream big and keep moving.', 'Every small step brings progress.'))
        self.native_type('[contenteditable=true]', phrase)
        assert self.js("return document.querySelector('[contenteditable=true]').innerText")==phrase,'签名输入未完成'
        evidence=self.web_capture('L-24-before-save')
        self.save_profile_editor();self.wait_text(phrase)
        evidence+=self.web_capture('L-24-saved')
        self.reload_profile();self.wait_text(phrase)
        evidence+=self.web_capture('L-24-refreshed')
        return '输入英文励志签名：'+phrase+'；保存返回及刷新后均回显一致。',evidence

    def native_type(self, selector, value):
        """按平台使用真实键盘或Chromedriver输入，兼容普通输入框和可编辑签名区域。"""
        if self.d.platform == 'android':
            self.d.fill_web(self.find_css(selector), value)
            return
        rect=self.js('let e=document.querySelector(arguments[0]);return e.getBoundingClientRect().toJSON()',selector)
        old=self.js('let e=document.querySelector(arguments[0]);return e.isContentEditable?e.innerText:e.value',selector)
        self.d.tap_web_rect(rect, x_fraction=min(40/rect['width'], .5), y_fraction=min(18/rect['height'], .5))
        self.web()
        if old:
            # 仅调整可见编辑器光标，再通过原生键盘删改，不直接赋值页面状态。
            self.js("let e=document.querySelector(arguments[0]);let r=document.createRange();r.selectNodeContents(e);r.collapse(false);let s=getSelection();s.removeAllRanges();s.addRange(r)",selector)
        self.d.request('/context',{'name':'NATIVE_APP'})
        text='\ue003'*(len(old)+1)+value if old else value
        self.d.request('/actions',{'actions':[{'type':'key','id':'keyboard','actions':[a for c in text for a in ({'type':'keyDown','value':c},{'type':'keyUp','value':c})]}]})
        self.web()

    def tag_state(self, label):
        """读取指定标签按钮HTML及n/6计数，供比较选中样式与数量变化。"""
        return self.js(r"""let e=Array.from(document.querySelectorAll('button')).find(e=>e.innerText.trim()===arguments[0]);return e?{html:e.outerHTML,count:document.body.innerText.match(/(\d)\/6/)?.[1]}:null""", label)

    def tags_select(self):
        """按剩余容量选择至多两个可见标签，逐项核对高亮及计数，记录目标以便取消。"""
        self.profile_page()
        visible=self.js("""return Array.from(document.querySelectorAll('button')).filter(e=>e.getBoundingClientRect().height>0&&e.querySelector('img[alt]')&&e.innerText.trim()===e.querySelector('img[alt]').alt).map(e=>e.innerText.trim())""")
        states=[(label,self.tag_state(label)) for label in visible]
        if not states:raise member.Blocked('未找到可见测试标签')
        capacity=6-int(states[0][1]['count'])
        labels=[label for label,state in states if 'border-[#FF66D6]' not in state['html']][:max(0,min(2,capacity))]
        if not labels:raise member.Blocked('标签已满6个或缺少未选择的可见标签')
        samples=[];evidence=[]
        selected=[]
        for label in labels:
            before=self.tag_state(label)
            if int(before['count'])>=6:raise member.Blocked('标签已满6个，无法新增')
            self.click_text(label)
            end=time.monotonic()+6
            while time.monotonic()<end:
                after=self.tag_state(label)
                if int(after['count'])==int(before['count'])+1:break
                time.sleep(.15)
            assert int(after['count'])==int(before['count'])+1, label+'计数未增加'
            assert after['html']!=before['html'],label+'未产生选中样式变化'
            samples.append({'label':label,'before':before,'after':after})
            selected.append(label)
            self.data['context']['selected_tags_this_run']=selected;self.save()
            evidence+=self.web_capture('L-25-'+label)
        path=runner.OUT/'L-25-states.json';path.write_text(json.dumps(samples,ensure_ascii=False,indent=2))
        self.data['context']['selected_tags_this_run']=labels;self.save()
        return '依次选择 '+ '、'.join(labels)+'，计数逐次增加且选中样式改变。',[*evidence,str(path)]

    def tags_cancel(self):
        """取消本轮成功选择的标签，逐项核对高亮消失与计数减一。"""
        labels=self.data['context'].get('selected_tags_this_run',[])
        if not labels:raise member.Blocked('没有本轮成功选择的标签')
        samples=[];evidence=[]
        for label in reversed(labels):
            before=self.tag_state(label);self.click_text(label)
            end=time.monotonic()+6
            while time.monotonic()<end:
                after=self.tag_state(label)
                if int(after['count'])==int(before['count'])-1:break
                time.sleep(.15)
            assert int(after['count'])==int(before['count'])-1,label+'计数未减少'
            assert after['html']!=before['html'],label+'选中样式未取消'
            samples.append({'label':label,'before':before,'after':after})
            evidence+=self.web_capture('L-26-'+label)
        path=runner.OUT/'L-26-states.json';path.write_text(json.dumps(samples,ensure_ascii=False,indent=2))
        return f'依次取消本轮{len(labels)}个标签，计数逐次减少且高亮取消。',[*evidence,str(path)]

    def premium_entry(self):
        """确认未订阅后从Profile会员卡进入订阅页面，返回实际结果和证据。"""
        self.require_unsubscribed();self.click_css('button[aria-label="Upgrade to Premium"]');self.wait_premium()
        return 'Profile 的 Upgrade to Premium 卡片打开 Get Premium；实际为全屏订阅页 /lobby/premium。',self.web_capture('L-27-premium')

    def premium_return(self):
        """关闭由Profile打开的订阅页面，核对返回同一Profile来源路由。"""
        if 'Get Premium' not in self.text():
            self.premium_entry()
        self.close_premium();self.wait_text('Gems Store')
        assert self.js('return location.pathname')=='/lobby/profile'
        return '点击订阅弹窗关闭入口，回到原 Profile，金币与资料入口可见。',self.web_capture('L-28-return')

    def gems_unsubscribed(self):
        """记录未订阅余额并点击金币入口，核对是否弹出订阅页。"""
        self.require_unsubscribed();before=self.balance()
        self.click_label('Gems Store');self.wait_premium()
        evidence=self.web_capture('L-29-gems')
        return f'未订阅余额 {before}，点击金币入口后出现 Get Premium。',evidence

    def tiers(self):
        """切换Week、Month、3 Months，核对套餐按钮同步，不执行付款。"""
        if 'Get Premium' not in self.text():
            self.premium_entry()
        samples=[];evidence=[]
        for title in ('1 week','1 Month','3 Months'):
            button=self.js("return Array.from(document.querySelectorAll('[data-premium-content] button')).find(e=>e.innerText.includes(arguments[0])&&!e.innerText.startsWith('Get '))",title)
            if not button:raise member.Blocked('缺少套餐 '+title)
            self.d.request(f'/element/{button[base.ELEMENT_KEY]}/click',{})
            time.sleep(.3)
            state=self.js("return Array.from(document.querySelectorAll('[data-premium-content] button')).map(e=>({text:e.innerText,html:e.outerHTML})).filter(e=>e.text.includes(arguments[0])||e.text.startsWith('Get '))",title)
            payment=next((x['text'] for x in state if x['text'].startswith('Get ')), '')
            assert title.lower() in payment.lower(), '支付按钮未同步套餐：'+payment
            samples.append({'tier':title,'state':state})
            evidence+=self.web_capture('L-30-'+title.replace(' ','-'))
        path=runner.OUT/'L-30-tiers.json';path.write_text(json.dumps(samples,ensure_ascii=False,indent=2))
        self.close_premium()
        return 'Week、Month、3 Months 均切换成功，支付按钮分别同步所选套餐。',[*evidence,str(path)]

    def profile_cases(self):
        """按顺序执行尚未记录的资料编辑、标签及订阅入口用例。"""
        for case_id, action in [('L-23',self.nickname),('L-24',self.signature),('L-25',self.tags_select),('L-26',self.tags_cancel),('L-27',self.premium_entry),('L-28',self.premium_return),('L-29',self.gems_unsubscribed),('L-30',self.tiers)]:
            if case_id not in self.data['results']:self.run_new(case_id,action)

    def match_tabs(self):
        """依次点击Match顶部选项，核对各选项实际选中状态。"""
        self.profile_page();self.click_text('Match');self.wait_text('Start matching')
        evidence=[]
        for label in ('women','men','All'):
            self.click_text(label)
            selected=self.js("return Array.from(document.querySelectorAll('button')).find(e=>e.innerText.trim()===arguments[0]).getAttribute('aria-pressed')",label)
            assert selected=='true',label+'未选中'
            evidence+=self.web_capture('N-04-'+label)
        return '已登录时 women、men、All 的 aria-pressed 均按点击变为 true。',evidence

    def match_discover(self):
        """点击Match中间Discover入口，核对路由及来源列表恢复。"""
        self.click_css('button[aria-label="Discover"]')
        self.wait_text('Discover')
        assert self.js('return location.pathname')=='/lobby/anchor'
        self.list_page()
        return 'Match 中间 Discover 点击后进入 /lobby/anchor，用户卡片可见。',self.web_capture('N-05-discover')

    def match_page(self):
        """进入Match并等待Start matching入口，不在此方法内消耗匹配次数。"""
        self.click_text('Match');self.wait_text('Start matching')
        end=time.monotonic()+8
        while time.monotonic()<end:
            if self.remaining() is not None:return
            time.sleep(.2)
        raise member.Blocked('Match入口已出现，但免费次数在8秒内尚未加载完成')

    def stop_call(self):
        """退出当前通话或搜索并核对停止状态；新完整套件还会离开Match防止自动续配。"""
        if self.js('return !!document.querySelector(\'svg:has(use[href="#icon-calling/call-ing-hangup"])\')'):
            self.click_css('svg:has(use[href="#icon-calling/call-ing-hangup"])')
        end=time.monotonic()+12
        while time.monotonic()<end:
            body=self.text()
            if 'Start matching' in body:return
            if 'Exit' in body:
                self.click_label('Exit');self.wait_text('Start matching');return
            time.sleep(.2)
        raise member.Blocked('未确认退出自动匹配，保留现场')

    def match_prepare(self):
        """执行已登录Match导航检查并核对未订阅送礼数据前置。"""
        self.run_new('N-04',self.match_tabs)
        self.run_new('N-05',self.match_discover)
        self.require_unsubscribed();balance=self.balance()
        self.data['context']['gift_unsubscribed_ready']=True
        self.data['context']['gift_unsubscribed_balance']=balance
        self.save()
        self.match_page()

    def start_matching(self):
        """检查本阶段主动启动次数预算，真实点击匹配并立即保存计数。"""
        starts=self.data['context'].get('member_matching_starts',0)
        if starts>=2:raise member.Blocked('本轮未订阅阶段已启动两次，停止重复匹配')
        self.native_tap_label('Start matching')
        self.data['context']['member_matching_starts']=starts+1;self.save()

    def native_tap_label(self,label):
        """读取当前可见标签位置并映射为真实触屏点击，随后恢复网页上下文。"""
        rect=self.js("let e=Array.from(document.querySelectorAll('body *')).filter(e=>e.getBoundingClientRect().height>0&&e.textContent.trim()===arguments[0]).at(-1);return e?.getBoundingClientRect().toJSON()",label)
        if not rect:raise AssertionError('未找到可见标签：'+label)
        self.d.tap_web_rect(rect)
        self.web()

    def native_viewport_origin(self):
        """历史iOS坐标兼容入口，从Chrome底部工具栏反推网页视口原点。"""
        height=self.js('return innerHeight')
        self.d.request('/context',{'name':'NATIVE_APP'})
        tree=self.d.tree()
        toolbar=next((n for n in tree.iter() if n.attrib.get('name')=='kToolbarNewTabButtonIdentifier' and n.attrib.get('visible')=='true'),None)
        if toolbar is None:raise member.Blocked('Chrome工具栏不可见，无法映射网页点击坐标')
        return (0,float(toolbar.attrib['y'])-height)

    def incoming_permission_state(self):
        """读取通话相关原生权限文案后恢复网页上下文，返回名称集合。"""
        self.d.request('/context',{'name':'NATIVE_APP'})
        names={n.attrib.get('name','') for n in self.d.tree().iter() if n.attrib.get('visible')=='true'}
        self.web()
        return names


def main():
    """解析命令行参数并执行所选阶段，将执行结果保存到对应批次。"""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase',choices=('inspect','profile','match-prepare','membership','await-login'))
    parser.add_argument('--batch-dir',type=Path)
    parser.add_argument('--env',choices=('test','prod'),default='test')
    parser.add_argument('--url')
    args=parser.parse_args();path=configure_batch(args.batch_dir)
    s=Suite(environment=args.env,url=args.url)
    if args.phase=='await-login':s.wait_login();return
    s.web()
    if args.phase=='profile':s.profile_cases()
    elif args.phase=='match-prepare':s.match_prepare()
    elif args.phase=='membership':print(json.dumps(s.profile_membership().to_dict(),ensure_ascii=False))
    else:print(json.dumps({'batch':str(path),'url':s.js('return location.href'),'body':s.text()},ensure_ascii=False))


if __name__=='__main__':main()
