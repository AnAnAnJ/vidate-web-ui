"""离线复现重复付款、登录空等、页面占位值和驱动断线，不操作真机。"""
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import Mock, patch

import web_ui_complete_suite as complete
import web_ui_full_rerun as full
import web_ui_native_login_watch as login
from web_ui_account_state import Membership, StateUnconfirmed
from web_ui_environment import ExecutionPolicy
from web_ui_execution_state import PaymentJournal, device_run, exclusive_file
from web_ui_mobile_driver import LegacyDriver, MobileDriver, MobileSessionUnavailable


class GemStoreTests(unittest.TestCase):
    def test_localized_prices_remain_valid_on_both_platforms(self):
        """人民币和美元档位均可识别，避免地区币种导致Android或iOS商城误报。"""
        for platform in ('android','ios'):
            for currency in ('¥','$'):
                with self.subTest(platform=platform,currency=currency):
                    suite=complete.Suite.__new__(complete.Suite)
                    suite.d=Mock(platform=platform)
                    suite.gem_tiers=Mock(return_value=[{'id':str(i),'text':f'{100*i}\n{currency}{i}.99'} for i in range(1,8)])
                    suite.data={'context':{}};suite.save=Mock();suite.web_capture=Mock(return_value=[])
                    suite.gem_store()
                    self.assertEqual(len(suite.data['context']['gem_tiers']),7)

    def test_empty_store_never_starts_payment(self):
        """空商城应清晰阻塞，不能创建订单或落入min空集合异常。"""
        suite=complete.Suite.__new__(complete.Suite)
        suite.policy=ExecutionPolicy.create('test')
        suite.payment_journal=Mock(return_value=Mock(read=Mock(return_value=None)))
        suite.profile_page=Mock();suite.balance=Mock(return_value=300);suite.gem_tiers=Mock(return_value=[])
        suite.begin_payment=Mock()
        with self.assertRaisesRegex(complete.member.Blocked,'未显示可识别的价格档位'):suite.topup_gems()
        suite.begin_payment.assert_not_called()


class GuestConversationTests(unittest.TestCase):
    def test_later_logged_precondition_never_overwrites_guest_pass(self):
        """游客消息已通过后，即使切窗补测被登录状态阻塞，也不能覆盖消息通过结果。"""
        suite=complete.Suite.__new__(complete.Suite)
        suite.d=Mock(platform='android');suite.web=Mock();suite.dismiss_login=Mock()
        suite.data={'results':{'M-13':{'status':'PASS'}}}
        suite.js=Mock(return_value={'logged':True,'navigation':True})
        suite.record=Mock();suite.failure_evidence=Mock(return_value=[])
        with self.assertRaises(complete.member.Blocked):suite.conversation(False)
        self.assertEqual(suite.record.call_count,1)
        self.assertEqual(suite.record.call_args.args[:2],('M-14','BLOCKED'))
        self.assertEqual(suite.data['results']['M-13']['status'],'PASS')

    def test_guest_and_member_captures_never_overwrite_each_other(self):
        """游客与登录通话使用同一截图标签时，也必须保留两份独立DOM和图片。"""
        with tempfile.TemporaryDirectory() as folder:
            suite=complete.runner.Runner.__new__(complete.runner.Runner)
            suite.js=Mock(side_effect=[{'text':'guest Hello!'}, {'text':'member Hey!'}])
            suite.d=Mock();suite.web=Mock()
            def save_image(label):
                """写入不同标签，模拟原生截图落盘且不连接设备。"""
                target=Path(folder)/(label+'.png');target.write_bytes(label.encode());return str(target)
            suite.d.screenshot.side_effect=save_image
            with patch.object(complete.runner,'OUT',Path(folder)):
                first=complete.runner.Runner.web_capture(suite,'greeting-sent')
                second=complete.runner.Runner.web_capture(suite,'greeting-sent')
            self.assertNotEqual(first[0],second[0]);self.assertNotEqual(first[1],second[1])
            self.assertEqual(json.loads(Path(first[1]).read_text())['text'],'guest Hello!')
            self.assertEqual(json.loads(Path(second[1]).read_text())['text'],'member Hey!')

    def test_logged_account_is_preserved_without_matching_on_both_phones(self):
        """Android/iOS补测游客通话时，已登录必须在任何匹配或账号操作前阻塞。"""
        for platform in ('android','ios'):
            with self.subTest(platform=platform):
                suite=complete.Suite.__new__(complete.Suite)
                suite.d=Mock(platform=platform);suite.web=Mock();suite.dismiss_login=Mock()
                suite.js=Mock(return_value={'logged':True,'navigation':True});suite.match_page=Mock()
                suite.record=Mock();suite.failure_evidence=Mock(return_value=[])
                with self.assertRaisesRegex(complete.member.Blocked,'保留账号状态'):
                    suite.conversation(False,case_ids={'M-13'})
                suite.match_page.assert_not_called();suite.d.request.assert_not_called()
                self.assertEqual(suite.record.call_args.args[:2],('M-13','BLOCKED'))

    def test_unknown_guest_navigation_does_not_start_matching(self):
        """缺少游客导航证据时保留现场，不以退出账号作为恢复手段。"""
        suite=complete.Suite.__new__(complete.Suite)
        suite.d=Mock(platform='android');suite.web=Mock();suite.dismiss_login=Mock()
        suite.js=Mock(return_value={'logged':False,'navigation':False});suite.match_page=Mock()
        suite.record=Mock();suite.failure_evidence=Mock(return_value=[])
        with self.assertRaisesRegex(complete.member.Blocked,'未确认游客导航'):
            suite.conversation(False,case_ids={'M-14'})
        suite.match_page.assert_not_called();suite.d.request.assert_not_called()


