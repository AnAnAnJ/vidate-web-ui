"""不连接手机，验证线上操作拦截及协议成功结果的复用。"""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from web_ui_account_state import (
    Membership, StateExcluded, StateUnconfirmed, membership_from_profile,
    require_case_state, classify_login_window, test_membership_due as membership_due,
)
from web_ui_extended_suite import Suite as ExtendedSuite
from web_ui_complete_suite import Suite as CompleteSuite

import web_ui_member_runner as runner
from web_ui_environment import ExecutionPolicy, EnvironmentExcluded, PROD_EXCLUDED, TEST_URL
from web_ui_full_rerun import Suite
import web_ui_member_suite as member


class EnvironmentPolicyTests(unittest.TestCase):
    def test_prod_exclusions_never_invoke_actions(self):
        """校验prod禁止操作在调用真实动作前即被拦截。"""
        policy = ExecutionPolicy.create('prod', 'https://production.example/')
        for operation in PROD_EXCLUDED:
            with self.subTest(operation=operation):
                action = Mock()
                with self.assertRaises(EnvironmentExcluded):
                    policy.execute(operation, action)
                action.assert_not_called()

    def test_test_environment_runs_provided_operations(self):
        """校验test允许经策略执行已授权的环境操作。"""
        policy = ExecutionPolicy.create('test')
        for operation in PROD_EXCLUDED:
            with self.subTest(operation=operation):
                action = Mock(return_value='executed')
                self.assertEqual(policy.execute(operation, action), 'executed')
                action.assert_called_once_with()

    def test_prod_still_allows_unsubscribed_popup_check(self):
        """校验prod仍允许未订阅Video的订阅拦截检查。"""
        policy = ExecutionPolicy.create('prod', 'https://production.example/')
        action = Mock(return_value='popup shown')
        self.assertEqual(policy.execute('unsubscribed_video', action), 'popup shown')
        action.assert_called_once_with()

    def test_environment_mismatch_and_missing_prod_url_stop_execution(self):
        """校验缺失线上地址或环境域名冲突会停止执行。"""
        for environment, url in [('prod', None), ('prod', TEST_URL)]:
            with self.subTest(environment=environment, url=url), self.assertRaises(ValueError):
                ExecutionPolicy.create(environment, url)
        policy = ExecutionPolicy.create('prod', 'https://production.example/')
        with self.assertRaises(ValueError):
            policy.verify_url(TEST_URL)

    def test_configured_test_site_can_be_overridden_for_other_sites(self):
        """校验可更换test网站且实际域名仍须与目标一致。"""
        policy = ExecutionPolicy.create('test', 'https://second-test.example/path')
        self.assertEqual(policy.base_url, 'https://second-test.example/')
        policy.verify_url('https://second-test.example/lobby/profile')
        with self.assertRaises(ValueError):
            policy.verify_url(TEST_URL)

    def test_prod_video_requires_unsubscribed_profile_before_any_video_action(self):
        """校验prod点击未订阅Video前先核对Profile身份。"""
        instance = Suite.__new__(Suite)
        instance.policy = ExecutionPolicy.create('prod', 'https://production.example/')
        instance.profile_membership = Mock(return_value=Membership('subscribed', 0, '20-10-2026'))
        with patch.object(member.Suite, 'video') as video:
            with self.assertRaises(StateExcluded):
                instance.video()
            video.assert_not_called()
            instance.profile_membership.return_value = Membership('unsubscribed', 0)
            instance.video()
            video.assert_called_once_with()

    def test_excluded_case_is_recorded_as_environment_skip_without_calling_action(self):
        """校验环境排除项记录为跳过且不调用业务动作。"""
        instance = Suite.__new__(Suite)
        instance.policy = ExecutionPolicy.create('prod', 'https://production.example/')
        instance.record = Mock()
        action = Mock()
        status = instance.run_case('FUTURE-PAYMENT', [], action, operation='subscription_payment')
        self.assertEqual(status, 'SKIPPED_ENV')
        self.assertEqual(instance.record.call_args.args[1], 'SKIPPED_ENV')
        action.assert_not_called()


