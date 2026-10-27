#!/usr/bin/env python3
"""由本轮独立记录和当前用例目录生成跨平台Allure报告。"""
import argparse
import hashlib
import html
import json
import mimetypes
import shutil
import subprocess
import tempfile
import uuid
from collections import Counter
from pathlib import Path

from web_ui_full_rerun import BATCH, GUEST_CASES, ROOT
from web_ui_extended_cases import ALL_CASES as CASES, SUBSCRIBED_IDS
from web_ui_environment import environment_plan, configuration
from web_ui_report_branding import apply_branding
from web_ui_case_index import entries as source_entries
from web_ui_platform_cases import platform_cases

try:
    from PIL import Image
except ImportError:
    Image = None

STATUS={'PASS':'passed','FAIL':'failed','BLOCKED':'broken','REVIEW':'broken','NOT_APPLICABLE':'skipped','NOT_RUN':'skipped','SKIPPED_ENV':'skipped','SKIPPED_STATE':'skipped'}


def write(path,data):
    """将结构化报告内容以UTF-8和缩进写入指定JSON文件。"""
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')


def attach(results, names):
    """将本轮证据复制为Allure附件，截图生成报告预览，返回附件描述列表。"""
    attachments=[]
    for name in names:
        path=Path(name)
        label=path.name
        if path.suffix.lower()=='.png' and Image is not None:
            # 报告使用清晰的小图预览，原始分辨率截图继续留在证据目录。
            previews=BATCH/'allure-preview-images';previews.mkdir(exist_ok=True)
            preview=previews/(hashlib.sha256(path.read_bytes()).hexdigest()[:24]+'.jpg')
            if not preview.exists():
                with Image.open(path) as im:
                    im=im.convert('RGB');im.thumbnail((1000,1900));im.save(preview,quality=85,optimize=True)
            path=preview
            label+='（报告预览）'
        source=hashlib.sha256(path.read_bytes()).hexdigest()[:24]+'-attachment'+path.suffix
        shutil.copyfile(path,results/source)
        attachments.append({'name':label,'source':source,'type':mimetypes.guess_type(path.name)[0] or 'text/plain'})
    return attachments


