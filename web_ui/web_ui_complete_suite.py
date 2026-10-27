#!/usr/bin/env python3
"""全量真机执行公共动作；复用既有 Appium 会话和批次证据。"""
import json
import random
import re
import time
from pathlib import Path

from web_ui_extended_suite import Suite as ExtendedSuite, configure_batch
from web_ui_extended_suite import base, runner, member
from web_ui_account_state import ACTIVE_MEMBER_CASES, StateUnconfirmed
from web_ui_environment import configuration, origin
from web_ui_execution_state import PaymentJournal, device_run
from web_ui_mobile_driver import MobileSessionUnavailable
from web_ui_call_billing import CallBillingMixin


class Suite(CallBillingMixin, ExtendedSuite):
    def capture_current_identity(self):
        """通过实际Profile导航观察用户信息主对象uuid，保存限时证据，不重放业务接口。"""
        self.profile_page()
        body=self.text()
        if not self.js("return Array.from(document.querySelectorAll('button')).some(e=>e.innerText.trim()==='Connect')"):
            raise StateUnconfirmed('当前Profile未确认已登录，不能采集游客UUID作为会员恢复目标')
        expected_name=self.data['context'].get('member_identity')
        if expected_name and not re.search(r'(?:^|\n)'+re.escape(expected_name)+r'(?:\n|$)',body):
            raise StateUnconfirmed('当前Profile昵称与本轮已登录账号不符，停止采集UUID')
        self.js("""window.__webUiIdentityProbe=null;
            if(!window.__webUiIdentityHook){window.__webUiIdentityHook=true;
            const save=(url,value,httpStatus)=>{if(new URL(url,location.href).pathname.endsWith('/accounts/details')&&value?.data?.uuid){
              const uuid=value.data.uuid;if(typeof uuid==='number'&&!Number.isSafeInteger(uuid))return;
              window.__webUiIdentityProbe={uuid:String(uuid),status:httpStatus,captured_at:new Date().toISOString(),json_path:'response.data.uuid',site_origin:location.origin}}};
            const fetchOriginal=window.fetch;window.fetch=async function(...args){const r=await fetchOriginal.apply(this,args);
              r.clone().json().then(v=>save(typeof args[0]==='string'?args[0]:args[0].url,v,r.status)).catch(()=>{});return r};
            const open=XMLHttpRequest.prototype.open,send=XMLHttpRequest.prototype.send;
            XMLHttpRequest.prototype.open=function(method,url,...args){this.__webUiUrl=url;return open.call(this,method,url,...args)};
            XMLHttpRequest.prototype.send=function(...args){this.addEventListener('load',()=>{try{save(this.__webUiUrl,this.responseType==='json'?this.response:JSON.parse(this.responseText),this.status)}catch(e){}});return send.apply(this,args)}}
            return true""")
        self.click_text('Discover');self.wait_text('Discover')
        self.click_text('Profile');self.wait_text('Edit Info')
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            identity=self.js('return {identity:window.__webUiIdentityProbe||null}')['identity']
            if identity and identity.get('status')==200 and re.fullmatch(r'\d+',identity.get('uuid','')):
                # 证据时效使用采集电脑时钟，避免手机时钟略超前导致刚采集即被误判。
                from datetime import datetime, timezone
                identity['browser_captured_at']=identity['captured_at']
                identity['captured_at']=datetime.now(timezone.utc).isoformat()
                identity['nickname']=self.data['context'].get('member_identity')
                target=self.path.parent/'current-user-identity.json'
                target.write_text(json.dumps(identity,ensure_ascii=False,indent=2))
                self.event('从当前真实用户信息响应采集主对象UUID',uuid=identity['uuid'],evidence=[str(target)])
                return identity
            time.sleep(.15)
        raise StateUnconfirmed('未捕获当前用户信息主对象data.uuid，不能使用历史UUID')

    def handle_android_media_request(self):
        """处理本站Chrome继续授权及系统音视频请求，保留原生证据；iOS沿用原流程。"""
        if self.d.platform!='android':return
        for _ in range(4):
            node=self.d.permission_button(True)
            if not node:break
            evidence=self.capture('android-pending-media-'+str(int(time.time()*1000)))
            node=self.d.permission_button(True)
            if not node:break
            self.d.tap_node(node)
            self.event('处理Android待确认音视频授权',label=node.get('text',''),evidence=evidence)
        self.web()

    def stop_call(self):
        """退出后离开 Match，销毁仍可能待触发的自动续配计时器。"""
        hangup='svg:has(use[href="#icon-calling/call-ing-hangup"])'
        deadline=time.monotonic()+12
        while time.monotonic()<deadline:
            if self.d.platform=='android' and 'Unlock Video Calls' in self.text():
                evidence=self.web_capture('call-subscription-limit')
                self.click_css('button[aria-label="cancel"]')
                self.wait_absent('Unlock Video Calls')
                self.d.request('/url',{'url':self.policy.base_url+'lobby/anchor'})
                self.wait_text('Discover')
                self.event('关闭Android通话订阅限制并退出通话',evidence=evidence)
                return
            rect=self.js("return Array.from(document.querySelectorAll(arguments[0])).find(e=>e.getBoundingClientRect().width>0)?.getBoundingClientRect().toJSON()",hangup)
            if rect:
                if self.d.platform=='android':
                    panel=self.js("return document.querySelector('[data-rsbs-overlay]')?.getBoundingClientRect().toJSON()")
                    if panel:
                        if rect['y']+rect['height']/2>=panel['y']:
                            raise member.Blocked('礼物面板遮挡退出入口，无法确定可关闭的遮罩位置')
                        # Android真实触屏点击遮罩，避免WebDriver将被遮挡的挂断按钮直接判错。
                        self.d.tap_web_rect(rect);self.web()
                        continue
                    self.click_css(hangup)
                else:
                    self.d.tap_web_rect(rect)
                self.web()
            elif 'Exit' in self.text():
                self.tap_current_label('Exit')
            elif 'Start matching' in self.text():
                self.dismiss_login()
                remaining=self.remaining()
                self.d.request('/url',{'url':self.policy.base_url+'lobby/anchor'})
                self.wait_text('Discover')
                self.wait_text('Profile')
                if self.js("return Array.from(document.querySelectorAll('video')).some(e=>e.getBoundingClientRect().width>0)"):
                    raise member.Blocked('离开Match后仍有可见视频，未确认退出')
                self.event('退出通话和自动续配，已离开Match',remaining=remaining,path=self.js('return location.pathname'))
                return
            time.sleep(.15)
        raise member.Blocked('未确认退出通话及自动续配，保留现场')

    def capture(self, label):
        """保存原生截图及页面XML，并在完成后恢复原网页上下文。"""
        previous=self.d.request('/context')
        self.d.request('/context',{'name':'NATIVE_APP'})
        evidence=super().capture(label)
        if previous!='NATIVE_APP':self.d.request('/context',{'name':previous})
        return evidence

    def tap_native(self, name):
        """按最新原生树定位屏幕内控件，依据实际屏幕尺寸执行点击。"""
        self.d.request('/context',{'name':'NATIVE_APP'})
        screen=self.d.request('/window/rect')
        nodes=[n for n in self.d.tree().iter() if n.attrib.get('name')==name
               and n.attrib.get('visible')=='true' and 0<=float(n.attrib.get('x',-1))<screen['width']
               and 0<=float(n.attrib.get('y',-1))<screen['height']]
        if not nodes:raise AssertionError('未找到屏幕内原生控件：'+name)
        a=nodes[0].attrib
        self.d.tap(round(float(a['x'])+float(a['width'])/2),round(float(a['y'])+float(a['height'])/2))

    def tap_current_label(self, label, restore=True):
        """将当前可见标签映射为触屏点击；restore=False用于点击后可能关闭标签页的场景。"""
        rect=self.js("let e=Array.from(document.querySelectorAll('body *')).filter(e=>e.getBoundingClientRect().height>0&&e.textContent.trim()===arguments[0]).at(-1);return e?.getBoundingClientRect().toJSON()",label)
        if not rect:raise AssertionError('当前页面没有可见标签：'+label)
        self.d.tap_web_rect(rect,restore=restore)

    def native_open_url(self, url):
        """在当前设备Chrome中打开目标网址，不修改默认浏览器、VPN或系统配置。"""
        if self.d.platform=='android':
            # 只导航当前 Chrome 会话，不修改默认浏览器或系统设置。
            self.d.request('/context',{'name':'NATIVE_APP'})
            contexts=self.d.request('/contexts')
            web=next((c for c in contexts if c!='NATIVE_APP'),None)
            if not web:raise member.Blocked('Android Chrome 没有可用网页上下文')
            self.d.request('/context',{'name':web})
            handles=self.d.request('/window/handles')
            if not handles:raise member.Blocked('Android Chrome 没有可用标签页')
            handle=self.data['context'].get('web_window')
            self.d.request('/window',{'handle':handle if handle in handles else handles[0]})
            self.d.request('/url',{'url':url})
            return
        self.d.request('/context',{'name':'NATIVE_APP'})
        self.tap_native('地址和搜索栏')
        time.sleep(.5)
        field=self.d.find('Address')
        self.d.request(f'/element/{field}/value',{'text':url+'\n'})
        # Chrome 偶尔将换行留在编辑态，确认原生 Go 后才视为提交导航。
        time.sleep(.3)
        if 'Go' in self.native_names():
            self.tap_native('Go')

    def visible_merchant(self, timeout=15):
        """在timeout秒内寻找前台被测站点，成功时缓存上下文及Android窗口标识。"""
        end=time.monotonic()+timeout
        context=self.data['context']
        if context.get('web_context'):
            try:
                self.d.focus_page({'context':context['web_context'],'window':context.get('web_window')})
                if origin(self.js('return location.href'))==origin(self.policy.base_url) and self.js('return document.visibilityState')=='visible':
                    return True
            except MobileSessionUnavailable:raise
            except RuntimeError:pass
        while time.monotonic()<end:
            page=self.d.select_page(self.policy.base_url)
            if page:
                self.data['context']['web_context']=page['context']
                self.data['context']['web_window']=page.get('window')
                self.save();return True
            time.sleep(.3)
        self.d.request('/context',{'name':'NATIVE_APP'})
        return False

    def wait_native(self, label, timeout=15):
        """轮询原生名称等待指定提示，超时不假设系统弹窗已出现。"""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            names = self.native_names()
            if label in names:
                return names
            time.sleep(.15)
        raise AssertionError('未出现原生控件：' + label)

    def android_scroll_to_label(self, label, max_swipes=3):
        """在Android真实前台页面有限滑动，直到目标文字进入原生可访问区域。"""
        if self.d.platform!='android':
            raise member.Blocked('Android原生滑动入口不能用于iOS')
        self.d.request('/context',{'name':'NATIVE_APP'})
        for attempt in range(max_swipes+1):
            if label in self.d.names():return
            if attempt<max_swipes:self.d.swipe_up()
        raise member.Blocked('有限滑动后仍未找到原生可见按钮：'+label)

    def reload_native(self):
        """按平台真实刷新Chrome页面；Android使用Chromedriver，iOS使用工具栏菜单。"""
        if self.d.platform=='android':
            self.web();self.d.request('/refresh',{})
            return
        self.d.request('/context', {'name': 'NATIVE_APP'})
        self.d.click('kLegacyToolbarToolsMenuButtonIdentifier')
        names=self.native_names()
        if 'kToolsMenuStop' in names:
            self.d.click('kToolsMenuStop')
            self.d.click('kLegacyToolbarToolsMenuButtonIdentifier')
        self.wait_native('kToolsMenuReload', 5)
        self.d.click('kToolsMenuReload')

    def permissions(self, prefix):
        """按实际平台执行拒绝、Try Again及允许通知检查，保留各项独立结果。"""
        if self.d.platform=='android':
            return self.permissions_android(prefix)
        ids = ('G-10', 'G-11', 'G-12') if prefix == 'G' else ('N-01', 'N-02', 'N-03')
        self.web();self.dismiss_login()
        if self.js('return location.pathname') != '/lobby/match':
            self.click_text('Match');self.wait_text('Start matching')
        if '不允许' not in self.native_names():
            self.reload_native()
        start = int(time.time()*1000)
        try:
            self.wait_native('不允许')
            evidence = self.capture(ids[0]+'-request')
            self.d.click('不允许');self.web();self.wait_text('Try Again')
            evidence += self.web_capture(ids[0]+'-denied')
            self.record(ids[0], 'PASS', 'Chrome 权限请求点击不允许后，网页展示开启权限说明和 Try Again。', runner.CATALOG[ids[0]][3], start, evidence)
        except Exception as error:
            self.web();self.record(ids[0], 'BLOCKED', str(error), ['进入 Match，尝试触发 Chrome 权限请求'], start, self.web_capture(ids[0]+'-blocked'))
            return
        start = int(time.time()*1000)
        before = self.js('return performance.timeOrigin')
        self.native_tap_label('Try Again')
        try:
            self.wait_native('允许')
            evidence = self.capture(ids[1]+'-reprompt');self.web()
            after = self.js('return performance.timeOrigin')
            self.record(ids[1], 'PASS' if before != after else 'FAIL', f'Try Again 后重新出现 Chrome 请求；页面 timeOrigin {before} → {after}。', runner.CATALOG[ids[1]][3], start, evidence)
        except AssertionError:
            self.web();self.record(ids[1], 'FAIL', 'Try Again 后未重新出现 Chrome 权限请求；实际网页：'+self.text(), runner.CATALOG[ids[1]][3], start, self.web_capture(ids[1]+'-failed'))
            self.dismiss_login();self.reload_native()
        start = int(time.time()*1000)
        try:
            self.wait_native('允许');self.d.click('允许')
            self.wait_native('已授予摄像头和麦克风使用权', 8)
            evidence = self.capture(ids[2]+'-notification');self.web()
            self.record(ids[2], 'PASS', 'Chrome 出现“已授予摄像头和麦克风使用权”内置下落通知。', runner.CATALOG[ids[2]][3], start, evidence)
        except Exception as error:
            self.web();self.record(ids[2], 'BLOCKED', str(error), runner.CATALOG[ids[2]][3], start, self.web_capture(ids[2]+'-blocked'))

    def permissions_android(self, prefix):
        """Android 的 Chrome 站点权限与系统权限分层处理，不预先自动授权。"""
        ids=('G-10','G-11','G-12') if prefix=='G' else ('N-01','N-02','N-03')
        self.skip_android_permission_notice(ids[2])
        self.web();self.dismiss_login()
        if self.js('return location.pathname')!='/lobby/match':
            self.click_text('Match');self.wait_text('Start matching')

        def wait_button(allow, timeout=12):
            """在限定时间内等待Android已识别权限层的允许或拒绝按钮。"""
            deadline=time.monotonic()+timeout
            while time.monotonic()<deadline:
                button=self.d.permission_button(allow)
                if button:return button
                time.sleep(.2)
            return None

        start=int(time.time()*1000)
        button=wait_button(False,2)
        if not button:
            self.web()
            states=self.js("return Promise.all(['camera','microphone'].map(async name=>(await navigator.permissions.query({name})).state))")
            if 'prompt' in states:
                self.tap_current_label('Start matching')
            button=wait_button(False)
        if not button:
            self.web();ev=self.web_capture(ids[0]+'-android-no-request')
            for cid in ids[:2]:
                self.record(cid,'BLOCKED','Android 未出现可识别的权限请求，可能已有授权或持久拒绝；未清理浏览器数据或修改系统权限。',
                            ['进入Match并刷新网页，检查Chrome及Android系统权限层'],start,ev)
            return
        evidence=self.capture(ids[0]+'-android-request')
        self.d.tap_node(button);self.web()
        deadline=time.monotonic()+12
        while time.monotonic()<deadline:
            if any(label in self.text() for label in ('Try Again','How Can I Provide Permission?')):break
            time.sleep(.15)
        android_help='How Can I Provide Permission?' in self.text()
        if 'Try Again' not in self.text() and not android_help:
            evidence+=self.web_capture(ids[0]+'-android-denied')
            self.record(ids[0],'FAIL','点击Android权限拒绝后未出现Try Again。',['进入Match','在实际权限层点击拒绝'],start,evidence)
            for cid in ids[1:2]:
                self.record(cid,'BLOCKED','拒绝后未出现Try Again，无法继续该权限流程。',[],int(time.time()*1000),evidence)
            return
        evidence+=self.web_capture(ids[0]+'-android-denied')
        self.record(ids[0],'PASS','实际权限请求点击拒绝后，网页显示开启权限说明和Try Again。',
                    ['进入Match','捕获Android实际权限请求并拒绝','核对网页Try Again说明'],start,evidence)
        if android_help:
            self.data['results'][ids[0]]['actual']='Android拒绝后展示开启摄像头和麦克风说明，入口为How Can I Provide Permission?。'
            self.data['results'][ids[0]]['executed_steps']=['进入Match并触发实际Chrome权限层','点击拒绝','核对Android权限说明']
            self.save()
            start=int(time.time()*1000)
            self.click_label('How Can I Provide Permission?');self.wait_text('Go to Settings')
            evidence=self.web_capture(ids[1]+'-settings-help')
            for title in ('Go to Permissions','Permission Management'):
                self.click_label('Next');self.wait_text(title)
                evidence+=self.web_capture(ids[1]+'-'+title.replace(' ','-'))
            self.click_label('Enjoy');self.wait_absent('Important Tips')
            self.record(ids[1],'PASS','Android按Next→Next→Enjoy完成三步权限引导，无Try Again入口。',
                        ['打开How Can I Provide Permission?','点击Next并核对Go to Permissions',
                         '再次点击Next核对Permission Management','点击Enjoy关闭引导'],start,evidence)
            self.data['results'][ids[1]]['expected']=['Android依次完成Next、Next、Enjoy权限说明；iOS仍执行Try Again刷新流程']
            self.save()
            start=int(time.time()*1000)
            evidence=self.android_site_permissions(True)
            self.reload_native()
            self.wait_text('Start matching')
            self.handle_android_media_request()
            self.event('Android站点音视频权限已开启，浏览器下落通知按平台差异跳过',evidence=evidence)
            return
        start=int(time.time()*1000);before=self.js('return performance.timeOrigin')
        self.tap_current_label('Try Again')
        button=wait_button(True)
        self.web();after=self.js('return performance.timeOrigin')
        evidence=self.web_capture(ids[1]+'-android-reprompt')
        ok=bool(button) and before!=after
        self.record(ids[1],'PASS' if ok else 'FAIL',f'Try Again后页面timeOrigin {before} → {after}；重新权限请求={bool(button)}。',
                    ['点击Try Again','核对页面重载及实际权限请求'],start,evidence)
        if not button:
            self.event('Android未出现新的权限请求，未执行允许',evidence=evidence)
            return
        evidence=[]
        # 部分系统分别请求摄像头、麦克风，最多处理四层已识别的允许按钮。
        for index in range(4):
            # 截图会切换上下文，点击前重新取原生节点，避免使用旧坐标。
            self.d.request('/context',{'name':'NATIVE_APP'})
            button=self.d.permission_button(True)
            if not button:break
            self.d.tap_node(button)
            evidence+=self.capture(ids[2]+f'-android-allowed-{index+1}')
            if not wait_button(True,1):break
        self.web()
        states=self.js('''return Promise.all(['camera','microphone'].map(async name=>{
            try{return {name,state:(await navigator.permissions.query({name})).state}}
            catch(e){return {name,state:'unsupported'}}}))''')
        evidence+=self.web_capture(ids[2]+'-android-result')
        granted=all(item['state']=='granted' for item in states)
        self.event('Android实际允许后权限状态核对',states=states,granted=granted,evidence=evidence)

    def skip_android_permission_notice(self, case_id):
        """按用户确认的Android差异跳过浏览器下落通知，不影响iOS校验。"""
        if self.d.platform!='android':
            raise member.Blocked('Android通知跳过规则不能用于iOS')
        self.record(case_id,'NOT_APPLICABLE','用户确认Android权限逻辑不同，跳过允许后的浏览器下落通知校验；iOS保留该用例。',
                    ['核对当前设备为Android，应用用户指定的平台跳过规则'],int(time.time()*1000),[])

    def android_site_permissions(self, enable):
        """仅操作当前被测域名的Android Chrome音视频权限，iOS调用会被拒绝。"""
        if self.d.platform!='android':
            raise member.Blocked('Android站点权限入口不能用于iOS')
        self.d.request('/context',{'name':'NATIVE_APP'})
        self.d.click('com.android.chrome:id/location_bar_status')
        self.wait_native('权限',5)
        if not any(self.policy.base_url.split('/')[2] == name for name in self.d.names()):
            raise member.Blocked('Chrome站点权限页域名与被测网站不符')
        self.d.click('权限');self.wait_native('摄像头',5);self.wait_native('麦克风',5)
        for _ in range(2):
            switches=[n.attrib for n in self.d.tree().iter() if
                      n.attrib.get('resource-id')=='com.android.chrome:id/switchWidget' and
                      (n.attrib.get('checked')=='true')!=enable]
            if not switches:break
            self.d.tap_node(switches[0]);time.sleep(.25)
        switches=[n.attrib for n in self.d.tree().iter() if n.attrib.get('resource-id')=='com.android.chrome:id/switchWidget']
        assert len(switches)==2 and all((n.get('checked')=='true')==enable for n in switches),'音视频权限开关未达到目标状态'
        evidence=self.capture('android-site-permissions-'+str(int(time.time()*1000)))
        for _ in range(2):
            if not any('page_info' in name for name in self.d.names()):break
            self.d.request('/appium/device/press_keycode',{'keycode':4})
        self.web()
        return evidence

    def reset_android_site_permissions(self):
        """仅重置当前被测域名的Chrome站点权限，保留账号及网站数据。"""
        if self.d.platform!='android':
            raise member.Blocked('Android站点权限重置不能用于iOS')
        self.web()
        host=self.policy.base_url.split('/')[2]
        self.d.request('/context',{'name':'NATIVE_APP'})
        self.d.click('com.android.chrome:id/location_bar_status')
        self.wait_native('权限',5)
        if host not in self.d.names():
            raise member.Blocked('权限重置页域名与被测网站不一致')
        self.d.click('权限');self.wait_native('重置权限',5)
        self.d.click('重置权限');self.wait_native('重置权限？',5)
        if '此选项会重置 '+host+' 的权限' not in self.d.names():
            raise member.Blocked('权限重置确认框域名不一致')
        evidence=self.capture('android-site-permission-reset-'+str(int(time.time()*1000)))
        self.d.click('android:id/button1')
        for _ in range(2):
            if not any('page_info' in name for name in self.d.names()):break
            self.d.request('/appium/device/press_keycode',{'keycode':4})
        self.web()
        self.event('重置本域名Chrome站点权限以实测拒绝流程',host=host,evidence=evidence)
        return evidence

    def matching_exit(self, logged=False):
        """记录剩余次数、启动一次匹配并在搜索中退出，核对余额恰好减一。"""
        first, second = ('N-06', 'N-07') if logged else ('M-11', 'M-12')
        self.web();self.dismiss_login();self.match_page()
        before = self.remaining()
        if not before:
            raise member.Blocked('当前未显示大于0的免费次数')
        start = int(time.time()*1000)
        self.native_tap_label('Start matching')
        self.handle_android_media_request()
        key = 'member_matching_starts' if logged else 'guest_matching_starts'
        self.data['context'][key] = self.data['context'].get(key, 0)+1
        self.save()
        end=time.monotonic()+10
        while time.monotonic()<end:
            body=self.text()
            if 'Exit' in body or 'Continue with Google' in body:
                break
            time.sleep(.1)
        entered='Exit' in body
        self.record(first, 'PASS' if entered else 'FAIL', f'启动前剩余 {before} 次；点击后：{body}', runner.CATALOG[first][3], start, self.web_capture(first+'-search'))
        start=int(time.time()*1000)
        if entered:
            self.native_tap_label('Exit');self.wait_text('Start matching')
            end=time.monotonic()+12;after=self.remaining()
            while after!=before-1 and time.monotonic()<end:
                time.sleep(.3);after=self.remaining()
            self.record(second, 'PASS' if after==before-1 else 'FAIL', f'搜索中 Exit 后返回 Match；次数 {before} → {after}，预期 {before-1}。', runner.CATALOG[second][3], start, self.web_capture(second+'-exit'))
        else:
            self.record(second, 'BLOCKED', '启动未进入连接页，无法执行搜索中 Exit。', ['查看启动后的页面'], start, self.web_capture(second+'-blocked'))
            self.dismiss_login()

    def media_positions(self):
        """读取可见视频的源、就绪状态及边界，用于识别本人画面和窗口交换。"""
        return self.js("return Array.from(document.querySelectorAll('video')).filter(e=>e.getBoundingClientRect().width>0).map(e=>({self:!e.currentSrc,src:e.currentSrc,ready:e.readyState,rect:e.getBoundingClientRect().toJSON()}))")

    def conversation(self, logged=False, case_ids=None):
        """Android短通话分别验证问候和交换；iOS沿用同通话流程，结束后均退出续配。"""
        message_id, swap_id = ('N-08', 'N-09') if logged else ('M-13', 'M-14')
        selected=set(case_ids or (message_id,swap_id))
        if not logged:
            selected={cid for cid in selected if getattr(self,'data',{}).get('results',{}).get(cid,{}).get('status')!='PASS'}
            if not selected:return
            self.web();self.dismiss_login()
            state=self.js("return {logged:Array.from(document.querySelectorAll('button')).some(e=>e.innerText.trim()==='Connect'&&e.getBoundingClientRect().height>0),navigation:['Discover','Match','Profile'].every(t=>Array.from(document.querySelectorAll('button')).some(e=>e.innerText.trim()===t&&e.getBoundingClientRect().height>0))}")
            reason=('M-13/M-14要求当前为游客；检测到已登录，保留账号状态，不自动退出登录补测' if state['logged'] else
                    '未确认游客导航状态，保留现场，不退出账号或启动匹配' if not state['navigation'] else None)
            if reason:
                evidence=self.failure_evidence('guest-conversation-precondition')
                for cid in sorted(selected):
                    self.record(cid,'BLOCKED',reason,['读取当前导航核对游客前置','前置不满足，保留账号状态并停止补测'],int(time.time()*1000),evidence)
                raise member.Blocked(reason)
        if self.d.platform=='android' and case_ids is None:
            for case_id in (message_id,swap_id):
                if self.data['results'].get(case_id,{}).get('status')!='PASS':
                    self.conversation(logged,case_ids={case_id})
            return
        if logged:self.web();self.dismiss_login()
        self.match_page()
        before=self.remaining()
        if not before:raise member.Blocked('无免费匹配次数')
        start=int(time.time()*1000)
        self.native_tap_label('Start matching')
        self.handle_android_media_request()
        key='member_matching_starts' if logged else 'guest_matching_starts'
        self.data['context'][key]=self.data['context'].get(key,0)+1;self.save()
        end=time.monotonic()+35
        try:
            while time.monotonic()<end:
                self.handle_android_media_request()
                body=self.text()
                if 'Continue with Google' in body:raise member.Blocked('游客启动匹配后出现登录弹窗')
                media=self.media_positions()
                # Android游客两项核对消息和窗口交换，不额外要求视频解码完成；原生授权遮挡仍独立检查。
                media_ready=(self.d.platform=='android' and not logged) or all(item['ready']>=2 for item in media)
                if self.js("return !!document.querySelector('textarea,input[placeholder]')") and len(media)>=2 and media_ready:
                    break
                time.sleep(.15)
            else:raise member.Blocked('35秒内未进入有输入框的双视频通话')
            if swap_id in selected and self.data['results'].get(swap_id,{}).get('status')!='PASS':
                self.run_case(swap_id, runner.CATALOG[swap_id][3], self.swap_video)
            if message_id in selected and self.data['results'].get(message_id,{}).get('status')!='PASS':
                self.run_case(message_id, runner.CATALOG[message_id][3], self.send_greeting)
            if logged:
                controls=self.js("return {text:document.body.innerText,buttons:Array.from(document.querySelectorAll('button')).map(e=>({text:e.innerText,html:e.outerHTML.slice(0,2500)})),icons:Array.from(document.querySelectorAll('use')).map(e=>e.getAttribute('href'))}")
                path=runner.OUT/'call-controls.json'
                path.write_text(json.dumps(controls,ensure_ascii=False,indent=2))
            if logged and self.data['context'].get('gift_unsubscribed_ready'):
                self.run_new('N-10', lambda:self.gift(False,self.data['context']['gift_unsubscribed_balance']))
        except member.Blocked as error:
            for cid in selected:
                self.record(cid,'BLOCKED',str(error),['有剩余次数时启动匹配，等待通话'],start,self.web_capture(cid+'-blocked'))
        finally:
            self.stop_call();self.dismiss_login()
            if not logged:
                # stop_call 已导航离开 Match，不从 Discover 猜测剩余次数。
                self.data['context']['guest_matching_remaining_final']='退出后已离开Match，未再次读取'
                self.save()

    def swap_video(self):
        """根据视频源识别本人画面，点击后比较两路窗口尺寸位置并保存前后证据。"""
        before=self.media_positions()
        own=next((x for x in before if x['self']),None)
        if own is None:raise member.Blocked('无法从视频源识别本人画面')
        ev=self.web_capture('video-swap-before')
        element=self.js("return Array.from(document.querySelectorAll('video')).find(e=>!e.currentSrc&&e.getBoundingClientRect().width>0)")
        self.d.request(f"/element/{element[base.ELEMENT_KEY]}/click",{})
        end=time.monotonic()+3
        while time.monotonic()<end:
            after=self.media_positions();own_after=next((x for x in after if x['self']),None)
            if own_after and own_after['rect']['width']!=own['rect']['width']:break
            time.sleep(.1)
        assert own_after and own_after['rect']['width']!=own['rect']['width'],'本人视频位置大小未变化'
        ev+=self.web_capture('video-swap-after')
        return '按视频源识别本人画面，点击后尺寸位置发生交换：'+json.dumps({'before':before,'after':after},ensure_ascii=False),ev

    def send_greeting(self):
        """发送随机简短英文问候，核对消息显示及输入框清空，不推断对方收信。"""
        selector='textarea,input[placeholder]'
        message=random.choice(('Hi!','Hello!','Hey!'))
        element=self.find_css(selector)
        self.d.request(f'/element/{element}/value',{'text':message})
        if self.d.platform=='android':
            self.d.request('/context',{'name':'NATIVE_APP'})
            if self.d.request('/appium/device/is_keyboard_shown'):
                self.d.request('/appium/device/hide_keyboard',{})
            self.web()
            time.sleep(.3)
        button=self.js("return Array.from(document.querySelectorAll('use')).find(e=>(e.getAttribute('href')||'').includes('send-active'))?.closest('button')")
        if not button:raise AssertionError('输入后未出现可用发送按钮')
        self.d.request(f"/element/{button[base.ELEMENT_KEY]}/click",{})
        end=time.monotonic()+5
        while time.monotonic()<end:
            body=self.text();value=self.js('return document.querySelector(arguments[0])?.value',selector)
            if message in body and not value:break
            time.sleep(.1)
        assert message in body and not value,'未确认消息显示及输入框清空'
        return f'发送随机英文问候 {message}，消息显示在当前会话，输入框清空。',self.web_capture('greeting-sent')

    def prepare_case_membership(self, case_id, state):
        """test首次真实订阅后改用后台恢复会员，绝不自动重复沙箱扣款。"""
        if case_id in ACTIVE_MEMBER_CASES and state.state=='unsubscribed' and self.policy.environment=='test':
            self.request_membership_extension(case_id)
        return state

    def request_membership_extension(self, case_id):
        """生成仅含当前UUID和固定Duration的后台待办，由浏览器UI完成后刷新确认。"""
        self.policy.require('membership_extension')
        context=self.data['context']
        if not context.get('initial_subscription_verified_at'):
            raise StateUnconfirmed('尚未确认本轮首次真实支付后的会员及300金币增量，禁止后台恢复代替首次支付')
        identity_path=self.path.parent/'current-user-identity.json'
        if not identity_path.is_file():
            raise StateUnconfirmed('恢复会员前须监听本手机当前用户信息响应并获取data.uuid')
        identity=json.loads(identity_path.read_text())
        from datetime import datetime
        captured=datetime.fromisoformat(identity['captured_at'].replace('Z','+00:00')).timestamp()
        if (identity.get('status')!=200 or identity.get('json_path')!='response.data.uuid'
                or identity.get('site_origin')!=origin(self.policy.base_url)
                or identity.get('nickname')!=context.get('member_identity')
                or not re.fullmatch(r'\d+',str(identity.get('uuid','')))
                or not 0<=time.time()-captured<=120):
            raise StateUnconfirmed('当前用户接口身份不匹配或已超过120秒，请重新采集，不沿用旧UUID')
        settings=configuration()['test_membership_recovery']
        request={'environment':'test','url':settings['url'],'uuid':identity['uuid'],
                 'duration':settings['duration'],'case_id':case_id,'requested_at':time.time(),
                 'identity_evidence':str(identity_path),'state':'pending_ui'}
        target=self.path.parent/'membership-extension-request.json'
        target.write_text(json.dumps(request,ensure_ascii=False,indent=2))
        self.event('test会员需后台恢复，停止重复沙箱支付',**request)
        raise StateUnconfirmed(f"请通过test后台UI为当前uuid={identity['uuid']}设置Duration={settings['duration']}，成功后刷新Profile再继续；待办：{target}")

    def confirm_membership_extension(self, evidence, response):
        """后台真实成功后刷新Profile；保存会员及余额，不将恢复计为支付用例通过。"""
        self.policy.require('membership_extension')
        target=self.path.parent/'membership-extension-request.json'
        request=json.loads(target.read_text())
        identity=json.loads((self.path.parent/'current-user-identity.json').read_text())
        if request['environment']!='test' or request['uuid']!=identity['uuid'] or request['duration']!=864000:
            raise StateUnconfirmed('后台恢复的环境、UUID或Duration与当前待办不一致')
        if not evidence or not all(Path(path).is_file() for path in evidence):
            raise StateUnconfirmed('后台恢复缺少实际执行证据')
        state=self.profile_membership(refresh=True)
        if state.state!='subscribed':raise StateUnconfirmed('后台操作后Profile未确认会员生效')
        request.update(state='verified',verified_at=time.time(),response=response,
                       profile=state.to_dict(),balance=self.balance(),evidence=evidence)
        target.write_text(json.dumps(request,ensure_ascii=False,indent=2))
        self.data['context']['membership_extension']=request
        self.event('后台恢复test会员已通过Profile复核',**request)
        return state

    def wait_any_text(self, labels, timeout=15):
        """等待当前页面出现任一已知语言文案，返回实际匹配文本。"""
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            body=self.text()
            found=next((label for label in labels if label in body),None)
            if found:return found
            time.sleep(.2)
        raise member.Blocked('未出现预期文案：'+ '/'.join(labels)+'；当前：'+self.text()[:600])

    def payment_journal(self):
        """读取本批持久支付记录，避免依靠进程内变量判断是否已付款。"""
        return PaymentJournal(self.path.parent)

    def begin_payment(self, kind, before, coins):
        """兼容历史付款检查点，先锁定单笔记录，再允许进入购买页面。"""
        context=self.data['context']
        legacy=('pending_subscription_balance','payment_confirmed_at','initial_subscription_verified_at') if kind=='subscription' else ('pending_gem_payment',)
        if any(key in context for key in legacy):
            raise StateUnconfirmed('已有付款检查点，停止重新购买；先核对原订单，缺少Toast不能通过重付补证据。')
        return self.payment_journal().begin(kind,before,coins)

    def observe_payment_notices(self):
        """在支付前监听商户可见Toast，保留短暂提示文本，不读凭据或接口正文。"""
        self.js("""if(!window.__webUiPaymentNotices){
            window.__webUiPaymentNotices=[];
            const observe=()=>{for(const e of document.querySelectorAll('.rv-toast,[role=status],[role=alert]')){
                const text=e.innerText?.trim();if(text&&e.getBoundingClientRect().height>0&&!window.__webUiPaymentNotices.includes(text)){
                    window.__webUiPaymentNotices.push(text);if(window.__webUiPaymentNotices.length>100)window.__webUiPaymentNotices.shift();}}};
            new MutationObserver(observe).observe(document.body,{subtree:true,childList:true,characterData:true});}
            window.__webUiPaymentNotices=[];""")

    def payment_notices(self):
        """合并支付期间捕获及当前仍可见的通知，避免截图后才查而漏掉Toast。"""
        return self.js("return [...new Set([...(window.__webUiPaymentNotices||[]),...Array.from(document.querySelectorAll('.rv-toast,[role=status],[role=alert]')).filter(e=>e.getBoundingClientRect().height>0).map(e=>e.innerText.trim()).filter(Boolean)])]")

    def sandbox_success(self, kind):
        """只操作明确显示测试模式的 Waffo 沙箱，不填写真实银行卡。"""
        self.policy.require('subscription_payment' if kind=='subscription' else 'coin_payment')
        journal=self.payment_journal();attempt=journal.read(kind)
        if not attempt or attempt['state'] not in {'prepared','submitted'}:
            raise StateUnconfirmed('本笔已成功或缺少准备记录；只能使用verify-payment补核验，禁止再次提交。')
        self.d.request('/context',{'name':'NATIVE_APP'})
        end=time.monotonic()+30;provider=None
        while time.monotonic()<end:
            provider=self.d.select_page('https://cashier-sandbox.waffo.com/')
            if provider:break
            time.sleep(.3)
        if not provider:raise member.Blocked('支付后30秒内未识别到沙箱收银台')
        self.d.activate_page(provider)
        actual=self.js('return location.origin')
        if actual!='https://cashier-sandbox.waffo.com':raise member.Blocked('第三方不是已确认的测试支付域名')
        if attempt['state']=='prepared':
            self.wait_any_text(('测试模式','Test Mode'))
            self.wait_any_text(('不会处理真实付款','No real payment will be processed'))
        english='Test Mode' in self.text()
        if attempt['state']=='prepared':
            for label in ('Add New Card','添加新卡'):
                if label in self.text():
                    self.click_label(label)
                    break
            self.wait_text('Success' if english else '成功');self.click_label('Success' if english else '成功')
            if self.js("return document.querySelector('#required-need-save')?.checked===true"):
                self.click_css('#required-need-save')
            self.wait_css('button[type=submit][data-dd-action-name=pay]')
            if self.d.platform=='android':
                self.d.request('/context',{'name':'NATIVE_APP'})
                self.android_scroll_to_label('Pay' if english else '支付')
                journal.update(kind,state='submitted',submitted_at=time.time())
                self.d.click('Pay' if english else '支付');self.d.focus_page(provider)
            else:
                journal.update(kind,state='submitted',submitted_at=time.time())
                self.tap_current_label('Pay' if english else '支付')
        self.wait_any_text(('支付成功','Payment successful','Payment Successful','Payment succeeded','Payment Succeeded'),45)
        confirmed=time.time()
        journal.update(kind,state='confirmed',confirmed_at=confirmed)
        if kind=='subscription':
            self.data['context']['payment_confirmed_at']=confirmed
        self.save()
        evidence=self.capture(kind+'-sandbox-success-'+str(int(confirmed)))
        # 完成按钮可能关闭收银台标签；之后仅从原生上下文重新发现商户。
        if self.d.platform=='android':
            self.d.request('/context',{'name':'NATIVE_APP'})
            self.d.click('Done' if english else '完成')
        else:self.tap_current_label('Done' if english else '完成',restore=False)
        self.d.request('/context',{'name':'NATIVE_APP'})
        if self.d.platform=='android':
            # 在切换WebDriver窗口前检查真实前台地址栏，避免切换本身造成回站假象。
            host=self.policy.base_url.split('/')[2]
            deadline=time.monotonic()+10;automatic=False
            while time.monotonic()<deadline:
                addresses=[n.attrib.get('text','') for n in self.d.tree().iter()
                           if n.attrib.get('resource-id')=='com.android.chrome:id/url_bar']
                if any(address.removeprefix('https://').split('/')[0]==host for address in addresses):
                    automatic=True;break
                time.sleep(.2)
            if automatic and not self.visible_merchant(5):
                raise member.Blocked('原生前台已回站，但无法绑定商户网页上下文')
        else:automatic=self.visible_merchant(10)
        if not automatic:
            # 错误回站可能落到Google账号列表；只记录检查结果，不把无关账号截图带进报告。
            journal.update(kind,automatic=False)
            self.event('支付成功后未自动回到商户，记录现场后手动回站',kind=kind,evidence=evidence)
            self.native_open_url(self.policy.base_url+'lobby/profile')
            if not self.visible_merchant(25):raise member.Blocked('沙箱支付成功，手动回站后仍无法找到前台商户页')
        notices=self.payment_notices()
        journal.update(kind,automatic=automatic,notices=notices,success_dialog='Premium unlocked' in self.text(),evidence=evidence)
        return confirmed,automatic,evidence

    def card_payment(self):
        """在商户支付方式面板选择卡支付，保留Android原生与iOS触屏分支。"""
        self.wait_text('Credit Card')
        if self.d.platform=='android':
            self.d.request('/context',{'name':'NATIVE_APP'})
            self.wait_native('Credit Card',5);self.d.click('Credit Card');self.web()
            self.wait_text('Pay')
            self.d.request('/context',{'name':'NATIVE_APP'});self.d.click('Pay')
        else:
            self.click_label('Credit Card');self.wait_text('Pay')
            self.tap_current_label('Pay',restore=False)

    def gem_tiers(self):
        """通过Profile金币入口打开商城，读取真实可见的金币档位和价格。"""
        if self.js('return location.pathname')!='/lobby/topup':
            self.profile_page()
            element=self.js("return Array.from(document.querySelectorAll('button')).find(e=>e.textContent.includes('Gems Store'))")
            if not element:raise member.Blocked('Profile金币入口尚未加载')
            self.d.request(f'/element/{element[base.ELEMENT_KEY]}/click',{})
        self.wait_text('Gem Store');self.wait_css('li[data-id]')
        return self.js("return Array.from(document.querySelectorAll('[data-id]')).filter(e=>e.getBoundingClientRect().height>0&&/[$¥￥€£]\\s*\\d[\\d,]*(?:\\.\\d{2})?/.test(e.innerText)).map(e=>({id:e.dataset.id,text:e.innerText.trim()}))")

    def gem_store(self):
        """断言会员金币商城有七个不同且带价格的档位，不以页面空壳通过。"""
        tiers=self.gem_tiers()
        assert len(tiers)==7 and len({tier['id'] for tier in tiers})==7,f'实际金币档位：{tiers}'
        assert all(re.search(r'[$¥￥€£]\s*\d[\d,]*(?:\.\d{2})?',tier['text']) for tier in tiers),'部分档位未显示价格'
        self.data['context']['gem_tiers']=tiers;self.save()
        return '进入Gem Store，实际展示7个不同的金币档位：'+json.dumps(tiers,ensure_ascii=False),self.web_capture('S-02-seven-gem-tiers')

    def prepare_first_video_cache(self):
        """首次Video前仅清理被测源业务缓存，保留设备和认证，再核对同一账号及会员。"""
        self.policy.require('subscribed_video')
        if self.data['context'].get('first_video_attempt'):
            raise member.Blocked('本轮已点击首次Video，不能清缓存后追溯首次弹窗')
        expected_origin=origin(self.policy.base_url)
        checkpoint=self.data['context'].get('first_video_cache')
        if checkpoint and checkpoint.get('origin')!=expected_origin:
            raise StateUnconfirmed('缓存清理检查点属于其他网站')
        if not checkpoint:
            identity=self.capture_current_identity()
            checkpoint={'origin':expected_origin,'uuid':identity['uuid'],'evidence':self.web_capture('S-04-before-cache')}
            cleared=self.d.request('/execute/async',{'script':"""
                const expected=arguments[0],done=arguments[arguments.length-1];
                (async()=>{if(location.origin!==expected)throw Error('当前页面不是被测源');
                const keys=['ios_deviceId','android_deviceId','client_id','deviceId'];
                const devices=keys.map(k=>localStorage.getItem(k)).filter(v=>v!==null).map(v=>{try{return JSON.parse(v)}catch{return v}});
                if(!devices.length||new Set(devices).size!==1||devices.some(v=>typeof v!=='string'||!v))throw Error('设备标识缺失或冲突');
                const authKey=k=>/auth|token|session|^appInfo$|^userInfo$|^isUserSet$|^canlogin$|^user_age_tip$|^x-webapp-id$/i.test(k);
                const retained=Object.fromEntries(Object.keys(localStorage).filter(k=>keys.includes(k)||k.startsWith('firebase:')||authKey(k)).map(k=>[k,localStorage.getItem(k)]));
                const retainedSession=Object.fromEntries(Object.keys(sessionStorage).filter(authKey).map(k=>[k,sessionStorage.getItem(k)]));
                const cachesBefore=await caches.keys();await Promise.all(cachesBefore.map(k=>caches.delete(k)));
                const workers=await navigator.serviceWorker.getRegistrations();await Promise.all(workers.map(w=>w.unregister()));
                localStorage.clear();sessionStorage.clear();for(const [k,v] of Object.entries(retained))localStorage.setItem(k,v);for(const [k,v] of Object.entries(retainedSession))sessionStorage.setItem(k,v);
                return {origin:location.origin,cleared_at:Date.now(),retained_keys:Object.keys(retained),cache_count:cachesBefore.length,
                    service_worker_count:workers.length,session_count:sessionStorage.length,retained_session_keys:Object.keys(retainedSession),retained_auth_storage:['cookies','indexeddb','auth_local_and_session_keys'],
                    http_cache:'未清理其他站点HTTP缓存；本源刷新重新核对账号'};})().then(value=>done({result:value}),error=>done({error:error.message}));
                """,'args':[expected_origin]})
            if cleared.get('error'):raise StateUnconfirmed(cleared['error'])
            checkpoint.update(cleared['result'])
            self.data['context']['first_video_cache']=checkpoint;self.save()
            self.d.request('/refresh',{});self.wait_text('Gems Store')
        after=self.capture_current_identity()
        if after['uuid']!=checkpoint['uuid']:raise StateUnconfirmed('清理本站缓存后账号UUID变化，未点击Video')
        self.require_subscribed()
        checkpoint['verified_at']=time.time();checkpoint['evidence']+=self.web_capture('S-04-after-cache')
        target=self.path.parent/'S-04-cache-preflight.json'
        target.write_text(json.dumps(checkpoint,ensure_ascii=False,indent=2))
        if str(target) not in checkpoint['evidence']:checkpoint['evidence'].append(str(target))
        self.save();self.event('首次Video前本站缓存清理及账号会员复核完成',evidence=checkpoint['evidence'])
        return checkpoint

    def first_video(self):
        """清理本站缓存后点击首次Video，核对余额提示；列表未加载时不消耗首次点击。"""
        checkpoint=self.prepare_first_video_cache()
        self.profile_page();before=self.balance();self.list_page()
        self.data['context']['first_video_attempt']={'at':time.time(),'balance':before};self.save()
        try:
            self.click_text('Video');self.wait_text('Balance',8)
            panel=self.js("return Array.from(document.querySelectorAll('div')).filter(e=>e.getBoundingClientRect().height>0&&e.innerText.includes('Balance')&&e.innerText.includes('Call rate:')).sort((a,b)=>a.innerText.length-b.innerText.length)[0]?.innerText")
            assert panel and re.search(r'\b'+str(before)+r'\b',panel),'首次Video余额提示与Profile不符：'+str(panel)
            return f'首次Video显示Balance，余额为{before}；实际面板：{panel}',checkpoint['evidence']+self.web_capture('S-04-balance-dialog')
        finally:
            self.d.request('/url',{'url':self.policy.base_url+'lobby/profile'});self.wait_text('Gems Store')

    def subscribed_video_dial(self):
        """首次余额提示处理后最多尝试三名真实卡片，确认拨号或通话并退出。"""
        if self.data['results'].get('S-04',{}).get('status')!='PASS':
            raise member.Blocked('S-04尚未完成，保留首次Video；Discover无可用卡片时不能拨号')
        self.profile_page();before=self.balance()
        if before<80:raise member.Blocked('当前余额不足80金币，不能拨打普通视频')
        self.list_page()
        targets=self.cards()[:3]
        for index,target in enumerate(targets):
            buttons=self.js("return Array.from(document.querySelectorAll('button')).filter(e=>e.innerText.trim()==='Video')")
            if index>=len(buttons):raise member.Blocked('卡片数量变化，停止避免点击错误目标')
            self.d.request(f"/element/{buttons[index][base.ELEMENT_KEY]}/click",{})
            try:
                deadline=time.monotonic()+15
                while time.monotonic()<deadline:
                    body=self.text()
                    if re.search(r'offline|not online',body,re.I):
                        self.event('主播离线，切换下一名',target=target,evidence=self.web_capture('S-06-offline'));break
                    if re.search(r'calling|connecting|\b00:\d{2}\b',body,re.I):
                        return '余额'+str(before)+'，点击'+target['name']+'的Video后进入拨号/通话。',self.web_capture('S-06-dial')
                    time.sleep(.15)
                else:raise member.Blocked('未确认拨号或明确离线，不重复点击')
            finally:
                if self.js("return Array.from(document.querySelectorAll('video')).some(e=>e.getBoundingClientRect().height>20)"):
                    self.stop_call()
            self.d.request('/url',{'url':self.policy.base_url+'lobby/anchor'});self.list_page()
        raise member.Blocked('最多三名均离线，未接通')

    def topup_gems(self):
        """选择最小真实金币档位完成一次沙箱充值，分别核对增量与成功通知。"""
        self.policy.require('coin_payment')
        if self.payment_journal().read('coin'):
            raise StateUnconfirmed('本批已有金币支付记录，只能补核验，禁止重复充值。')
        self.profile_page();before=self.balance();tiers=self.gem_tiers()
        if not tiers:raise member.Blocked('金币商城未显示可识别的价格档位，未创建或提交支付订单')
        for tier in tiers:
            numbers=[int(line.replace(',','')) for line in tier['text'].splitlines() if re.fullmatch(r'\d[\d,]*',line)]
            if len(numbers)!=1:raise member.Blocked('无法唯一读取金币档位承诺数：'+tier['text'])
            tier['coins']=numbers[0]
        target=min(tiers,key=lambda item:item['coins'])
        self.begin_payment('coin',before,target['coins'])
        self.observe_payment_notices()
        self.data['context']['pending_gem_payment']={'before':before,'tier':target};self.save()
        self.web_capture('S-03-before-payment')
        self.click_css('[data-id="'+target['id']+'"]');self.card_payment()
        self.sandbox_success('coin')
        return self.verify_payment('coin')

    def gift(self, subscribed, before):
        """在已连接通话中发送一份20金币星星，核对会话礼物或金币不足提示。"""
        self.click_css('svg:has(use[href="#icon-calling/gift-call"])')
        selector='[data-rsbs-overlay] li:has(img[src$="gift-star.png"])'
        self.wait_css(selector)
        price=self.js("return document.querySelector(arguments[0]).innerText.trim()",selector)
        if price!='20' or before<0 or (subscribed and before<20):raise member.Blocked('星星价格或可用余额不满足本次送礼前置')
        evidence=self.web_capture(('S-07' if subscribed else 'N-10')+'-before-send')
        self.click_css(selector)
        end=time.monotonic()+5;samples=[]
        while time.monotonic()<end:
            sample=self.js("return {text:document.body.innerText,toasts:Array.from(document.querySelectorAll('.rv-toast,[role=alert],[role=status]')).map(e=>e.innerText),gifts:Array.from(document.querySelectorAll('img[src$=\"gift-star.png\"]')).filter(e=>!e.closest('[role=dialog],[data-rsbs-overlay]')).map(e=>({text:e.parentElement.parentElement.innerText,html:e.parentElement.parentElement.outerHTML}))}")
            samples.append(sample)
            if sample['gifts'] or any(re.search(r'insufficient|not enough|金币不足',t,re.I) for t in sample['toasts']):break
            time.sleep(.15)
        label='S-07' if subscribed else 'N-10'
        path=runner.OUT/(label+'-gift-result.json');path.write_text(json.dumps(samples,ensure_ascii=False,indent=2))
        evidence+=[str(path)]+self.web_capture(label+'-after-send')
        if subscribed or before>=20:
            assert sample['gifts'],'发送后会话区未出现星星礼物消息；'+sample['text'][:700]
            return ('已订阅' if subscribed else '未订阅')+'且余额'+str(before)+'，通话连接成功后发送20金币星星，会话出现礼物消息：'+json.dumps(sample,ensure_ascii=False),evidence
        assert not sample['gifts'] and any(re.search(r'insufficient|not enough|金币不足',t,re.I) for t in sample['toasts']),'未捕获金币不足提示或礼物反而发送成功'
        return '未订阅且余额不足20，发送星星失败并出现金币不足提示。',evidence

    def subscribed_gift_call(self):
        """会员最多启动一次匹配，连接后送礼，结束时关闭礼物面板并退出自动续配。"""
        self.profile_page();before=self.balance()
        if before<20:raise member.Blocked('余额不足20，无法测试已订阅送礼成功')
        self.click_text('Match');self.wait_text('Start matching');self.native_tap_label('Start matching')
        context=self.data['context'];context['subscribed_matching_starts']=context.get('subscribed_matching_starts',0)+1;self.save()
        end=time.monotonic()+25
        try:
            while time.monotonic()<end:
                media=self.media_positions()
                if len(media)>=2 and all(item['ready']>=2 for item in media) and 'Call connecting' not in self.text():
                    return self.gift(True,before)
                if 'Continue with Google' in self.text():raise member.Blocked('匹配中登录弹窗阻塞')
                time.sleep(.15)
            raise member.Blocked('本次匹配25秒内未确认双视频连接成功，不反复启动匹配')
        finally:
            self.stop_call()

    def subscribe(self, renewal=False):
        """仅在test未订阅时执行Week卡支付，核对回站、会员生效及增加300金币。"""
        self.policy.require('subscription_payment')
        if renewal:
            raise StateUnconfirmed('后续会员恢复使用test后台UUID + Duration=864000，不再重复沙箱订阅')
        if self.payment_journal().read('subscription'):
            raise StateUnconfirmed('本批已有订阅支付记录，只能补核验，禁止重复付款。')
        state=self.profile_membership(refresh=True)
        if state.state!='unsubscribed':raise StateUnconfirmed('测试订阅前未确认未订阅状态')
        before=self.balance();self.begin_payment('subscription',before,300)
        self.payment_journal().update('subscription',membership_before=state.to_dict())
        self.data['context']['pending_subscription_balance']=before;self.save()
        self.observe_payment_notices();self.premium_entry();self.click_label('1 week')
        label=self.js("return Array.from(document.querySelectorAll('[data-premium-content] button')).find(e=>e.innerText.startsWith('Get ')&&e.innerText.toLowerCase().includes('week'))?.innerText.trim()")
        if not label:raise member.Blocked('Week 支付按钮未加载')
        self.tap_current_label(label)
        self.card_payment()
        return self.complete_subscription(before,renewal)

    def complete_subscription(self, before, renewal=False):
        """完成已进入的测试订阅订单，统一核对回站、会员及300金币增量。"""
        if renewal:raise StateUnconfirmed('会员恢复走UUID后台，不提交第二笔支付。')
        attempt=self.payment_journal().read('subscription')
        if not attempt or attempt['before']!=before:raise StateUnconfirmed('原订单余额与准备记录不符，停止提交。')
        if attempt['state'] in {'prepared','submitted'}:self.sandbox_success('subscription')
        return self.verify_payment('subscription')

    def verify_payment(self, kind):
        """只补核验已成功原订单；回站或Toast缺证据时阻塞，绝不再点击Pay。"""
        self.policy.require('subscription_payment' if kind=='subscription' else 'coin_payment')
        journal=self.payment_journal();attempt=journal.read(kind)
        if not attempt or attempt['state'] not in {'confirmed','verified'}:
            raise StateUnconfirmed('未保存真实支付成功节点，只能核对原订单现场，不能重新付款。')
        if kind=='subscription' and attempt.get('membership_before',{}).get('state')!='unsubscribed':
            raise StateUnconfirmed('缺少原订单付款前未订阅的检查点，不能将补核验判为订阅用例通过。')
        evidence=list(attempt.get('evidence',[]))
        if not self.visible_merchant(3):
            self.native_open_url(self.policy.base_url+'lobby/profile')
            if not self.visible_merchant(5):raise StateUnconfirmed('原订单已成功，但商户页面尚不可读，停止补核验。')
        success=attempt.get('success_dialog',False)
        if kind=='subscription' and not success:
            # 只在仍有可能看到原成功弹窗时短暂观察，避免空等12秒。
            end=time.monotonic()+3
            while time.monotonic()<end:
                if 'Premium unlocked' in self.text():success=True;break
                time.sleep(.2)
        notices=list(dict.fromkeys(attempt.get('notices',[])+self.payment_notices()))
        journal.update(kind,success_dialog=success,notices=notices)
        evidence+=self.web_capture(('S-01' if kind=='subscription' else 'S-03')+'-original-payment-return')
        if 'Start Exploring' in self.text():self.click_label('Start Exploring')
        state=self.profile_membership(refresh=True);after=self.balance()
        evidence+=self.web_capture(('S-01' if kind=='subscription' else 'S-03')+'-original-payment-balance')
        expected=attempt['before']+attempt['coins']
        details=f"原订单成功时间{attempt['confirmed_at']}；自动回站={attempt.get('automatic','未捕获')}；金币{attempt['before']}→{after}，预期{expected}；会员={state.state}；有效期={state.active_until}；成功弹窗={success}；通知={notices}。"
        journal.update(kind,after=after,profile=state.to_dict(),evidence=evidence)
        self.event('仅核验原订单，未重复支付',kind=kind,details=details,evidence=evidence)
        if after!=expected or (kind=='subscription' and state.state!='subscribed'):
            raise StateUnconfirmed('原订单到账或会员尚未确认，不重复充值；'+details)
        if kind=='subscription':
            self.data['context']['initial_subscription_verified_at']=attempt['confirmed_at'];self.save()
        if attempt['state']=='confirmed':journal.update(kind,state='verified',verified_at=time.time())
        notification_ok=success if kind=='subscription' else any(re.search(r'success|added|received|got|congrat|成功',notice,re.I) for notice in notices)
        if not attempt.get('automatic') or not notification_ok:
            raise member.Blocked('原订单已到账；回站或成功提示证据不完整，禁止以重付补证据；'+details)
        return details,evidence

    def verify_payment_case(self, kind):
        """补核验不要求回到未订阅态，也不再次走购买前的会员条件分支。"""
        case_id='S-01' if kind=='subscription' else 'S-03'
        return self.run_case(case_id,
            ['读取本批原支付成功检查点','仅核对已保存的回站提示和当前会员及余额，不提交支付'],
            lambda:self.verify_payment(kind),
            operation='subscription_payment' if kind=='subscription' else 'coin_payment',check_membership=False)


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('phase',choices=('permissions-guest','permissions-member','exit-guest','exit-member','call-guest','call-member','subscribe','verify-subscription','verify-topup','inspect'))
    p.add_argument('--env',choices=('test','prod'),default='test');p.add_argument('--url');p.add_argument('--batch-dir',type=Path)
    p.add_argument('--session-file',type=Path,help='指定Android或iOS的独立Appium连接记录')
    args=p.parse_args()
    session_file=args.session_file or runner.ROOT.parent/'work/current-appium-session.json'
    with device_run(session_file):
        configure_batch(args.batch_dir);s=Suite(environment=args.env,url=args.url,session_file=session_file)
        s.d.health();s.data['context'].pop('session_blocker',None);s.save();s.web()
        if args.phase.startswith('permissions'):s.permissions('G' if args.phase.endswith('guest') else 'N')
        elif args.phase.startswith('exit'):s.matching_exit(args.phase.endswith('member'))
        elif args.phase.startswith('call'):s.conversation(args.phase.endswith('member'))
        elif args.phase=='subscribe':s.run_new('S-01',s.subscribe)
        elif args.phase=='verify-subscription':s.verify_payment_case('subscription')
        elif args.phase=='verify-topup':s.verify_payment_case('coin')
        else:print(json.dumps({'url':s.js('return location.href'),'body':s.text()},ensure_ascii=False))