class PermissionNoticePlatformTests(unittest.TestCase):
    """浏览器下落通知仅在Android跳过，iOS仍需真实校验。"""

    def test_android_notice_is_recorded_as_not_applicable(self):
        """Android按用户指定规则记录跳过，不记为通过或失败。"""
        instance=CompleteSuite.__new__(CompleteSuite)
        instance.d=Mock(platform='android')
        instance.record=Mock()
        instance.skip_android_permission_notice('G-12')
        self.assertEqual(instance.record.call_args.args[:2],('G-12','NOT_APPLICABLE'))

    def test_ios_cannot_use_android_notice_skip(self):
        """iOS误调用Android跳过入口时阻止记录。"""
        instance=CompleteSuite.__new__(CompleteSuite)
        instance.d=Mock(platform='ios')
        instance.record=Mock()
        with self.assertRaises(member.Blocked):
            instance.skip_android_permission_notice('G-12')
        instance.record.assert_not_called()

    def test_android_editor_hides_keyboard_and_uses_current_web_save(self):
        """Android收起键盘后使用实时网页Save，防止原生坐标误点。"""
        instance=ExtendedSuite.__new__(ExtendedSuite)
        instance.d=Mock(platform='android')
        instance.d.request.return_value=True
        instance.web=Mock();instance.click_label=Mock();instance.click_text=Mock();instance.wait_text=Mock()
        instance.save_profile_editor()
        instance.d.request.assert_any_call('/appium/device/hide_keyboard',{})
        instance.d.click.assert_not_called()
        instance.click_text.assert_called_once_with('Save')
        instance.click_label.assert_not_called()

    def test_ios_editor_keeps_existing_web_save(self):
        """iOS保留原Save点击，不调用Android键盘及原生按钮接口。"""
        instance=ExtendedSuite.__new__(ExtendedSuite)
        instance.d=Mock(platform='ios')
        instance.click_label=Mock();instance.wait_text=Mock()
        instance.save_profile_editor()
        instance.click_label.assert_called_once_with('Save')
        instance.d.request.assert_not_called()
        instance.d.click.assert_not_called()


class AgreementReuseTests(unittest.TestCase):
    def test_success_is_reused_without_opening_any_phone_page(self):
        """校验同域名协议成果复用时不重新操作手机页面。"""
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            evidence = directory / 'agreement.txt'
            evidence.write_text('两份协议已核对，正文非空')
            verified = {'status': 'PASS', 'stop': 12345, 'actual': '两份协议正文非空', 'evidence': [str(evidence)]}
            first = runner.Runner.__new__(runner.Runner)
            first.data = {'url': TEST_URL, 'results': {'L-01': copy.deepcopy(verified)}}
            first.d = Mock()
            first.d.dismiss_login.side_effect = AssertionError('不应再次打开登录或协议')
            first.event = Mock()
            with patch.object(runner, 'ROOT', directory):
                self.assertEqual(first.agreements()['status'], 'PASS')
                first.d.dismiss_login.assert_not_called()
                cache = json.loads((directory / 'web_ui_agreement_cache.json').read_text())
                self.assertEqual(len(cache), 1)
                second = runner.Runner.__new__(runner.Runner)
                second.data = {'url': TEST_URL, 'results': {}}
                second.event = Mock()
                second.d = Mock()
                result = second.agreements()
                self.assertEqual(result['verification_mode'], 'reused')
                self.assertIn('本次未重新打开协议', result['actual'])
                self.assertEqual(result['source_stop'], 12345)
                self.assertNotEqual(result['stop'],12345)
                second.d.dismiss_login.assert_not_called()

    def test_different_domain_does_not_reuse_test_result(self):
        """校验不同域名不能复用另一站点的协议结论。"""
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            (directory / 'web_ui_agreement_cache.json').write_text(json.dumps({'https://test.randomvideochat-talk.pages.dev': {'status': 'PASS'}}))
            instance = runner.Runner.__new__(runner.Runner)
            instance.data = {'url': 'https://production.example/', 'results': {}}
            instance.d = Mock()
            instance.d.dismiss_login.side_effect = RuntimeError('进入首次实际校验')
            with patch.object(runner, 'ROOT', directory), self.assertRaisesRegex(RuntimeError, '进入首次实际校验'):
                instance.agreements()
            instance.d.dismiss_login.assert_called_once_with()


