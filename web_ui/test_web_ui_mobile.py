"""无真机校验设备选择、触屏映射和Android会话隔离，不能代替真机通过结果。"""
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import Mock, patch

from web_ui_mobile_driver import MobileDriver, MobileCompatibilityError, native_tree, map_web_point
from web_ui_mobile_session import android_capabilities, choose_device
from web_ui_extended_suite import Suite
import web_ui_member_runner as runner


class DeviceSelectionTests(unittest.TestCase):
    """确保自动识别便利性不导致选错手机。"""

    def test_single_device_is_selected_by_identifier(self):
        """唯一已授权设备可以自动选择，离线设备不参与候选。"""
        devices=[{'udid':'phone-a','state':'device'}, {'udid':'phone-b','state':'offline'}]
        self.assertEqual(choose_device(devices)['udid'],'phone-a')

    def test_multiple_devices_require_target(self):
        """多台设备首次连接必须明确目标，不隐式使用列表首项。"""
        devices=[{'udid':'phone-a','state':'device'}, {'udid':'phone-b','state':'device'}]
        with self.assertRaisesRegex(RuntimeError,'多台'):
            choose_device(devices)
        self.assertEqual(choose_device(devices,udid='phone-b')['udid'],'phone-b')
        self.assertEqual(choose_device(devices,preferred='phone-a')['udid'],'phone-a')

    def test_disconnected_saved_target_never_falls_back_to_other_phone(self):
        """原目标离线时阻止换机，即使另一台是唯一在线设备。"""
        with self.assertRaisesRegex(RuntimeError,'上次指定'):
            choose_device([{'udid':'other','state':'device'}],preferred='saved')

    def test_unauthorized_target_is_never_selected(self):
        """未完成USB授权的手机不创建会话。"""
        with self.assertRaises(RuntimeError):
            choose_device([{'udid':'phone-a','state':'unauthorized'}],udid='phone-a')

    def test_chrome_uses_existing_profile_and_real_permission_prompts(self):
        """不清浏览器资料、不预先授权，不加入无痕或模拟媒体参数。"""
        caps=android_capabilities({'udid':'target','name':'Pixel','version':'15'})['capabilities']['alwaysMatch']
        self.assertTrue(caps['appium:noReset'])
        self.assertFalse(caps['appium:autoGrantPermissions'])
        self.assertEqual(caps['appium:udid'],'target')
        self.assertEqual(caps['goog:chromeOptions'],{'androidPackage':'com.android.chrome'})


class NativeMappingTests(unittest.TestCase):
    """覆盖高像素密度、键盘缩小视口及错误边界。"""

    def test_android_xml_exposes_usable_bounds_and_names(self):
        """UiAutomator2的text和bounds能转为共用定位属性。"""
        tree=native_tree(ET.fromstring('<hierarchy><node text="允许" bounds="[24,150][324,250]" displayed="true"/></hierarchy>'))
        node=tree[0].attrib
        self.assertEqual((node['name'],node['x'],node['y'],node['width'],node['height']),('允许','24','150','300','100'))

    def test_css_coordinates_scale_to_physical_pixels(self):
        """不同于iPhone逻辑像素的Android物理像素正确换算。"""
        point=map_web_point({'x':10,'y':100,'width':100,'height':40},
                            {'width':360,'height':640}, {'x':0,'y':120,'width':1080,'height':1920})
        self.assertEqual(point,(180,480))

    def test_visual_viewport_offsets_are_accounted_for(self):
        """键盘造成可视区域偏移时按visualViewport映射。"""
        point=map_web_point({'x':10,'y':100,'width':100,'height':40},
                            {'width':360,'height':300,'offsetTop':50},
                            {'x':0,'y':120,'width':1080,'height':900})
        self.assertEqual(point,(180,330))

    def test_mismatched_bounds_or_offscreen_target_prevent_taps(self):
        """边界含工具栏或目标离屏时拒绝猜测坐标。"""
        with self.assertRaises(MobileCompatibilityError):
            map_web_point({'x':0,'y':0,'width':10,'height':10},
                          {'width':360,'height':600},{'x':0,'y':0,'width':1080,'height':2400})
        with self.assertRaises(MobileCompatibilityError):
            map_web_point({'x':0,'y':700,'width':10,'height':10},
                          {'width':360,'height':600},{'x':0,'y':0,'width':1080,'height':1800})


