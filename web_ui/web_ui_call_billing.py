"""Android/iOS共用通话计费业务，触屏操作仍由各自的MobileDriver适配。"""
import math
import time

from web_ui_environment import configuration
from web_ui_member_suite import Blocked
from web_ui_account_state import StateExcluded
from web_ui_platform_cases import platform_pause_reason, disabled_case_reason


def check_call_billing(before, after, seconds, free, rate=80):
    """对一分钟至两分钟内的证据按分钟向上取整计费，Free仍应零扣费。"""
    if not all(isinstance(n, int) and n >= 0 for n in (before, after)):
        raise ValueError('金币余额格式无效')
    if not 60 <= seconds < 120:
        return 'BLOCKED', f'缺少一分钟且不足两分钟的完整计时证据：{seconds}秒'
    expected = 0 if free else math.ceil(seconds / 60) * rate
    actual = before-after
    return ('PASS' if actual == expected else 'FAIL',
            f'{"Free免费" if free else "普通"}通话{seconds}秒，金币{before}→{after}，实际扣除{actual}，预期扣除{expected}')


class CallBillingMixin:
    def unavailable_billing_target(self, reason):
        """Android无目标或有限拨号未接通按用户要求跳过；其他平台保留原阻塞规则。"""
        platform=self.d.platform
        rules=configuration().get('platform_rules',{}).get(platform,{})
        if rules.get('skip_unavailable_call_targets'):
            raise StateExcluded(reason+'；未形成已接听通话，按本平台规则跳过计费校验')
        raise Blocked(reason)

    def billing_clock(self):
        """仅在双路视频已加载且存在唯一可见通话计时时返回秒数。"""
        return self.js(r'''const media=Array.from(document.querySelectorAll('video')).filter(e=>e.getBoundingClientRect().height>20&&e.readyState>=2&&e.videoWidth>0);
            const timers=[...new Set(Array.from(document.querySelectorAll('span,p,div')).filter(e=>!e.children.length&&e.getBoundingClientRect().height>0).map(e=>e.textContent.trim()).filter(t=>/^\d{1,2}:\d{2}(?::\d{2})?$/.test(t)))];
            if(media.length<2||timers.length!==1)return null;
            const freeLabel=Array.from(document.querySelectorAll('span,p,div')).filter(e=>!e.children.length&&e.getBoundingClientRect().height>0).map(e=>e.textContent.trim()).find(t=>/^(?:free(?: (?:video )?call)?|免费(?:通话)?)$/i.test(t));
            return {label:timers[0],seconds:timers[0].split(':').map(Number).reduce((a,b)=>a*60+b,0),free:!!freeLabel,free_label:freeLabel||''}''')

    def billing_dial(self, free):
        """按卡片Free标识选人，通过真实平台触屏拨打，最多尝试三名。"""
        self.d.request('/url', {'url': self.policy.base_url+'lobby/anchor'})
        self.wait_text('Discover')
        self.list_page()
        targets = self.js('''return Array.from(document.querySelectorAll('button')).filter(e=>e.textContent.trim()==='Video'&&e.getBoundingClientRect().height>0).map((e,index)=>({index,text:e.parentElement.parentElement.parentElement.innerText}))''')
        import re
        known_free = self.data['context'].get('billing_free_targets', [])
        targets = [t for t in targets if bool(re.search(r'\bfree\b|免费', t['text'], re.I)) == free and not (not free and t['text'] in known_free)][:3]
        if not targets:
            self.unavailable_billing_target('没有明确Free免费标识的主播' if free else '没有非Free的普通主播')
        for target in targets:
            evidence = self.web_capture('billing-card')
            rect = self.js('''return Array.from(document.querySelectorAll('button')).filter(e=>e.textContent.trim()==='Video'&&e.getBoundingClientRect().height>0)[arguments[0]]?.getBoundingClientRect().toJSON()''', target['index'])
            if not rect:
                raise Blocked('主播卡片刷新后位置失效，停止避免误点')
            # Android和iOS分别使用既有驱动坐标映射，不共享原生坐标。
            self.d.tap_web_rect(rect)
            self.web()
            deadline = time.monotonic()+30
            while time.monotonic() < deadline:
                body = self.text()
                if re.search(r'offline|not online', body, re.I):
                    break
                if 'Get Premium' in body:
                    raise Blocked('拨打前会员已到期，需要恢复会员并重新确认')
                if re.search(r'insufficient|not enough|buy gems', body, re.I):
                    raise Blocked('拨打时金币不足，未接通计费通话')
                clock = self.billing_clock()
                if clock:
                    return target, clock, evidence
                time.sleep(.25)
            self.d.request('/url', {'url': self.policy.base_url+'lobby/anchor'})
            self.wait_text('Discover')
        self.unavailable_billing_target('最多三名主播未接通，不使用拨号等待时长推断通话扣费')

    def billing_call(self, free=False):
        """记录跨入第二分钟的实际通话及余额，避免在60秒扣费边界挂断造成误判。"""
        if free and disabled_case_reason('S-10'):
            raise StateExcluded('S-10已在所有端停用：'+disabled_case_reason('S-10'))
        self.policy.require('subscribed_video')
        self.require_subscribed()
        before = self.balance()
        if not free and before < 80:
            raise Blocked(f'余额{before}不足一分钟80金币')
        first = self.data['results'].get('S-04', {}).get('status') == 'PASS'
        if not first:
            if platform_pause_reason('S-04',self.d.platform):
                raise StateExcluded('本平台S-04首次Video前置已按用户要求暂缓，计费依赖未满足，本轮跳过')
            raise Blocked('需先完成S-04首次Video余额提示，避免吞掉首次弹窗')
        attempt = {'platform':self.d.platform, 'before':before, 'free':free,
                   'started_at':time.time(), 'evidence':self.web_capture('billing-before')}
        self.data['context'].setdefault('call_billing_attempts', []).append(attempt)
        self.save()
        issue = None
        try:
            target, clock, evidence = self.billing_dial(free)
            attempt.update(target=target, seconds=clock['seconds'])
            attempt['actual_free'] = clock['free']
            attempt['evidence'] += evidence
            attempt['evidence'] += self.web_capture('billing-connected')
            if clock['free'] != free:
                if clock['free']:
                    self.data['context'].setdefault('billing_free_targets', []).append(target['text'])
                raise Blocked('实际通话有Free标识，不作为耗币通话' if clock['free'] else '实际通话没有Free标识，不作为免费通话')
            deadline = time.monotonic()+75
            while time.monotonic() < deadline:
                clock = self.billing_clock()
                if not clock:
                    raise Blocked('通话提前结束或唯一计时缺失')
                attempt['seconds'] = clock['seconds']
                if clock['free'] != free:
                    raise Blocked('通话期间Free标识变化，不能作为单一计费类型判断')
                if clock['seconds'] >= 65:
                    attempt['evidence'] += self.web_capture('billing-minute')
                    break
                time.sleep(.25)
            if attempt['seconds'] < 60:
                raise Blocked('未观察到完整一分钟通话')
        except Exception as error:
            issue = error
        finally:
            # stop_call内部为Android/iOS分别处理遮挡及触屏退出，复用已有平台分支。
            active = self.js('''return location.pathname==='/lobby/match'||Array.from(document.querySelectorAll('video')).some(e=>e.getBoundingClientRect().height>20)''')
            if active:
                self.stop_call()
            else:
                self.d.request('/url', {'url':self.policy.base_url+'lobby/profile'})
            self.profile_page()
            self.reload_profile()
            attempt['after'] = self.balance()
            attempt['evidence'] += self.web_capture('billing-after')
            attempt['finished_at'] = time.time()
            self.save()
        if issue:
            if isinstance(issue,StateExcluded):
                raise StateExcluded(f'{issue}；已退出，实际余额{before}→{attempt["after"]}')
            raise Blocked(f'{issue}；已退出，实际余额{before}→{attempt["after"]}')
        status, actual = check_call_billing(before, attempt['after'], attempt['seconds'], free)
        attempt.update(status=status, actual=actual)
        self.save()
        if status == 'BLOCKED':
            raise Blocked(actual)
        if status == 'FAIL':
            raise AssertionError(actual)
        return actual, attempt['evidence']

    def spend_to_zero(self):
        """使用真实普通通话消费准备零余额数据；达到预算、无法拨号或扣费异常即停止。"""
        self.policy.require('subscribed_video')
        budget = configuration().get('call_billing', {}).get('max_spend_seconds', 600)
        end = time.monotonic()+budget
        self.require_subscribed()
        balance = self.balance()
        while balance:
            if time.monotonic()+65 > end:
                raise Blocked(f'真实通话消费预算{budget}秒已用尽，余额{balance}')
            if balance < 80:
                raise Blocked(f'实际余额{balance}不足单分钟80金币，不能冒充余额0')
            attempts_before = len(self.data['context'].get('call_billing_attempts', []))
            try:
                self.billing_call(False)
            except Blocked:
                attempts = self.data['context'].get('call_billing_attempts', [])
                attempt = attempts[-1] if len(attempts) > attempts_before else {}
                if attempt.get('actual_free') and attempt.get('after') == balance:
                    continue
                raise
            updated = self.balance()
            if updated >= balance:
                raise Blocked('普通通话后余额未减少，停止继续消费')
            balance = updated
        return balance

    def empty_video(self):
        """先真实消费到零再拨Video，保留首次Video前置及金币不足提示证据。"""
        self.policy.require('subscribed_video')
        self.require_subscribed()
        if self.data['results'].get('S-04', {}).get('status') != 'PASS':
            raise Blocked('需先完成S-04，不能用首次Video替代非首次无金币拦截')
        self.spend_to_zero()
        self.d.request('/url', {'url':self.policy.base_url+'lobby/anchor'})
        self.wait_text('Discover')
        self.click_label('Video')
        deadline = time.monotonic()+8
        import re
        while time.monotonic() < deadline:
            if re.search(r'insufficient|not enough|out of|buy gems', self.text(), re.I):
                return '通过真实普通通话消费到0，非首次Video显示金币不足', self.web_capture('S-05-empty')
            time.sleep(.2)
        raise AssertionError('实际余额0，非首次Video未显示金币不足弹窗')