class VideoPreconditionTests(unittest.TestCase):
    def test_guest_profile_cannot_supply_membership_uuid(self):
        """清缓存退出登录时不能把游客资料页当作原会员，阻止错误UUID恢复。"""
        suite=complete.Suite.__new__(complete.Suite)
        suite.profile_page=Mock();suite.text=Mock(return_value='Edit Info\nGuest\nUpgrade to Premium')
        suite.js=Mock(return_value=False)
        with self.assertRaisesRegex(StateUnconfirmed,'不能采集游客UUID'):suite.capture_current_identity()
        self.assertEqual(suite.js.call_count,1)

    def test_consumed_first_video_never_clears_cache_again(self):
        """两个手机平台都不能通过清缓存重造已消耗的首次Video证据。"""
        for platform in ('android','ios'):
            with self.subTest(platform=platform):
                suite=complete.Suite.__new__(complete.Suite)
                suite.policy=ExecutionPolicy.create('test');suite.d=Mock(platform=platform)
                suite.data={'context':{'first_video_attempt':{'at':1}}}
                with self.assertRaisesRegex(complete.member.Blocked,'已点击首次Video'):suite.prepare_first_video_cache()
                suite.d.request.assert_not_called()

    def test_missing_discover_card_does_not_consume_first_video(self):
        """列表加载失败必须保留首次点击机会，不能记录成已点击弹窗失败。"""
        suite=complete.Suite.__new__(complete.Suite)
        suite.prepare_first_video_cache=Mock(return_value={'evidence':[]})
        suite.profile_page=Mock();suite.balance=Mock(return_value=300)
        suite.list_page=Mock(side_effect=complete.member.Blocked('Discover未加载'))
        suite.data={'context':{}};suite.click_text=Mock()
        with self.assertRaises(complete.member.Blocked):suite.first_video()
        self.assertNotIn('first_video_attempt',suite.data['context'])
        suite.click_text.assert_not_called()


