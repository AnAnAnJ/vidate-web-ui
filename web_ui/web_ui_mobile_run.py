#!/usr/bin/env python3
"""移动Chrome共用阶段入口：Android / iOS 分别保存会话、批次及证据。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from web_ui_complete_suite import Suite, configure_batch, runner
from web_ui_environment import ExecutionPolicy, configuration

ROOT = Path(__file__).resolve().parent


def client_identity(suite):
    """只读取配置列出的设备ID键；多个不同值时阻塞，不输出其他存储内容。"""
    keys = configuration().get('device_id_keys', ['ios_deviceId','android_deviceId','client_id','deviceId'])
    found = suite.js('''return arguments[0].map(key=>({key,value:localStorage.getItem(key)}))
        .filter(item=>item.value)''', keys)
    for item in found:
        try:
            value = json.loads(item['value'])
        except (ValueError, TypeError):
            value = item['value']
        item['value'] = value.strip() if isinstance(value, str) else None
    found = [item for item in found if item['value']]
    unique = {item['value'] for item in found}
    if len(unique) != 1:
        raise RuntimeError('设备client_id缺失或存在冲突，需要依据当前网站确认存储键后再执行重置')
    return {'client_id':unique.pop(), 'source_keys':[item['key'] for item in found]}


def require_preflight(suite, batch, mode):
    """完整test轮次必须有同设备同网址的清理及重置记录；补测与prod按原规则。"""
    if suite.policy.environment == 'prod' or mode == 'incremental':
        return
    path = batch/'device-preflight.json'
    if not path.exists():
        raise RuntimeError('完整test轮次尚未完成设备关联用户清理与追加6位后缀重置；先完成前置UI操作并保存证据')
    data = json.loads(path.read_text())
    device = suite.data['device']
    identity = client_identity(suite)
    if (data.get('status') != 'reset_complete' or data.get('environment') != 'test'
            or data.get('website') != suite.policy.base_url
            or data.get('device_udid') != device['udid']
            or data.get('old_client_id') != identity['client_id']):
        raise RuntimeError('重置前置记录与当前设备/网址/client_id不一致，停止本轮动作')


def main():
    """解析平台会话和阶段参数，复用既有用例；仅显式选择subscribe阶段才进入订阅操作。"""
    phases = ('inspect','guest-basic','guest-permissions','guest-navigation','guest-exit','guest-call',
              'profile','member-discover','member-permissions','member-navigation','member-exit',
              'member-call','membership','subscribe','billing-paid','empty-video')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=phases)
    parser.add_argument('--platform', choices=('android','ios'), default='android')
    parser.add_argument('--session-file', type=Path)
    parser.add_argument('--batch-dir', type=Path, required=True, help='本设备独立批次；后续阶段沿用同一路径')
    parser.add_argument('--mode', choices=('full','incremental'), default='full')
    parser.add_argument('--env', choices=('test','prod'), default='test')
    parser.add_argument('--url')
    args = parser.parse_args()
    # 在连接或导航手机前验证prod地址，避免把test网址当作线上。
    ExecutionPolicy.create(args.env, args.url)
    session_file = args.session_file or ROOT.parent/'work'/('current-android-appium-session.json'
                                                         if args.platform=='android' else 'current-appium-session.json')
    if not session_file.exists():
        raise RuntimeError('没有该平台的已连接会话，请先识别设备并建立会话')
    batch = configure_batch(args.batch_dir, update_pointer=False)
    suite = Suite(environment=args.env, url=args.url, session_file=session_file)
    if suite.d.platform != args.platform:
        raise RuntimeError('指定平台与Appium真实设备不符，停止执行')
    suite.set_execution_mode(args.mode=='incremental');suite.web()
    if args.phase=='inspect':
        print(json.dumps({'device':suite.data['device'], 'url':suite.js('return location.origin+location.pathname'),
                          'identity':client_identity(suite), 'batch':str(batch)},ensure_ascii=False,indent=2))
        suite.web_capture('device-connected')
        return
    require_preflight(suite, batch, args.mode)
    actions = {'guest-basic':suite.basic, 'guest-permissions':lambda:suite.permissions('G'),
               'guest-navigation':suite.match_nav, 'guest-exit':lambda:suite.matching_exit(False),
               'guest-call':lambda:suite.conversation(False), 'profile':suite.profile_cases,
               'member-discover':lambda:suite.run_case('L-03',runner.CATALOG['L-03'][3],suite.discover),
               'member-permissions':lambda:suite.permissions('N'),
               'member-navigation':lambda:(suite.run_new('N-04',suite.match_tabs),suite.run_new('N-05',suite.match_discover)),
               'member-exit':lambda:suite.matching_exit(True), 'member-call':lambda:suite.conversation(True),
               'membership':lambda:print(json.dumps(suite.profile_membership().to_dict(),ensure_ascii=False)),
               'subscribe':lambda:suite.run_new('S-01',suite.subscribe),
               'billing-paid':lambda:suite.run_new('S-09',lambda:suite.billing_call(False)),
               # S-10 / billing-free：免费一分钟停用，所有端不执行。
               'empty-video':lambda:suite.run_new('S-05',suite.empty_video)}
    actions[args.phase]()


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError) as error:
        raise SystemExit(str(error))