class AccountStateTests(unittest.TestCase):
    def test_profile_needs_member_style_and_nonempty_expiry(self):
        """校验会员识别同时需要正确卡片样式及有效期。"""
        for body, expected in [
            ('Premium Member Active until 20-10-2026', 'subscribed'),
            ('Upgrade to Premium', 'unsubscribed'),
            ('Premium Member Active until', 'unknown'),
            ('Premium Member Active until 20-10-2026 Upgrade to Premium', 'unknown'),
            ('Loading...', 'unknown'),
        ]:
            with self.subTest(body=body):
                self.assertEqual(membership_from_profile(body, now=100).state, expected)

    def test_expired_membership_is_reclassified_from_fresh_profile(self):
        """校验会员到期后按刷新结果重新识别，禁止沿用历史状态。"""
        before = membership_from_profile('Premium Member Active until 20-10-2026', now=100)
        after = membership_from_profile('Upgrade to Premium', now=401)
        require_case_state('S-07', before, 'test')
        with self.assertRaises(StateUnconfirmed):
            require_case_state('S-07', after, 'test')

    def test_five_minute_boundary_only_applies_to_test(self):
        """校验5分钟会员条件仅适用test且边界时刻正确。"""
        self.assertFalse(membership_due('test', 100, 399.99))
        self.assertTrue(membership_due('test', 100, 400))
        self.assertFalse(membership_due('prod', 100, 999))
        self.assertFalse(membership_due('test', None, 999))

    def test_prod_member_skips_associated_unsubscribed_actions(self):
        """校验prod已订阅身份跳过强关联未订阅用例。"""
        instance = Suite.__new__(Suite)
        instance.d = Mock(assert_no_system_permission_dialog=Mock())
        instance.policy = ExecutionPolicy.create('prod', 'https://production.example/')
        instance.profile_membership = Mock(return_value=Membership('subscribed', 100, '20-10-2026'))
        instance.record = Mock()
        action = Mock()
        status = instance.run_case('L-09', [], action)
        self.assertEqual(status, 'SKIPPED_STATE')
        action.assert_not_called()

    def test_generic_profile_edit_does_not_require_unsubscribed_account(self):
        """校验通用资料编辑不被未订阅身份条件错误限制。"""
        instance = Suite.__new__(Suite)
        instance.d = Mock(assert_no_system_permission_dialog=Mock())
        instance.policy = ExecutionPolicy.create('prod', 'https://production.example/')
        instance.record = Mock()
        instance.profile_membership = Mock()
        action = Mock(return_value=('已保存昵称', []))
        self.assertEqual(instance.run_case('L-23', [], action), 'PASS')
        action.assert_called_once()
        instance.profile_membership.assert_not_called()

    def test_prod_payment_exclusion_precedes_account_lookup(self):
        """校验prod付款拦截优先于会员读取和任何付款动作。"""
        instance = Suite.__new__(Suite)
        instance.policy = ExecutionPolicy.create('prod', 'https://production.example/')
        instance.record = Mock()
        instance.profile_membership = Mock()
        action = Mock()
        self.assertEqual(instance.run_case('S-01', [], action, operation='subscription_payment'), 'SKIPPED_ENV')
        action.assert_not_called()
        instance.profile_membership.assert_not_called()

    def test_subscribed_phase_cannot_start_before_unmarked_cases_are_handled(self):
        """校验未标记项尚未处理时不能开始订阅阶段。"""
        instance = ExtendedSuite.__new__(ExtendedSuite)
        instance.data = {'results': {}}
        instance.run_case = Mock()
        with self.assertRaises(RuntimeError):
            instance.run_new('S-01', Mock())
        instance.run_case.assert_not_called()