class AndroidWindowTests(unittest.TestCase):
    """Android一个上下文内可能有多个Chrome标签页。"""

    def test_matching_page_is_selected_without_executing_js_on_other_sites(self):
        """避开Google及新标签页，仅向核对过的网址执行页面脚本。"""
        driver=MobileDriver('stub',capabilities={'platformName':'Android'})
        state={'window':None};scripts=[]
        def request(path,data=None):
            """模拟多个真实窗口，并记录哪些窗口收到JS。"""
            if path=='/context':return None
            if path=='/appium/device/current_package':return 'com.android.chrome'
            if path=='/contexts':return ['NATIVE_APP','CHROMIUM']
            if path=='/window/handles':return ['newtab','google','merchant']
            if path=='/window':state['window']=data['handle'];return None
            if path=='/url':return {'newtab':'chrome://newtab/', 'google':'https://accounts.google.com/',
                                    'merchant':'https://test.example/lobby/anchor'}[state['window']]
            if path=='/execute/sync':scripts.append(state['window']);return 'visible'
            raise AssertionError(path)
        driver.request=request
        self.assertEqual(driver.select_page('https://test.example/'),{'context':'CHROMIUM','window':'merchant'})
        self.assertEqual(scripts,['merchant'])

    def test_other_foreground_app_does_not_receive_browser_actions(self):
        """Chrome不在前台时不扫描或切换网页，避免误操作其他应用。"""
        driver=MobileDriver('stub',capabilities={'platformName':'Android'})
        driver.request=Mock(side_effect=[None,'com.example.other'])
        self.assertIsNone(driver.select_page('https://test.example/'))
        self.assertEqual(driver.request.call_count,2)

    def test_web_click_that_closes_window_does_not_restore_closed_context(self):
        """支付完成会关闭标签页，触屏后不再向已关闭上下文发请求。"""
        driver=MobileDriver('stub',capabilities={'platformName':'Android'})
        driver.request=Mock(side_effect=['CHROMIUM',{'width':360,'height':640}])
        driver.web_bounds=Mock(return_value={'x':0,'y':100,'width':1080,'height':1920})
        driver.tap=Mock()
        driver.tap_web_rect({'x':0,'y':100,'width':100,'height':50},restore=False)
        self.assertEqual(driver.request.call_count,2)
        driver.tap.assert_called_once_with(150,475)

    def test_permission_button_does_not_match_a_webpage_allow_label(self):
        """业务页的普通Allow按钮不能冒充系统授权按钮。"""
        driver=MobileDriver('stub',capabilities={'platformName':'Android'})
        driver.request=Mock()
        driver.tree=Mock(return_value=native_tree(ET.fromstring('''<hierarchy>
            <node text="Allow" package="com.android.chrome" bounds="[0,100][200,200]"/>
            </hierarchy>''')))
        self.assertIsNone(driver.permission_button(True))
        driver.tree=Mock(return_value=native_tree(ET.fromstring('''<hierarchy>
            <node text="Allow" resource-id="com.android.permissioncontroller:id/permission_allow_foreground_only_button"
                  bounds="[0,100][200,200]"/></hierarchy>''')))
        self.assertIsNotNone(driver.permission_button(True))

    def test_android_chinese_chrome_permission_labels(self):
        """三星实际Chrome权限文案可识别，仍要求本域名和音视频请求上下文。"""
        driver=MobileDriver('stub',capabilities={'platformName':'Android'})
        driver.target_url='https://test.example/'
        driver.request=Mock()
        driver.tree=Mock(return_value=native_tree(ET.fromstring('''<hierarchy>
            <node text="test.example 想使用您的摄像头和麦克风" package="com.android.chrome" bounds="[0,0][500,100]"/>
            <node text="在访问期间允许" package="com.android.chrome" bounds="[0,100][500,200]"/>
            <node text="一律不允许" package="com.android.chrome" bounds="[0,200][500,300]"/>
            </hierarchy>''')))
        self.assertEqual(driver.permission_button(True)['text'],'在访问期间允许')
        self.assertEqual(driver.permission_button(False)['text'],'一律不允许')

    def test_android_system_permission_overlay_blocks_web_success(self):
        """系统弹窗遮挡网页时抛阻塞且恢复原上下文，不能因后台DOM变化判通过。"""
        driver=MobileDriver('stub',capabilities={'platformName':'Android'})
        driver.request=Mock(return_value='CHROMIUM')
        driver.tree=Mock(return_value=native_tree(ET.fromstring('''<hierarchy>
            <node text="要允许Chrome拍摄照片吗" package="com.android.permissioncontroller" bounds="[0,100][500,300]"/>
            </hierarchy>''')))
        with self.assertRaisesRegex(MobileCompatibilityError,'遮挡'):
            driver.assert_no_system_permission_dialog()
        driver.request.assert_called_with('/context',{'name':'CHROMIUM'})

    def test_android_chrome_continue_media_chip_must_be_handled(self):
        """Chrome摄像头继续提示属于待授权层，不能因网页DOM已进入通话就判通过。"""
        driver=MobileDriver('stub',capabilities={'platformName':'Android'})
        driver.target_url='https://test.example/';driver.request=Mock(return_value='CHROMIUM')
        driver.tree=Mock(return_value=native_tree(ET.fromstring('''<hierarchy>
            <node text="test.example" package="com.android.chrome" bounds="[0,0][500,100]"/>
            <node text="允许使用摄像头？Chrome 需要为此网站授予摄像头权限" package="com.android.chrome" bounds="[0,100][500,200]"/>
            <node text="继续" resource-id="com.android.chrome:id/permission_action" package="com.android.chrome" bounds="[0,200][500,300]"/>
            </hierarchy>''')))
        self.assertEqual(driver.permission_button(True)['text'],'继续')
        with self.assertRaises(MobileCompatibilityError):driver.assert_no_system_permission_dialog()

    def test_web_continue_button_cannot_be_used_as_native_media_grant(self):
        """网页自己的Continue没有Chrome原生控件ID，不能被授权处理器点击。"""
        driver=MobileDriver('stub',capabilities={'platformName':'Android'})
        driver.target_url='https://test.example/';driver.request=Mock()
        driver.tree=Mock(return_value=native_tree(ET.fromstring('''<hierarchy>
            <node text="test.example camera" package="com.android.chrome" bounds="[0,0][500,100]"/>
            <node text="Continue" package="com.android.chrome" bounds="[0,100][500,200]"/>
            </hierarchy>''')))
        self.assertIsNone(driver.permission_button(True))

    def test_ios_does_not_run_android_permission_guard(self):
        """iOS直接沿用既有权限流程，不读取Android原生树或切换上下文。"""
        driver=MobileDriver('stub',capabilities={'platformName':'iOS'})
        driver.request=Mock()
        driver.assert_no_system_permission_dialog()
        driver.request.assert_not_called()


