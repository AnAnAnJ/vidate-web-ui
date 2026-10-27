#!/usr/bin/env python3
"""识别连接的手机，创建独立 Android Chrome 会话；不安装驱动或更改网络。"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
WORK = ROOT.parent/'work'
PREFERENCES = WORK/'web_ui_devices.json'
DEFAULT_SERVER = 'http://127.0.0.1:4723/wd/hub'


def command(args):
    """以参数数组运行只读设备识别命令，限制超时并报告非零退出原因。"""
    result = subprocess.run(args, capture_output=True, text=True, timeout=20, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or '设备识别命令执行失败')
    return result.stdout.strip()


def adb_path():
    """查找现有adb可执行文件；未安装时报告缺失，不自动安装或修改环境。"""
    path = shutil.which('adb')
    fallback = Path.home()/'platform-tools/adb'
    if not path and fallback.is_file():
        path = str(fallback)
    if not path:
        raise RuntimeError('未发现adb；暂不安装软件，连接设备时再处理环境依赖')
    return path


def android_devices():
    """通过adb识别连接状态、唯一标识、型号、系统及Chrome版本，不修改设备配置。"""
    adb = adb_path()
    devices = []
    for line in command([adb, 'devices', '-l']).splitlines()[1:]:
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[:2]
        item = {'platform': 'android', 'udid': serial, 'state': state}
        if state == 'device':
            get = lambda key: command([adb, '-s', serial, 'shell', 'getprop', key])
            item.update(name=get('ro.product.model'), version=get('ro.build.version.release'))
            package = command([adb, '-s', serial, 'shell', 'dumpsys', 'package', 'com.android.chrome'])
            versions = [line.split('versionName=', 1)[1].strip() for line in package.splitlines() if 'versionName=' in line]
            item['chrome_version'] = versions[0] if versions else None
        devices.append(item)
    return devices


def ios_devices():
    # usbmux 只枚举当前USB连接设备，不读取钥匙串或开发者账户。
    """通过已有usbmux工具枚举USB连接iPhone并读取基础资料，不处理签名或账户。"""
    program = shutil.which('idevice_id')
    info = shutil.which('ideviceinfo')
    if not program:
        return []
    devices = []
    for serial in command([program, '-l']).splitlines():
        item = {'platform': 'ios', 'udid': serial, 'state': 'device'}
        if info:
            for target, key in [('name','DeviceName'), ('version','ProductVersion'), ('model','ProductType')]:
                try:
                    item[target] = command([info, '-u', serial, '-k', key])
                except RuntimeError:
                    item[target] = '未读取（设备可能未解锁或未信任）'
        devices.append(item)
    return devices


def choose_device(devices, *, udid=None, preferred=None):
    """按显式或已记住的标识选择手机；多台、未授权或目标离线时停止，禁止回退误测。"""
    connected = [d for d in devices if d['state'] == 'device']
    if udid:
        match = [d for d in connected if d['udid'] == udid]
        if len(match) != 1:
            raise RuntimeError('指定设备未连接或未授权；不切换到其他手机')
        return match[0]
    if preferred:
        match = [d for d in connected if d['udid'] == preferred]
        if len(match) == 1:
            return match[0]
        # 保存的目标设备未连接，不把另一台手机当成原设备。
        raise RuntimeError('上次指定的设备未连接；换机请显式指定 --udid')
    if len(connected) == 1:
        return connected[0]
    if not connected:
        raise RuntimeError('未检测到已授权Android设备；请连接并完成USB调试授权')
    raise RuntimeError('检测到多台Android设备；请用 --udid 指定本次目标，不默认选择第一台')


def android_capabilities(device, chromedriver=None):
    """构造普通Android Chrome能力，保留现有资料及真实权限请求，可指定匹配Chromedriver。"""
    caps = {'platformName': 'Android', 'browserName': 'Chrome',
            'appium:automationName': 'UiAutomator2', 'appium:udid': device['udid'],
            'appium:deviceName': device.get('name') or 'Android',
            'appium:noReset': True, 'appium:dontStopAppOnReset': True,
            'appium:shouldTerminateApp': False, 'appium:autoGrantPermissions': False,
            'appium:newCommandTimeout': 3600,
            'goog:chromeOptions': {'androidPackage': 'com.android.chrome'}}
    if device.get('version'):
        caps['appium:platformVersion'] = device['version']
    if chromedriver:
        path = Path(chromedriver).expanduser().resolve()
        if not path.is_file():
            raise ValueError('指定的Chromedriver不存在')
        caps['appium:chromedriverExecutable'] = str(path)
    return {'capabilities': {'alwaysMatch': caps, 'firstMatch': [{}]}}


def create_session(server, payload):
    """请求Appium创建Android会话，返回有效会话数据；失败时不覆盖旧连接记录。"""
    request = Request(server.rstrip('/')+'/session', data=json.dumps(payload).encode(),
                      headers={'Content-Type':'application/json'}, method='POST')
    try:
        with urlopen(request, timeout=180) as response:
            value = json.load(response)['value']
    except urllib.error.HTTPError as error:
        detail = json.load(error).get('value', {})
        raise RuntimeError('Android Chrome会话未建立：'+detail.get('message','Appium请求失败')) from None
    if value.get('error') or not value.get('sessionId'):
        raise RuntimeError('Appium未返回有效会话；未覆盖已保存的连接记录')
    return value


def main():
    """识别设备或创建独立Android连接；dry-run不连接、不写会话和目标记录。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('devices','connect'))
    parser.add_argument('--udid')
    parser.add_argument('--appium-url', default=DEFAULT_SERVER)
    parser.add_argument('--chromedriver', help='可选：与设备Chrome主版本匹配的Chromedriver路径')
    parser.add_argument('--session-file', type=Path, default=WORK/'current-android-appium-session.json')
    parser.add_argument('--dry-run', action='store_true', help='只显示会话配置，不连接手机、不写记录')
    args = parser.parse_args()
    if args.action == 'devices':
        print(json.dumps({'android':android_devices(), 'ios':ios_devices()}, ensure_ascii=False, indent=2))
        return
    preferences = json.loads(PREFERENCES.read_text()) if PREFERENCES.exists() else {}
    if args.dry_run and args.udid:
        device = {'platform':'android','udid':args.udid,'name':'Android（待实机识别）','state':'device'}
    else:
        device = choose_device(android_devices(), udid=args.udid, preferred=preferences.get('android'))
        if not device.get('chrome_version'):
            raise RuntimeError('目标手机未检测到普通Chrome；不替换成其他浏览器或自动安装')
    payload = android_capabilities(device, args.chromedriver)
    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    result = create_session(args.appium_url, payload)
    record = {'session_id':result['sessionId'], 'appium_url':args.appium_url,
              'device':device, 'created_at':datetime.now(timezone.utc).isoformat()}
    args.session_file.parent.mkdir(parents=True, exist_ok=True)
    args.session_file.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    preferences['android'] = device['udid']
    PREFERENCES.write_text(json.dumps(preferences, ensure_ascii=False, indent=2))
    print(json.dumps(record, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError) as error:
        raise SystemExit(str(error))