class MembershipRecoveryTests(unittest.TestCase):
    def instance(self, environment='test'):
        """构造不连接手机的套件桩，用于验证会员恢复规则及禁止付款的分支。"""
        instance=CompleteSuite.__new__(CompleteSuite)
        instance.policy=ExecutionPolicy.create(environment, 'https://production.example/' if environment=='prod' else None)
        instance.data={'context':{'payment_confirmed_at':100}}
        instance.js=Mock(return_value='https://example.test/lobby/anchor')
        instance.event=Mock();instance.subscribe=Mock();instance.d=Mock()
        instance.profile_membership=Mock(return_value=Membership('subscribed',401,'20-10-2026'))
        return instance

    def test_test_expiry_requests_admin_recovery_without_repeating_payment(self):
        """校验test会员失效转后台待办，不能再次调用沙箱支付。"""
        instance=self.instance()
        instance.request_membership_extension=Mock(side_effect=StateUnconfirmed('后台待办'))
        with self.assertRaisesRegex(StateUnconfirmed,'后台待办'):
            instance.prepare_case_membership('S-04',Membership('unsubscribed',401))
        instance.request_membership_extension.assert_called_once_with('S-04')
        instance.subscribe.assert_not_called()

    def test_unknown_unexpired_and_prod_states_never_trigger_payment(self):
        """校验身份未知、未到期或prod均不自动续订。"""
        for environment,state,now in [('prod','unsubscribed',401),('test','unknown',401),('test','subscribed',399)]:
            instance=self.instance(environment)
            with self.subTest(environment=environment,state=state,now=now),patch('web_ui_complete_suite.time.time',return_value=now):
                instance.prepare_case_membership('S-04',Membership(state,now))
                instance.subscribe.assert_not_called()

    def test_admin_recovery_cannot_replace_first_subscription(self):
        """首次真实支付尚未校验时不能后台开会员冒充首次成功。"""
        instance=self.instance()
        with self.assertRaisesRegex(StateUnconfirmed,'首次真实支付'):
            instance.request_membership_extension('S-04')
        instance.subscribe.assert_not_called()

    def test_full_suite_must_finish_all_unmarked_cases_before_payment(self):
        """校验完整轮次也必须先处理游客及其他未标记项才能付款。"""
        from web_ui_extended_cases import NEW_CASES,SUBSCRIBED_IDS
        instance=ExtendedSuite.__new__(ExtendedSuite)
        instance.data={'context':{'incremental_without_reset':False},'results':{c[0]:{} for c in NEW_CASES if c[0] not in SUBSCRIBED_IDS}}
        instance.run_case=Mock()
        with self.assertRaisesRegex(RuntimeError,'G-03'):
            instance.run_new('S-01',Mock())
        instance.run_case.assert_not_called()


