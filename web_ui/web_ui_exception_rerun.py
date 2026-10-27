#!/usr/bin/env python3
"""仅处理已选择的异常用例，登录与环境准备作为前置事件保存。"""
import argparse
import json
import time
from pathlib import Path

from web_ui_complete_suite import Suite, configure_batch, runner, member
from web_ui_native_login_watch import watch_login
from web_ui_execution_state import device_run


def incoming_cases(suite):
    """按本次回调的Chat分类补测来电，记录有界观察和不适用分支。"""
    classification=suite.data['context'].get('login_classification',{})
    state=classification.get('state')
    evidence=classification.get('evidence',[])
    login_at=classification.get('login_at',time.time())
    start=int(login_at*1000)
    suite.web()
    if state not in {'new','existing'}:
        observed=suite.data['context'].get('incoming_events',[])
        evidence=evidence+[path for event in observed for path in event.get('evidence',[])]
        for case_id in ('L-04','L-05','L-06'):
            suite.record(case_id,'BLOCKED',f'实际已捕获来电{len(observed)}次，来电截图及操作单独保留；本次登录窗口未能确定新老用户，不能追溯判断身份和时限。',[],start,evidence)
        return
    if state=='new':
        suite.record('L-06','SKIPPED_STATE','本次登录30秒内Chat官方消息证据判定为新用户；老用户无欢迎来电分支不适用。',
                     ['连续观察真实登录后的Chat窗口，核对teamoffice消息或Official非零角标'],start,evidence)
    else:
        for case_id in ('L-04','L-05'):
            suite.record(case_id,'SKIPPED_STATE','本次完整30秒Chat未出现teamoffice，按老用户跳过新用户来电。',
                         ['连续观察真实登录后30秒的Chat窗口'],start,evidence)
    samples=[]
    seen=any(login_at<=event['seen_at']<=login_at+180 for event in suite.data['context'].get('incoming_events',[]))
    while time.time()<login_at+180:
        body=suite.text()
        invitation='is inviting you to private call.' in body or 'Incoming call' in body
        samples.append({'time':time.time(),'invitation':invitation,'path':suite.js('return location.pathname')})
        if invitation:
            seen=True
            if state=='new':
                suite.poll_incoming()
                if suite.data['context'].get('incoming_joined'):
                    suite.stop_call()
                    break
            else:break
        time.sleep(.3)
    path=suite.path.parent/'incoming-window-samples.json'
    path.write_text(json.dumps(samples,ensure_ascii=False,indent=2))
    evidence=evidence+[str(path)]+suite.web_capture('incoming-window-end')
    if state=='existing':
        suite.record('L-06','FAIL' if seen else 'PASS',
                     f'本次老用户从登录后30秒Chat分类完成至登录后{time.time()-login_at:.1f}秒连续前台观察，欢迎来电出现={seen}。',
                     ['完成登录后30秒Chat分类','在前台连续观察至登录后三分钟，记录来电状态'],start,evidence)
    else:
        for case_id in ('L-04','L-05'):
            if case_id not in suite.data['results']:
                suite.record(case_id,'BLOCKED','已确认本次新用户；观察至登录后三分钟，未捕获本项所需来电，详见采样。',
                             ['捕获本次登录和teamoffice消息','在前台观察至登录后三分钟'],start,evidence)


def unsubscribed_gift(suite):
    """只为N-10匹配一次并送礼，不重复执行已通过的聊天和视频交换用例。"""
    suite.profile_page()
    before=suite.balance()
    suite.click_text('Match');suite.wait_text('Start matching')
    if not suite.remaining():raise member.Blocked('本次无免费匹配次数，未启动通话。')
    suite.native_tap_label('Start matching')
    suite.handle_android_media_request()
    suite.event('N-10仅启动一次匹配',balance=before)
    deadline=time.monotonic()+35
    try:
        while time.monotonic()<deadline:
            media=suite.media_positions()
            if len(media)>=2 and all(item['ready']>=2 for item in media) and 'Call connecting' not in suite.text():
                return suite.gift(False,before)
            suite.poll_incoming()
            time.sleep(.15)
        raise member.Blocked('本次35秒内未确认双视频连接，未重复启动匹配。')
    finally:suite.stop_call()


def main():
    """绑定本轮独立批次和原手机会话，执行指定异常阶段。"""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase',choices=('login-watch','incoming','unsubscribed-gift','subscription','topup','verify-subscription','verify-topup','inspect'))
    parser.add_argument('--batch-dir',type=Path,required=True)
    parser.add_argument('--session-file',type=Path,required=True)
    args=parser.parse_args()
    with device_run(args.session_file):
        configure_batch(args.batch_dir)
        suite=Suite(session_file=args.session_file,environment='test')
        suite.d.health()
        suite.data['context'].pop('session_blocker',None);suite.save()
        if not suite.data['context'].get('selected_case_ids'):raise ValueError('缺少本轮异常用例范围')
        if args.phase=='login-watch':
            watch_login(suite);incoming_cases(suite);return
        suite.web()
        if args.phase=='incoming':incoming_cases(suite)
        elif args.phase=='unsubscribed-gift':suite.run_new('N-10',lambda:unsubscribed_gift(suite))
        elif args.phase=='subscription':suite.run_new('S-01',suite.subscribe)
        elif args.phase=='topup':suite.run_new('S-03',suite.topup_gems)
        elif args.phase=='verify-subscription':suite.verify_payment_case('subscription')
        elif args.phase=='verify-topup':suite.verify_payment_case('coin')
        else:print(suite.text())


if __name__=='__main__':main()