class GiftBalanceTests(unittest.TestCase):
    def test_unsubscribed_gift_uses_available_balance(self):
        """未订阅但余额足够可以成功；余额不足必须提示且不能出现礼物消息。"""
        cases=((300,True,[]),(0,False,['Insufficient gems']))
        for before,success,toasts in cases:
            with self.subTest(before=before),tempfile.TemporaryDirectory() as folder:
                suite=complete.Suite.__new__(complete.Suite)
                suite.click_css=Mock();suite.wait_css=Mock();suite.web_capture=Mock(return_value=[])
                sample={'gifts':[{'text':'You have sent a'}] if success else [],'text':'call','toasts':toasts}
                suite.js=Mock(side_effect=['20',sample])
                with patch.object(complete.runner,'OUT',Path(folder)):
                    actual,_=suite.gift(False,before)
                self.assertIn('礼物消息' if success else '金币不足',actual)


class PaymentRecoveryTests(unittest.TestCase):
    def instance(self, folder, platform='android'):
        """构造支付页面桩，任何真实设备请求均由Mock替代。"""
        suite=complete.Suite.__new__(complete.Suite)
        suite.path=Path(folder)/'report.json'
        suite.data={'context':{}}
        suite.policy=ExecutionPolicy.create('test')
        suite.d=Mock(platform=platform,assert_no_system_permission_dialog=Mock())
        suite.d.select_page.return_value={'context':'WEBVIEW','window':'payment'}
        host=suite.policy.base_url.split('/')[2]
        suite.d.tree.return_value=ET.fromstring('<hierarchy><node resource-id="com.android.chrome:id/url_bar" text="'+host+'"/></hierarchy>')
        suite.js=Mock(side_effect=lambda script,*args:'https://cashier-sandbox.waffo.com' if 'location.origin' in script else False)
        suite.text=Mock(return_value='Test Mode\nPremium unlocked')
        for name in ('wait_any_text','wait_text','wait_css','click_label','click_css','tap_current_label',
                     'android_scroll_to_label','save','event','native_open_url'):
            setattr(suite,name,Mock())
        suite.capture=Mock(return_value=[])
        suite.web_capture=Mock(return_value=[])
        suite.visible_merchant=Mock(return_value=True)
        suite.payment_notices=Mock(return_value=[])
        suite.profile_membership=Mock(return_value=Membership('subscribed',100,'23-10-2026'))
        suite.balance=Mock(return_value=400)
        return suite

    def confirmed(self, suite, kind='subscription', automatic=True):
        """写入原订单各节点，订阅明确保存付款前未订阅证据。"""
        journal=suite.payment_journal()
        journal.begin(kind,100,300)
        journal.update(kind,membership_before=Membership('unsubscribed',50).to_dict())
        journal.update(kind,state='submitted')
        journal.update(kind,state='confirmed',confirmed_at=100,automatic=automatic,success_dialog=True)
        return journal

    def test_restarted_process_cannot_repeat_payment(self):
        """重建记录对象模拟进程恢复，已提交订单不能再准备或提交。"""
        with tempfile.TemporaryDirectory() as folder:
            journal=PaymentJournal(folder)
            journal.begin('subscription',0,300)
            journal.update('subscription',state='submitted')
            restored=PaymentJournal(folder)
            with self.assertRaises(StateUnconfirmed):restored.begin('subscription',0,300)
            with self.assertRaises(StateUnconfirmed):restored.update('subscription',state='submitted')
            self.assertEqual(restored.read('subscription')['state'],'submitted')

    def test_both_platforms_save_submission_before_pay(self):
        """Android和iOS都必须先保存提交状态再点击Pay。"""
        for platform in ('android','ios'):
            with self.subTest(platform=platform),tempfile.TemporaryDirectory() as folder:
                suite=self.instance(folder,platform)
                suite.payment_journal().begin('subscription',100,300)
                def click(label,**kwargs):
                    """在真正提交位置检查可恢复记录已写入磁盘。"""
                    if label=='Pay':self.assertEqual(PaymentJournal(folder).read('subscription')['state'],'submitted')
                suite.d.click.side_effect=click
                suite.tap_current_label.side_effect=click
                suite.sandbox_success('subscription')
                calls=suite.d.click.call_args_list if platform=='android' else suite.tap_current_label.call_args_list
                self.assertEqual(sum(call.args[0]=='Pay' for call in calls),1)
                self.assertEqual(suite.payment_journal().read('subscription')['state'],'confirmed')

    def test_submitted_recovery_observes_success_without_clicking_pay(self):
        """原提交结果未知时只观察成功页，两个平台均不能再点击Pay。"""
        for platform in ('android','ios'):
            with self.subTest(platform=platform),tempfile.TemporaryDirectory() as folder:
                suite=self.instance(folder,platform)
                journal=suite.payment_journal();journal.begin('coin',100,300)
                journal.update('coin',state='submitted')
                suite.sandbox_success('coin')
                suite.click_label.assert_not_called()
                for call in suite.d.click.call_args_list+suite.tap_current_label.call_args_list:
                    self.assertNotEqual(call.args[0],'Pay')

    def test_screenshot_failure_does_not_lose_success_checkpoint(self):
        """成功页截图失败仍保留已确认节点，恢复时只能补核验。"""
        with tempfile.TemporaryDirectory() as folder:
            suite=self.instance(folder)
            suite.payment_journal().begin('subscription',100,300)
            suite.capture.side_effect=RuntimeError('截图中断')
            with self.assertRaisesRegex(RuntimeError,'截图中断'):suite.sandbox_success('subscription')
            self.assertEqual(PaymentJournal(folder).read('subscription')['state'],'confirmed')
            with self.assertRaises(StateUnconfirmed):suite.begin_payment('subscription',100,300)

    def test_initial_subscription_still_requires_unsubscribed_account(self):
        """首次订阅必须未订阅，已订阅和未知状态都不能进入购买。"""
        for state in ('subscribed','unknown'):
            with self.subTest(state=state),tempfile.TemporaryDirectory() as folder:
                suite=self.instance(folder)
                suite.profile_membership.return_value=Membership(state,100)
                suite.premium_entry=Mock()
                with self.assertRaises(StateUnconfirmed):suite.subscribe()
                suite.premium_entry.assert_not_called()
                self.assertIsNone(suite.payment_journal().read('subscription'))

    def test_original_order_can_be_verified_after_membership_activates(self):
        """已付款账号可补核验原订单，但不会重新进入沙箱或修改首次购买前置。"""
        with tempfile.TemporaryDirectory() as folder:
            suite=self.instance(folder);self.confirmed(suite)
            suite.sandbox_success=Mock();suite.record=Mock()
            self.assertEqual(suite.verify_payment_case('subscription'),'PASS')
            suite.sandbox_success.assert_not_called()
            suite.profile_membership.assert_called_once_with(refresh=True)
            suite.d.click.assert_not_called()

    def test_missing_original_unsubscribed_evidence_blocks_verification(self):
        """缺少原订单未订阅证据时不能把当前已订阅状态当首次订阅通过。"""
        with tempfile.TemporaryDirectory() as folder:
            suite=self.instance(folder);journal=self.confirmed(suite)
            journal.update('subscription',membership_before={})
            with self.assertRaisesRegex(StateUnconfirmed,'付款前未订阅'):suite.verify_payment('subscription')
            suite.profile_membership.assert_not_called()

    def test_missing_return_or_toast_blocks_without_recharging(self):
        """到账成功但缺回站或Toast时保留阻塞，不通过重付补证据。"""
        for kind,automatic in (('subscription',False),('coin',True)):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as folder:
                suite=self.instance(folder);journal=self.confirmed(suite,kind,automatic)
                suite.sandbox_success=Mock()
                with self.assertRaises(complete.member.Blocked):suite.verify_payment(kind)
                suite.sandbox_success.assert_not_called()
                self.assertEqual(journal.read(kind)['state'],'verified')
                with self.assertRaises(StateUnconfirmed):suite.begin_payment(kind,400,300)

    def test_prod_verification_is_excluded_before_any_phone_operation(self):
        """线上连补核验入口也先按策略跳过，不查询会员或操作设备。"""
        with tempfile.TemporaryDirectory() as folder:
            suite=self.instance(folder)
            suite.policy=ExecutionPolicy.create('prod','https://production.example/')
            suite.record=Mock()
            self.assertEqual(suite.verify_payment_case('subscription'),'SKIPPED_ENV')
            suite.d.assert_no_system_permission_dialog.assert_not_called()
            suite.profile_membership.assert_not_called()


