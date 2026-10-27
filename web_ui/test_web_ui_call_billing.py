"""核对通话计费规则、prod准入和报告Logo处理，不连接设备。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from web_ui_call_billing import CallBillingMixin, check_call_billing
from web_ui_environment import EnvironmentExcluded, ExecutionPolicy
from web_ui_report_branding import apply_branding
from web_ui_platform_cases import platform_cases
from web_ui_platform_cases import platform_pause_reason
from web_ui_account_state import StateExcluded
from web_ui_member_suite import Blocked
from web_ui_full_rerun import Suite as FullSuite
from web_ui_extended_suite import Suite as ExtendedSuite
from web_ui_case_index import CASES


class CallBillingTests(unittest.TestCase):
    def test_android_pauses_video_cases_before_any_device_action(self):
        """Android暂缓三项在会员检查前即跳过，iOS与PC范围不受影响。"""
        for cid in ('S-04','S-05','S-06'):
            with self.subTest(case_id=cid):
                self.assertTrue(platform_pause_reason(cid,'android'))
                self.assertIsNone(platform_pause_reason(cid,'ios'))
                self.assertIsNone(platform_pause_reason(cid,'web'))
                instance=FullSuite.__new__(FullSuite)
                instance.policy=ExecutionPolicy.create('test');instance.d=Mock(platform='android',assert_no_system_permission_dialog=Mock())
                instance.record=Mock();instance.profile_membership=Mock();action=Mock()
                self.assertEqual(instance.run_case(cid,[],action),'SKIPPED_STATE')
                action.assert_not_called();instance.profile_membership.assert_not_called()
                instance.d.assert_no_system_permission_dialog.assert_not_called()

    def test_unavailable_call_target_skips_only_android(self):
        """没有可接通主播时Android跳过，iOS继续保持原来的阻塞分类。"""
        for platform,error in (('android',StateExcluded),('ios',Blocked)):
            with self.subTest(platform=platform):
                instance=CallBillingMixin();instance.d=Mock(platform=platform)
                with self.assertRaises(error):instance.unavailable_billing_target('有限尝试后没有主播接听')

    def test_android_skip_survives_call_cleanup(self):
        """未接通的跳过结论经退出和余额复核后仍应跳过，不能被统一改成阻塞。"""
        instance=CallBillingMixin();instance.policy=ExecutionPolicy.create('test')
        instance.d=Mock(platform='android');instance.data={'results':{'S-04':{'status':'PASS'}},'context':{}}
        instance.require_subscribed=Mock();instance.balance=Mock(return_value=400)
        instance.web_capture=Mock(return_value=[]);instance.save=Mock();instance.js=Mock(return_value=False)
        instance.profile_page=Mock();instance.reload_profile=Mock()
        instance.billing_dial=Mock(side_effect=StateExcluded('本轮没有可接通主播'))
        with self.assertRaisesRegex(StateExcluded,'已退出'):instance.billing_call(False)
        self.assertEqual(instance.billing_dial.call_count,1)
        self.assertEqual(instance.data['context']['call_billing_attempts'][0]['after'],400)

    def test_pc_scope_changes_do_not_remove_phone_cases(self):
        """PC六项专属排除保持隔离，S-10全平台停用，手机权限预期保留。"""
        self.assertEqual(len(platform_cases(CASES,'web')),57)
        for platform in ('android','ios'):
            self.assertEqual(len(platform_cases(CASES,platform)),63)
            ids={c[0] for c in platform_cases(CASES,platform)}
            self.assertTrue({'L-14','N-01','L-20','L-11','G-11','L-05'} <= ids)
            self.assertNotIn('S-10',ids)
            n03=next(c for c in platform_cases(CASES,platform) if c[0]=='N-03')
            self.assertIn('浏览器通知',n03[4][0])
            l04=next(c for c in platform_cases(CASES,platform) if c[0]=='L-04')
            self.assertIn('取消','；'.join(l04[3]))

    def test_disabled_free_case_stops_all_mobile_entries_before_device_actions(self):
        """两种手机的旧编号和免费通话入口均须在设备、会员及业务动作前停用。"""
        for platform in ('android','ios'):
            with self.subTest(platform=platform):
                instance=ExtendedSuite.__new__(ExtendedSuite)
                instance.d=Mock(platform=platform);instance.record=Mock();instance.profile_membership=Mock()
                action=Mock()
                for entry in (lambda:instance.run_case('S-10',[],action),
                              lambda:instance.run_new('S-10',action),
                              lambda:CallBillingMixin.billing_call(instance,True)):
                    with self.assertRaisesRegex(StateExcluded,'免费一分钟停用'):entry()
                action.assert_not_called();instance.record.assert_not_called()
                instance.profile_membership.assert_not_called()
                self.assertEqual(instance.d.mock_calls,[])

    def test_paid_and_free_calls_have_distinct_balance_assertions(self):
        """分别验证普通扣80和免费不扣费，错误余额变化必须失败。"""
        for before, after, free, status in ((400,320,False,'PASS'),(400,400,False,'FAIL'),(400,400,True,'PASS'),(400,320,True,'FAIL')):
            with self.subTest(after=after, free=free):
                self.assertEqual(check_call_billing(before,after,60,free)[0],status)

    def test_incomplete_or_ambiguous_duration_is_blocked(self):
        """短于一分钟或达到两分钟时不得套用一分钟扣费判断。"""
        for seconds in (0,59,120):
            self.assertEqual(check_call_billing(400,320,seconds,False)[0],'BLOCKED')

    def test_partial_second_minute_is_rounded_up_on_both_mobile_platforms(self):
        """手机共用断言须与已确认的70秒扣160规则一致，免费通话仍不扣费。"""
        for seconds in (61, 70, 119):
            with self.subTest(seconds=seconds):
                self.assertEqual(check_call_billing(400,240,seconds,False)[0],'PASS')
                self.assertEqual(check_call_billing(400,320,seconds,False)[0],'FAIL')
                self.assertEqual(check_call_billing(400,400,seconds,True)[0],'PASS')

    def test_prod_does_not_touch_device_or_spend_coins(self):
        """线上在触碰页面前拒绝新增通话计费和余额准备。"""
        instance = CallBillingMixin()
        instance.policy = ExecutionPolicy.create('prod','https://production.example')
        instance.require_subscribed = Mock()
        for action in (lambda:instance.billing_call(False),instance.spend_to_zero,instance.empty_video):
            with self.assertRaises(EnvironmentExcluded):
                action()
        with self.assertRaisesRegex(StateExcluded,'免费一分钟停用'):
            instance.billing_call(True)
        instance.require_subscribed.assert_not_called()

    def test_logo_is_embedded_and_repeated_application_is_idempotent(self):
        """普通与单文件页面内嵌相同图片，二次生成不会叠加侧栏样式。"""
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder)
            (report/'index.html').write_text('<html><head><link rel="icon" href="favicon.ico"></head></html>')
            apply_branding(report)
            first = (report/'index.html').read_text()
            self.assertIn('data:image/png;base64,', first)
            self.assertNotIn('href="favicon.ico"', first)
            apply_branding(report)
            self.assertEqual((report/'index.html').read_text(),first)


if __name__ == '__main__':
    unittest.main()