class LoginClassificationTests(unittest.TestCase):
    def test_android_short_calls_run_one_pending_case_per_connection(self):
        """Android六秒试用通话逐项执行，已通过项不再匹配；iOS原入口不变。"""
        suite=CompleteSuite.__new__(CompleteSuite)
        suite.d=Mock(platform='android');suite.data={'results':{'N-08':{'status':'PASS'}}}
        suite.conversation=Mock()
        CompleteSuite.conversation(suite,True)
        suite.conversation.assert_called_once_with(True,case_ids={'N-09'})

    def test_five_selected_tags_still_allows_one_add_and_cancel_target(self):
        """5/6前置只选择剩余一个名额，不能因脚本固定选择两个而误报阻塞。"""
        suite=ExtendedSuite.__new__(ExtendedSuite)
        suite.profile_page=Mock();suite.js=Mock(return_value=['Latex','Biting'])
        selected={'count':'5','html':'border-[#FF66D6]'}
        unselected={'count':'5','html':'unselected'}
        suite.tag_state=Mock(side_effect=[selected,unselected,unselected,{'count':'6','html':'border-[#FF66D6]'}])
        suite.click_text=Mock();suite.web_capture=Mock(return_value=[]);suite.save=Mock()
        suite.data={'context':{}}
        with tempfile.TemporaryDirectory() as folder,patch.object(runner,'OUT',Path(folder)):
            suite.tags_select()
        suite.click_text.assert_called_once_with('Biting')
        self.assertEqual(suite.data['context']['selected_tags_this_run'],['Biting'])

    def test_official_nonzero_badge_within_window_confirms_new_user(self):
        """Official消息角标非零是已确认的新用户依据，不能仅因未出现teamoffice文本漏判。"""
        self.assertEqual(classify_login_window(100, [(105, 'Chats\n2\nOfficial\nSupport')], 105, False), 'new')
        self.assertEqual(classify_login_window(100, [(131, 'Chats\n2\nOfficial\nSupport')], 131, False), 'unknown')

    def test_official_title_or_navigation_badge_alone_is_not_a_message(self):
        """Official入口固定标题和导航角标不能冒充Official非零消息。"""
        for body in ('Official\nSupport\nConnect\n2\nProfile', '0\nOfficial\nSupport'):
            self.assertEqual(classify_login_window(100, [(105, body)], 105, False), 'unknown')

    def test_teamoffice_within_thirty_seconds_confirms_new_user(self):
        """校验只有登录后30秒内teamoffice消息能判定新用户。"""
        self.assertEqual(classify_login_window(100, [(129.9, 'Team Office\nHi')], 130, True), 'new')
        self.assertEqual(classify_login_window(100, [(130, 'teamoffice')], 130, True), 'new')

    def test_late_message_does_not_change_existing_user_classification(self):
        """校验30秒后消息不能追溯改变完整窗口内的老用户判定。"""
        self.assertEqual(classify_login_window(100, [(130.01, 'teamoffice')], 131, True), 'existing')

    def test_no_message_requires_completed_chat_window(self):
        """校验无消息必须有完整有效窗口才能判为老用户。"""
        self.assertEqual(classify_login_window(100, [], 129, True), 'unknown')
        self.assertEqual(classify_login_window(100, [], 130, False), 'unknown')
        self.assertEqual(classify_login_window(100, [], 130, True), 'existing')

    def test_old_login_cannot_be_reclassified_from_current_page(self):
        """校验错过登录窗口时不能用当前页面追溯身份。"""
        instance = runner.Runner.__new__(runner.Runner)
        instance.click_text = Mock()
        with patch.object(runner.time, 'time', return_value=131), self.assertRaises(RuntimeError):
            instance.observe_login_success(100)
        instance.click_text.assert_not_called()

    def test_failed_chat_loading_does_not_classify_existing_user(self):
        """校验Chat加载失败导致窗口无效，不能据无消息判为老用户。"""
        instance = runner.Runner.__new__(runner.Runner)
        instance.data = {'context': {}}
        instance.d = Mock(platform='android')
        instance.save = Mock()
        instance.click_text = Mock()
        instance.wait_text = Mock()
        instance.web_capture = Mock(return_value=[])
        instance.text = Mock(return_value='Chats\nLoading failed. Please try again.')
        instance.js = Mock(side_effect=['/lobby/sessions', 'visible', '/lobby/sessions', '/lobby/sessions'])
        instance.event = Mock()
        with patch.object(runner.time, 'time', side_effect=[100, 130]):
            result = instance.observe_login_success(100)
        self.assertEqual(result['state'], 'unknown')
        self.assertFalse(result['chat_window_valid'])


if __name__ == '__main__':
    unittest.main()