def main():
    """从指定批次真实结果生成当前目录的Allure，动态标注设备；未执行项不计为通过。"""
    global BATCH
    parser=argparse.ArgumentParser();parser.add_argument('--batch-dir',type=Path);args=parser.parse_args()
    if args.batch_dir:BATCH=args.batch_dir.resolve()
    data=json.loads((BATCH/'report.json').read_text())
    device=data.get('device',{})
    platform=device.get('platform','ios')
    platform_label={'ios':'iOS 端','android':'Android 端','web':'PC 端'}.get(platform,'未知端')
    device_name=device.get('name','iPhone 13')
    device_label=f"{device_name} / {platform} {device.get('version','18.7.8' if platform=='ios' else '未知')} / 普通 Chrome"
    framework={'android':'Appium UiAutomator2','ios':'Appium XCUITest','web':'Chrome / CUA Playwright'}.get(platform,'未知驱动')
    metadata=json.loads((BATCH/'preconditions.json').read_text()) if (BATCH/'preconditions.json').is_file() else {}
    run_environment=data['context'].get('run_environment','test')
    environment_label={'test':'test（测试环境）','prod':'prod（生产环境）'}.get(run_environment,run_environment)
    cases=platform_cases(GUEST_CASES+CASES,platform)
    selected=data['context'].get('selected_case_ids')
    if selected:
        unknown=set(selected)-{case[0] for case in cases}
        if unknown:raise ValueError('未知复测用例：'+', '.join(sorted(unknown)))
        cases=[case for case in cases if case[0] in selected]
    report_title=f'Web UI · {platform_label} · {environment_label} · ' + (f'异常{len(cases)}项复测' if selected else f'全量{len(cases)}项复测') + ('（执行阻塞）' if data['context'].get('execution_blocker') else '')
    statuses=Counter(); identities=[]
    source_index={item['id']:item for item in source_entries()}
    with tempfile.TemporaryDirectory(prefix='rvc-full-allure-') as temp:
        results=Path(temp)/'results';results.mkdir()
        for case_id,module,title,steps,expected in cases:
            blocker=data['context'].get('execution_blocker')
            pending_time=blocker['time'] if blocker else max((r['stop'] for r in data['results'].values()),default=0)
            pending={'status':'NOT_RUN','actual':'本轮尚未进入该用例。'+(blocker['reason'] if blocker else '不作为通过证据。'),'executed_steps':[],'start':pending_time,'stop':pending_time,'evidence':blocker['evidence'] if blocker else []}
            result=data['results'].get(case_id,pending)
            if platform=='android' and case_id in {'G-10','N-01'}:
                expected=['拒绝Chrome或系统音视频权限后，出现开启权限说明和How Can I Provide Permission?入口']
            elif platform=='android' and case_id in {'G-11','N-02'}:
                steps=['从拒绝提示打开How Can I Provide Permission?','依次点击Next、Next，核对三步权限说明','点击Enjoy关闭引导']
                expected=['按Android的Next→Next→Enjoy完成权限引导，页面恢复可操作']
            status=STATUS[result['status']];statuses[status]+=1
            identity=str(uuid.uuid5(uuid.NAMESPACE_URL,str(BATCH)+':'+case_id))
            identities.append(identity)
            mode='网页游客' if case_id[0] in 'GM' else '已登录－已订阅' if case_id in SUBSCRIBED_IDS else '已登录 - 未订阅'
            ordered=lambda texts:'<ol>'+''.join('<li>'+html.escape(t)+'</li>' for t in texts)+'</ol>'
            desc='<p><strong>测试身份：</strong>'+mode+'　<strong>本轮：</strong>'+BATCH.name+'</p>'
            desc+='<p><strong>执行端：</strong>'+html.escape(platform_label)+'　<strong>测试环境：</strong>'+html.escape(environment_label)+'　<strong>被测地址：</strong><a href="'+html.escape(data['url'],quote=True)+'">'+html.escape(data['url'])+'</a></p>'
            if case_id not in data['results']:desc+='<p><strong>执行情况：尚未执行，环境阻塞；不是已执行失败。</strong></p>'
            if result.get('verification_mode')=='reused':desc+='<p>协议复用既有校验成果，来源时间和证据见实际结果；本轮未重复打开。</p>'
            if result.get('verification_mode')=='reviewed_execution':desc+='<p><strong>本轮执行复核：</strong>按本轮原始断言记录和用户现场确认复核，未重新操作设备；附件说明证据来源及局限。</p>'
            if case_id in {'L-04','L-05','L-06'} and data['context'].get('incoming_user_feedback'):
                desc+='<p><strong>用户补充：</strong>用户反馈曾看见假来电弹窗；自动化没有保存对应时刻的完整证据。此次已优化同步观察与点击前截图，仍需实测核验，原结果不改判通过。</p>'
            recorded_expected=result.get('expected')
            if recorded_expected and recorded_expected!=expected:
                desc+='<p><strong>用例已更新：</strong>下方为当前设计，本次实测采用旧版预期：'+html.escape('；'.join(recorded_expected))+'。新增要求尚未补测，不能作为已通过。</p>'
            source=source_index[case_id]
            implementation=source['implementation'].get(platform,{})
            source_label=str(implementation.get('file') or '尚未封装独立入口')+(':'+str(implementation['line']) if implementation.get('line') else '')
            desc+='<p><strong>用例定义：</strong><code>'+html.escape(source['definition']['file']+':'+str(source['definition']['line']))+'</code>　<strong>本平台实现：</strong><code>'+html.escape(source_label)+'</code></p>'
            desc+='<h3>设计步骤</h3>'+ordered(steps)+'<h3>本次执行步骤</h3>'+ordered(result['executed_steps'])
            desc+='<h3>预期结果</h3>'+ordered(expected)+'<h3>实际结果与判定依据</h3><p>'+html.escape(result['actual'])+'</p>'
            if result.get('recovery_notes'):
                desc+='<p><strong>历史补测准备（不属于用例步骤）：</strong>'+html.escape('；'.join(result['recovery_notes']))+'</p>'
            attachments=attach(results,result['evidence'])
            out={'uuid':identity,'historyId':hashlib.sha256((mode+case_id).encode()).hexdigest(),'testCaseId':case_id,'fullName':mode+'.'+case_id,'name':case_id+'｜'+title,'status':status,'stage':'finished','descriptionHtml':desc,'start':result['start'],'stop':result['stop'],
                 'labels':[{'name':'parentSuite','value':mode},{'name':'suite','value':module},{'name':'framework','value':framework},{'name':'host','value':device_name},{'name':'tag','value':platform},{'name':'tag','value':run_environment},{'name':'tag','value':result['status']}],
                 'steps':[{'name':str(i)+'、'+t,'status':'passed'} for i,t in enumerate(result['executed_steps'],1)]+[{'name':'核对预期和实际','status':status,'parameters':[{'name':'预期','value':'；'.join(expected)},{'name':'实际','value':result['actual']}]}], 'attachments':attachments}
            if status!='passed':out['statusDetails']={'message':result['actual']}
            write(results/(identity+'-result.json'),out)
        audit=BATCH/'execution-audit.json'
        write(audit,{'events':data['events'],'disabled_cases':configuration().get('disabled_cases',{}),
                     'disabled_results':data.get('disabled_results',{}),
                     'membership_rule':'test首次真实支付校验后，后续恢复使用当前用户信息接口data.uuid，在test后台设置Duration=864000并刷新Profile确认；prod禁止；不单列用例'})
        prep=[BATCH/name for name in ('device-preflight.json','device-query.png','account-cleanup.png','account-cleanup.txt','device-reset.png','preconditions.json','current-user-identity.json','membership-restore.txt','membership-restore.png','membership-extension-request.json','membership-extension-admin.png','membership-extension-profile.png','execution-audit.json') if (BATCH/name).is_file()]
        for support_id in data['context'].get('support_case_ids',[]):
            prep.extend(Path(name) for name in data['results'].get(support_id,{}).get('evidence',[]) if Path(name).is_file())
        if (BATCH/'payment-attempts.json').is_file():prep.append(BATCH/'payment-attempts.json')
        prep.extend(ROOT/name for name in ('web_ui_case_index.json','WebUI_用例索引.md') if (ROOT/name).is_file())
        if prep:
            write(results/'full-rerun-container.json',{'uuid':str(uuid.uuid5(uuid.NAMESPACE_URL,str(BATCH)+':preconditions')),'name':'本轮环境准备与账号前置核对','children':identities,'befores':[{'name':run_environment+' 环境前置记录','status':'passed','stage':'finished','attachments':attach(results,prep)}]})
        context=data['context']
        matching=f"游客初始{context.get('guest_matching_initial','未知')}次；点击启动尝试{context.get('guest_matching_starts','未知')}次（不代表已进入连接页）；含自动续配确认消耗{context.get('guest_matching_consumed','未知')}次；最近读取剩余{context.get('guest_matching_remaining_final','未知')}次"
        env={'测试环境':environment_label,'被测地址':data['url'],'设备':device_label,'本轮批次':BATCH.name,'环境规则':json.dumps(environment_plan(run_environment),ensure_ascii=False),'执行前清理':'prod规则跳过' if run_environment=='prod' else '见本轮环境前置附件','测试范围':f'共{len(cases)}项；关闭麦克风和摄像头已移除；新增项无本次证据则记未执行','会员到期':'首次真实支付后，test用当前data.uuid + Duration=864000后台恢复并刷新Profile确认；prod禁止；不单列S-08','轮播标准':'多图能自动轮播即可，3秒可接受','匹配使用':matching,'本次登录身份':context.get('member_identity','未知'),'Say Hi':data['results'].get('L-08',{}).get('actual','尚未执行'),'协议规则':'同一域名校验通过一次后复用成果；跨批次复用标明来源，明确复检后才重新打开'}
        env['设备']=device_label
        env['执行端']=platform_label
        env['全平台停用']='；'.join(f'{cid}：{reason}（Android/iOS/PC）' for cid,reason in configuration().get('disabled_cases',{}).items())
        if context.get('incremental_without_reset'):env['执行前清理']='沿用现有数据补测；未删除关联账号、未重置设备'
        if platform=='web':env['PC专属范围']='移除L-14/N-01/L-20/L-11/G-11/L-05；G-10检查权限引导，G-12检查Chrome重载提示；L-04首次来电立即接听；L-25逐次等待保存接口并间隔1.5秒；S-04首次Video前清理本站缓存并复核同UUID及会员；N-10未订阅时按实际余额判断送礼：足够成功，不足提示金币不足'
        if selected:
            env['测试范围']=('沿用当前数据，仅复测上轮异常：' if context.get('incremental_without_reset') else '重置设备后，仅复测上轮异常：')+', '.join(selected)
            env['上轮来源']=data['context'].get('rerun_source','未记录')
        env['设备标识']=device.get('udid','历史批次未记录')
        # 设备信息在报告生成时读取，不把 Android 标记为历史 iPhone。
        prop='\n'.join(k+'='+v for k,v in env.items())
        (results/'environment.properties').write_text(''.join(f'\\u{ord(c):04x}' if ord(c)>127 else c for c in prop),encoding='ascii')
        execution_label='桌面复测' if platform=='web' else '真机复测'
        write(results/'executor.json',{'name':platform+' Chrome '+run_environment+' '+execution_label,'type':'local','buildName':BATCH.name,'reportName':report_title})
        write(results/'categories.json',[{'name':'数据或环境阻塞','matchedStatuses':['broken']},{'name':'实际结果不符预期','matchedStatuses':['failed']}])
        shutil.copytree(results,BATCH/'allure-results',dirs_exist_ok=True)
        for folder,single in [('allure-full-rerun-report',False),('allure-full-rerun-single-file',True)]:
            dest=Path(temp)/folder
            cmd=['/Users/mac/Downloads/allure-2.35.1/bin/allure','generate',str(results),'-o',str(dest),'--lang','zh','--name',report_title]
            if single:cmd.append('--single-file')
            subprocess.run(cmd,check=True)
            apply_branding(dest,ROOT)
            shutil.copytree(dest,BATCH/folder,dirs_exist_ok=True)
            shutil.copytree(dest,ROOT/folder,dirs_exist_ok=True)
        print(dict(statuses));print(ROOT/'allure-full-rerun-single-file/index.html')


if __name__=='__main__':main()