class ConnectionAndLockTests(unittest.TestCase):
    def test_same_device_in_different_batches_is_exclusive_and_releases(self):
        """同一设备不同连接文件也不能并行执行，退出后可再次进入。"""
        with tempfile.TemporaryDirectory() as folder:
            first=Path(folder)/'first.json';second=Path(folder)/'second.json'
            for path in (first,second):path.write_text(json.dumps({'device':{'platform':'android','udid':'test-phone'}}))
            with device_run(first):
                with self.assertRaises(StateUnconfirmed):
                    with device_run(second):self.fail('不应取得同设备锁')
            with device_run(second):pass

    def test_file_lock_is_released_even_after_exception(self):
        """异常退出不遗留持有中的锁，无需删除锁文件。"""
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'device.lock'
            with self.assertRaisesRegex(RuntimeError,'中断'):
                with exclusive_file(path):raise RuntimeError('中断')
            with exclusive_file(path):pass

    def test_driver_disconnect_is_reported_once_on_both_platforms(self):
        """驱动失效快速停止，Android和iOS均不自动重发动作。"""
        for platform in ('Android','iOS'):
            with self.subTest(platform=platform):
                driver=MobileDriver('offline',capabilities={'platformName':platform})
                with patch.object(LegacyDriver,'request',side_effect=RuntimeError('invalid session id')) as request:
                    with self.assertRaises(MobileSessionUnavailable):driver.request('/element/example/click',{})
                    with self.assertRaises(MobileSessionUnavailable):driver.request('/source')
                    request.assert_called_once()

    def test_disconnect_blocks_later_cases_without_more_phone_calls(self):
        """首项发现断线后后续项直接阻塞，避免重复截图和超时。"""
        suite=full.Suite.__new__(full.Suite)
        suite.policy=ExecutionPolicy.create('test');suite.data={'context':{}}
        suite.d=Mock(assert_no_system_permission_dialog=Mock());suite.record=Mock();suite.web_capture=Mock()
        action=Mock(side_effect=MobileSessionUnavailable('会话断开'))
        self.assertEqual(suite.run_case('L-23',[],action),'BLOCKED')
        later=Mock()
        self.assertEqual(suite.run_case('L-24',[],later),'BLOCKED')
        later.assert_not_called();suite.web_capture.assert_not_called()
        suite.d.assert_no_system_permission_dialog.assert_called_once()

    def test_failed_evidence_capture_preserves_original_failure(self):
        """证据补采失败不能覆盖原始断言和用例结论。"""
        suite=full.Suite.__new__(full.Suite)
        suite.policy=ExecutionPolicy.create('test');suite.data={'context':{}}
        suite.d=Mock(assert_no_system_permission_dialog=Mock());suite.record=Mock();suite.event=Mock()
        suite.web_capture=Mock(side_effect=MobileSessionUnavailable('截图连接失败'))
        self.assertEqual(suite.run_case('L-23',[],Mock(side_effect=AssertionError('昵称未保存'))),'FAIL')
        self.assertIn('昵称未保存',suite.record.call_args.args[2])
        suite.web_capture.assert_called_once()


