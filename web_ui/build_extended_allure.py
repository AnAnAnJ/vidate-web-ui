#!/usr/bin/env python3
"""仅统计当前有效的新增业务用例，旧用例不合并成新实测。"""
import hashlib
import html
import json
import shutil
import subprocess
import tempfile
import uuid
from collections import Counter
from pathlib import Path

import build_full_rerun_allure as common
from web_ui_extended_cases import NEW_CASES, SUBSCRIBED_IDS
from web_ui_extended_suite import POINTER, ROOT


def main():
    """按当前有效新增目录生成增量报告，停用项不计入统计。"""
    batch=Path(json.loads(POINTER.read_text())['batch'])
    common.BATCH=batch
    data=json.loads((batch/'report.json').read_text())
    statuses=Counter()
    with tempfile.TemporaryDirectory(prefix='web-ui-extended-allure-') as folder:
        results=Path(folder)/'results';results.mkdir()
        for cid,module,title,steps,expected in NEW_CASES:
            r=data['results'].get(cid,{'status':'NOT_RUN','actual':'本次尚未执行，不计为通过。','executed_steps':[],'start':0,'stop':0,'evidence':[]})
            status=common.STATUS[r['status']];statuses[status]+=1
            mode='已订阅（未标记项处理后执行）' if cid in SUBSCRIBED_IDS else '已登录未订阅'
            listing=lambda items:'<ol>'+''.join('<li>'+html.escape(t)+'</li>' for t in items)+'</ol>'
            desc='<p>本次只补测新增项，沿用账号数据；未执行删除账号或设备重置。旧22项不重复统计。</p>'
            desc+='<p>test会员有效期5分钟：超过有效期后刷新确认当前状态，正常到期不判为订阅丢失。报告已按此规则修正。</p>'
            desc+='<p>测试身份：'+mode+'；环境：'+data['context'].get('run_environment','test')+'</p>'
            desc+='<h3>设计步骤</h3>'+listing(steps)+'<h3>实际执行步骤</h3>'+listing(r['executed_steps'])
            desc+='<h3>预期结果</h3>'+listing(expected)+'<h3>实际结果与判定依据</h3><p>'+html.escape(r['actual'])+'</p>'
            identity=str(uuid.uuid5(uuid.NAMESPACE_URL,str(batch)+cid))
            result={'uuid':identity,'historyId':hashlib.sha256((data['url']+cid).encode()).hexdigest(),'testCaseId':cid,'fullName':mode+'.'+cid,'name':cid+'｜'+title,'status':status,'stage':'finished','descriptionHtml':desc,'start':r['start'],'stop':r['stop'],
                'links':[{'type':'custom','name':'被测网站','url':data['url']}],
                'labels':[{'name':'parentSuite','value':mode},{'name':'suite','value':module},{'name':'framework','value':'Appium XCUITest'},{'name':'tag','value':data['context'].get('run_environment','test')},{'name':'tag','value':r['status']}],
                'steps':[{'name':f'{i}、{t}','status':'passed'} for i,t in enumerate(r['executed_steps'],1)]+[{'name':'核对预期与实际','status':status,'parameters':[{'name':'预期','value':'；'.join(expected)},{'name':'实际','value':r['actual']}]}],
                'attachments':common.attach(results,r['evidence'])}
            if status!='passed':result['statusDetails']={'message':r['actual']}
            common.write(results/(identity+'-result.json'),result)
        common.write(results/'executor.json',{'name':'iPhone Chrome 增量补测','type':'local','buildName':batch.name,'reportName':f'{len(NEW_CASES)}项新增业务用例'})
        common.write(results/'categories.json',[{'name':'前置数据或环境阻塞','matchedStatuses':['broken']},{'name':'实际与预期不符，根因待复核','matchedStatuses':['failed']}])
        audit=batch/'execution-audit.json';common.write(audit,{'context':data['context'],'events':data['events']})
        common.write(results/'audit-container.json',{'uuid':str(uuid.uuid4()),'name':'增量范围与执行审计','children':[str(uuid.uuid5(uuid.NAMESPACE_URL,str(batch)+c[0])) for c in NEW_CASES],'befores':[{'name':'沿用账号；不清数据；未订阅项先处理','status':'passed','attachments':common.attach(results,[str(audit),str(ROOT/'WebUI_已登录_去重及新增用例.md')])}]})
        shutil.copytree(results,batch/'allure-results',dirs_exist_ok=True)
        for name,single in [('allure-extended-report',False),('allure-extended-single-file',True)]:
            dest=Path(folder)/name
            cmd=['/Users/mac/Downloads/allure-2.35.1/bin/allure','generate',str(results),'-o',str(dest),'--lang','zh','--name','Web UI · 已登录增量补测']
            if single:cmd.append('--single-file')
            subprocess.run(cmd,check=True)
            common.apply_branding(dest,ROOT)
            shutil.copytree(dest,batch/name,dirs_exist_ok=True)
        print(dict(statuses));print(batch/'allure-extended-single-file/index.html')
    lines=['# 已登录增量补测执行报告','',f'批次：{batch.name}；{len(NEW_CASES)}项新增业务用例，旧22项不计本次执行。','',str(dict(statuses)),'',
           'test会员有效期为5分钟，是执行条件，不单列用例；确认到期后重新执行测试订阅并继续，过程写入审计事件。','',
           '沿用当前账号及设备数据。协议本次不重复打开。关闭麦克风及摄像头继续移除。','']
    for cid,_,title,_,expected in NEW_CASES:
        r=data['results'].get(cid,{'status':'NOT_RUN','actual':'未执行','executed_steps':[],'evidence':[]})
        lines += [f'## {cid} {title} — {r["status"]}','','实际步骤：','']+[f'{i}. {t}' for i,t in enumerate(r['executed_steps'],1)]
        lines += ['','预期：'+'；'.join(expected),'','实际：'+r['actual'],'','证据：','']+[f'- [{Path(p).name}](<{p}>)' for p in r['evidence']]+['']
    (batch/'执行报告.md').write_text('\n'.join(lines))


if __name__=='__main__':main()