class BatchIsolationTests(unittest.TestCase):
    """跨平台、换手机和切换执行模式不能混用已产生结果的批次。"""

    def test_other_phone_cannot_append_results_to_existing_batch(self):
        """平台相同但UDID不同仍视为换机，禁止合并原设备结果。"""
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)
            (path/'report.json').write_text(json.dumps({'device':{'platform':'android','udid':'first'},
                'environment':'Android Chrome','results':{'G-04':{}},'context':{},'events':[]}))
            driver=Mock()
            driver.device_info.return_value={'platform':'android','udid':'second'}
            with patch.object(runner,'OUT',path),patch.object(runner,'MobileDriver',return_value=driver):
                with self.assertRaisesRegex(ValueError,'独立'):
                    runner.Runner(session_id='stub')

    def test_legacy_iphone_batch_cannot_be_reused_by_android(self):
        """没有device字段的历史iPhone记录也不能被Android覆盖。"""
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)
            (path/'report.json').write_text(json.dumps({'environment':'iPhone 13 / Chrome',
                'results':{'G-04':{}},'context':{},'events':[]}))
            driver=Mock()
            driver.device_info.return_value={'platform':'android','udid':'second'}
            with patch.object(runner,'OUT',path),patch.object(runner,'MobileDriver',return_value=driver):
                with self.assertRaisesRegex(ValueError,'iPhone'):
                    runner.Runner(session_id='stub')

    def test_mode_change_is_only_allowed_before_any_case_result(self):
        """空批次允许选择模式，有结果后禁止改变清数据规则。"""
        suite=Suite.__new__(Suite)
        suite.data={'device':{'name':'Pixel','platform':'android','version':'15'},
                    'context':{'incremental_without_reset':True},'results':{}}
        suite.save=Mock()
        suite.set_execution_mode(False)
        self.assertIn('全量复测',suite.data['environment'])
        suite.data['results']['G-04']={}
        with self.assertRaisesRegex(ValueError,'不能'):
            suite.set_execution_mode(True)
        self.assertFalse(suite.data['context']['incremental_without_reset'])


if __name__=='__main__':
    unittest.main()