class LoginAndLoadingTests(unittest.TestCase):
    def suite(self, names):
        """构造原生登录观察桩，只返回指定页面状态。"""
        suite=Mock()
        suite.policy=ExecutionPolicy.create('test')
        suite.data={'context':{}}
        suite.d.names.return_value=set(names)
        return suite

    def test_google_redirect_query_is_not_merchant_foreground(self):
        """Google地址参数含被测域名时不能误判已经回站。"""
        self.assertFalse(login.on_merchant({'accounts.google.com/signin?continue=https://merchant.example/'},'merchant.example'))
        self.assertTrue(login.on_merchant({'https://merchant.example/lobby/profile'},'merchant.example'))

    def test_already_logged_in_cannot_restart_registration_window(self):
        """启动已经登录时拒绝重计时，不点Chat追溯历史消息。"""
        suite=self.suite({full.origin(ExecutionPolicy.create('test').base_url),'Connect'})
        with self.assertRaisesRegex(StateUnconfirmed,'启动时已经登录'):login.watch_login(suite)
        suite.d.click.assert_not_called()

    def test_identity_verification_wait_is_bounded_and_checkpointed(self):
        """身份验证超过限定时间立即落盘退出，不保存验证码或账号列表。"""
        suite=self.suite({'两步验证','verification-sensitive-code'})
        with patch.object(login.time,'monotonic',side_effect=[0,0,46]),patch.object(login.time,'sleep'):
            with self.assertRaises(login.LoginWaitingForUser):login.watch_login(suite)
        checkpoint=suite.data['context']['login_checkpoint']
        self.assertEqual(checkpoint['stage'],'waiting_user')
        self.assertNotIn('sensitive',json.dumps(checkpoint))
        suite.save.assert_called_once()

    def test_existing_unknown_classification_cannot_restart_window(self):
        """已有不完整窗口只保留未知，不在后续恢复时重新判定老用户。"""
        suite=self.suite(set());suite.data['context']['login_classification']={'state':'unknown','login_at':100}
        with self.assertRaisesRegex(StateUnconfirmed,'禁止重新计时'):login.watch_login(suite)
        suite.d.names.assert_not_called()

    def test_english_identity_prompt_is_recognized_without_recording_challenge(self):
        """英文Google本人验证应明确阻塞，不落盘通知数字或误判普通网络超时。"""
        for heading in ('Verify it’s you', "Verify it's you"):
            with self.subTest(heading=heading):
                self.assertEqual(login.human_login_step({heading, '98'}), '需要完成Google本人身份验证')

    def test_login_callback_uses_dom_when_android_tree_only_has_address(self):
        """原生树缺少网页按钮时，仍连续捕获登录回调并立即启动Chat观察。"""
        suite=self.suite({full.origin(ExecutionPolicy.create('test').base_url)})
        suite.js.side_effect=[{'prompt':True,'connected':False},{'prompt':False,'connected':True}]
        result={'state':'new','observed_until':123}
        suite.observe_login_success.return_value=result
        with patch.object(login.time,'sleep'):
            self.assertEqual(login.watch_login(suite),result)
        suite.observe_login_success.assert_called_once()
        suite.d.click.assert_not_called()

    def test_google_foreground_is_not_changed_for_merchant_dom_probe(self):
        """Google授权仍在前台时，不为读取商户DOM抢走授权标签的焦点。"""
        suite=self.suite({'accounts.google.com/signin'})
        self.assertEqual(login.merchant_login_state(suite,suite.d.names(),'merchant.example'),(False,False))
        suite.web.assert_not_called()
        suite.js.assert_not_called()

    def test_profile_waits_for_response_and_stable_ui_instead_of_zero_placeholder(self):
        """接口未完成及界面占位阶段均不能返回，需两次稳定真实界面。"""
        suite=full.Suite.__new__(full.Suite)
        base={'timeOrigin':1,'httpStatus':200}
        suite.js=Mock(side_effect=[dict(base,end=0,age=0,text='0 Upgrade to Premium'),
                                  dict(base,end=20,age=100,text='0 Upgrade to Premium'),
                                  dict(base,end=20,age=400,text='400 Premium Member'),
                                  dict(base,end=20,age=550,text='400 Premium Member')])
        with patch.object(full.time,'sleep'):suite.wait_account_details()
        self.assertEqual(suite.js.call_count,4)

    def test_profile_http_error_stops_instead_of_accepting_default_zero(self):
        """用户接口错误时阻塞身份及余额核验，不接受页面默认值。"""
        suite=full.Suite.__new__(full.Suite)
        suite.js=Mock(return_value={'timeOrigin':1,'end':20,'httpStatus':503,'age':500,'text':'0 Upgrade to Premium'})
        with self.assertRaisesRegex(StateUnconfirmed,'HTTP503'):suite.wait_account_details()


if __name__=='__main__':
    unittest.main()
