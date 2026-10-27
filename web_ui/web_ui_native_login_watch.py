#!/usr/bin/env python3
"""Android原生登录阶段只观察回站和Chat窗口，不接管凭据或身份验证。"""
import argparse
import json
import time
from pathlib import Path

from web_ui_complete_suite import Suite, configure_batch
from web_ui_execution_state import device_run
from web_ui_account_state import StateUnconfirmed


class LoginWaitingForUser(StateUnconfirmed):
    """系统或Google需要用户验证，停止无期限等待并保留登录检查点。"""


def on_merchant(names, host):
    """按完整主机名核对原生地址，不把Google跳转参数中的商户域名当回站。"""
    return any(value.removeprefix('https://').removeprefix('http://').split('/')[0]==host for value in names)


def human_login_step(names):
    """只识别验证阶段，不保存验证码、密码或其他账号列表。"""
    if any('指纹' in value for value in names):return '需要在手机完成指纹验证'
    if any('两步验证' in value or '兩步驟驗證' in value for value in names):return '需要完成Google两步验证'
    if any(value in {'Verify it’s you', "Verify it's you", '验证身份', '驗證身分'} for value in names):
        return '需要完成Google本人身份验证'
    return None


def merchant_login_state(suite, names, host):
    """原生地址确认已在本站后补读网页按钮；Google前台时不切换商户标签。"""
    if not on_merchant(names, host):
        return False, False
    if 'Continue with Google' in names or 'Connect' in names:
        return 'Continue with Google' in names, 'Connect' in names
    # Android有时只导出地址栏，网页仍已渲染；不能仅依赖原生树判回调。
    suite.web()
    state = suite.js("""return {
        prompt:Array.from(document.querySelectorAll('button')).some(e=>e.innerText.trim()==='Continue with Google'),
        connected:Array.from(document.querySelectorAll('button')).some(e=>e.innerText.trim()==='Connect')
    }""")
    return state['prompt'], state['connected']


def watch_login(suite, timeout=45):
    """原生地址结合本站DOM确认回站，复用既有30秒Chat和来电同步观察。"""
    suite.d.request('/context', {'name':'NATIVE_APP'})
    deadline = time.monotonic() + timeout
    host = suite.policy.base_url.split('/')[2]
    armed=False
    waiting=None
    previous=suite.data['context'].get('login_classification')
    if previous:
        if previous.get('state') in {'new','existing'}:return previous
        raise StateUnconfirmed('本次登录分类已开始但证据不完整，禁止重新计时追溯；保留原窗口结果。')
    while time.monotonic() < deadline:
        names = suite.d.names()
        merchant = on_merchant(names,host)
        prompt, connected = merchant_login_state(suite, names, host)
        suite.d.request('/context', {'name':'NATIVE_APP'})
        human=human_login_step(names)
        if prompt or on_merchant(names,'accounts.google.com') or human or {'identifierId','password'}&names:
            armed=True
        if human and human!=waiting:
            waiting=human
            suite.data['context']['login_checkpoint']={'stage':'waiting_user','reason':human,'checked_at':time.time()}
            suite.event('登录需要用户验证，短时监测回站',reason=human,timeout_seconds=timeout)
        if merchant and connected:
            if not armed:
                raise StateUnconfirmed('启动时已经登录，缺少连续回调观察；不按当前Chat追溯新老用户。')
            login_at = time.time()
            suite.web()
            result = suite.observe_login_success(login_at)
            suite.data['context']['login_checkpoint']={'stage':'classified','checked_at':result['observed_until']}
            suite.event('原生地址与网页DOM确认Google回站后完成Chat分类', **result)
            return result
        time.sleep(.5)
    suite.data['context']['login_checkpoint']={'stage':'waiting_user' if waiting else 'timeout','reason':waiting or '限定时间内未回站','checked_at':time.time()}
    suite.save()
    raise LoginWaitingForUser((waiting or '未观察到登录回站')+f'；本阶段已等待{timeout}秒后停止。恢复时先核对前台状态，不能追溯错过的30秒窗口。')


def main():
    """绑定Android独立批次和原生Appium会话，输出实际观察到的登录分类。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch-dir', required=True, type=Path)
    parser.add_argument('--session-file', required=True, type=Path)
    args = parser.parse_args()
    with device_run(args.session_file):
        configure_batch(args.batch_dir, update_pointer=False)
        suite = Suite(session_file=args.session_file)
        suite.d.health()
        suite.data['context'].pop('session_blocker',None)
        suite.save()
        if suite.d.platform != 'android':
            raise RuntimeError('此原生观察入口仅用于Android；iOS沿用既有网页登录观察')
        print(json.dumps(watch_login(suite), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
