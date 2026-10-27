#!/usr/bin/env python3
"""导出去重用例、源码位置、平台入口及前置条件；查询时无需设备和浏览器。"""
import argparse
import ast
import json
import re
from pathlib import Path

from web_ui_full_rerun import GUEST_CASES
from web_ui_extended_cases import ALL_CASES
from web_ui_environment import configuration
from web_ui_platform_cases import disabled_case_reason

ROOT = Path(__file__).resolve().parent
CASES = GUEST_CASES + ALL_CASES


def location(filename, method=None, case_id=None):
    """通过实际源码解析行号，避免文档中的固定行号在修改后失效。"""
    path = ROOT/filename
    text = path.read_text(encoding='utf-8')
    if method and path.suffix == '.py':
        nodes = [n for n in ast.walk(ast.parse(text)) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == method]
        if nodes:
            return {'file':filename, 'line':nodes[0].lineno, 'method':method}
    if method and path.suffix == '.mjs':
        match = re.search(r'^(?:  (?:async )?'+re.escape(method)+r'\(|(?:export )?(?:async )?function '+re.escape(method)+r'\()', text, re.M)
    else:
        match = re.search(r'[\'\"]'+re.escape(case_id)+r'[\'\"]', text) if case_id else None
    if match:
        return {'file':filename, 'line':text.count('\n', 0, match.start())+1, 'method':method}
    raise ValueError(f'用例位置不存在：{filename} {method or case_id}')


def entries():
    """为每项列出定义和三个平台实现，未封装的手机Video明确标记人工阶段。"""
    output = []
    mobile = {'G-03':('web_ui_full_rerun.py','tabs'),'G-04':('web_ui_full_rerun.py','card_case'),'G-05':('web_ui_full_rerun.py','scroll_case'),
              'G-06':('web_ui_full_rerun.py','guest_action'),'G-07':('web_ui_full_rerun.py','guest_action'),'G-17':('web_ui_full_rerun.py','guest_profile'),
              'L-01':('web_ui_member_runner.py','agreements'),'L-02':('web_ui_full_rerun.py','wait_login'),
              'L-03':('web_ui_member_suite.py','discover'),'L-07':('web_ui_member_suite.py','sayhi'),'L-08':('web_ui_member_suite.py','quota'),
              'L-09':('web_ui_member_suite.py','video'),'L-10':('web_ui_member_suite.py','close_case'),'L-11':('web_ui_member_suite.py','masked'),
              'L-12':('web_ui_member_suite.py','detail_case'),'L-13':('web_ui_member_suite.py','masked'),'L-14':('web_ui_member_suite.py','menu'),
              'L-15':('web_ui_member_suite.py','block_dialog'),'L-16':('web_ui_member_suite.py','report_reasons'),'L-17':('web_ui_member_suite.py','report_submit'),
              'L-18':('web_ui_member_suite.py','block_cancel'),'L-19':('web_ui_member_suite.py','block_submit'),
              'L-20':('web_ui_member_suite.py','carousel'),'L-21':('web_ui_member_suite.py','carousel'),'L-22':('web_ui_member_suite.py','back_case'),
              'L-23':('web_ui_extended_suite.py','nickname'),'L-24':('web_ui_extended_suite.py','signature'),'L-25':('web_ui_extended_suite.py','tags_select'),
              'L-26':('web_ui_extended_suite.py','tags_cancel'),'L-27':('web_ui_extended_suite.py','premium_entry'),'L-28':('web_ui_extended_suite.py','premium_return'),
              'L-29':('web_ui_extended_suite.py','gems_unsubscribed'),'L-30':('web_ui_extended_suite.py','tiers'),
              'N-04':('web_ui_extended_suite.py','match_tabs'),'N-05':('web_ui_extended_suite.py','match_discover'),
              'N-10':('web_ui_exception_rerun.py','unsubscribed_gift'),'S-01':('web_ui_complete_suite.py','subscribe'),
              'S-02':('web_ui_complete_suite.py','gem_store'),'S-03':('web_ui_complete_suite.py','topup_gems'),
              'S-04':('web_ui_complete_suite.py','first_video'),'S-06':('web_ui_complete_suite.py','subscribed_video_dial'),
              'S-05':('web_ui_call_billing.py','empty_video'),'S-07':('web_ui_complete_suite.py','subscribed_gift_call'),
              'S-09':('web_ui_call_billing.py','billing_call')}
    web_rules=configuration().get('platform_rules',{}).get('web',{})
    web_excluded=set(web_rules.get('excluded_case_ids',[]))
    for cid, module, title, steps, expected in CASES:
        definition = 'web_ui_full_rerun.py' if cid[0] in 'GM' else 'web_ui_member_cases.py' if cid in {c[0] for c in ALL_CASES[:22]} else 'web_ui_extended_cases.py'
        web_method = 'incoming' if cid in {'L-04','L-05','L-06'} else 'observeLogin' if cid == 'L-02' else 'sayHiOnce' if cid == 'L-07' else 'execute'
        web = location('web_ui_standalone/web_ui_run.mjs',method='executeCases') if cid == 'L-01' else location('web_ui_desktop_suite.mjs', method=web_method)
        if web_method == 'execute' and cid != 'L-01' and cid not in web_excluded:
            text=(ROOT/'web_ui_desktop_suite.mjs').read_text()
            offset=text.index('  async execute(id)')
            match=re.search(r'[\'\"]'+re.escape(cid)+r'[\'\"]',text[offset:])
            if not match:raise ValueError('没有Web分发入口：'+cid)
            web['dispatch']={'file':'web_ui_desktop_suite.mjs','line':text.count('\n',0,offset+match.start())+1}
        if cid in web_excluded:web['status']='PC已移除；Android/iOS继续保留'
        if cid in web_rules.get('case_overrides',{}):web['case_override']=web_rules['case_overrides'][cid]
        if cid in {'L-04','L-05','L-06'}:
            target = ('web_ui_exception_rerun.py','incoming_cases')
        elif cid in {'G-10','G-11','G-12','N-01','N-02','N-03'}:
            target = ('web_ui_complete_suite.py','permissions')
        elif cid in {'M-09','M-10'}:
            target = ('web_ui_full_rerun.py','match_nav')
        elif cid in {'M-11','M-12','N-06','N-07'}:
            target = ('web_ui_complete_suite.py','matching_exit')
        elif cid in {'M-13','M-14','N-08','N-09'}:
            target = ('web_ui_complete_suite.py','conversation')
        else:
            target = mobile.get(cid)
        phone = location(*target) if target else {'file':None, 'line':None, 'method':None, 'status':'尚未封装独立手机入口，需交互执行并留证'}
        requires = []
        if cid in {'L-04','L-05','L-06'}:
            requires = ['从未登录状态启动L-02观察；登录后三分钟连续执行；不能用旧登录追溯']
        elif cid in {'S-05','S-09'}:
            requires = ['test环境、实际会员有效、已完成S-04首次Video提示；普通通话80金币/分钟']
        output.append({'id':cid, 'module':module, 'title':title, 'definition':location(definition,case_id=cid),
                       'implementation':{'web':web, 'android':dict(phone,platform='android'), 'ios':dict(phone,platform='ios')},
                       'requires':requires, 'steps':steps, 'expected':expected})
    return output


def export():
    """同步导出浏览器业务目录、源码索引JSON和便于人工查阅的Markdown。"""
    index = entries()
    (ROOT/'web_ui_browser_cases.json').write_text(json.dumps(CASES,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (ROOT/'web_ui_case_index.json').write_text(json.dumps(index,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lines = ['# Web UI 用例与源码索引', '', f'手机目录{len(index)}项；PC有效{sum(not item["implementation"]["web"].get("status") for item in index)}项。源码行号由脚本实时解析。页面通过按钮名称、角色和CSS定位，原生操作按Android/iOS/Web分别适配。', '',
             'S-10 已在 Android / iOS / PC 全部注释停用，备注：免费一分钟停用。不计入有效目录；可用 `--case S-10` 查看备注及原定义注释位置。', '',
             '| 编号 | 手机原始用例 | PC用例 | 定义 | Web实现 | 手机实现 |', '| --- | --- | --- | --- | --- | --- |']
    for item in index:
        definition, web, mobile = item['definition'], item['implementation']['web'], item['implementation']['android']
        fmt = lambda v: f'{v["file"]}:{v["line"]}' if v['file'] else '尚未封装独立入口'
        lines.append(f'| {item["id"]} | {item["title"]} | {"已移除" if web.get("status") else web.get("case_override",{}).get("title",item["title"])} | {fmt(definition)} | {"PC已移除" if web.get("status") else fmt(web)} | {fmt(mobile)} |')
    (ROOT/'WebUI_用例索引.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return index


def main():
    """按编号离线查询定义、步骤、预期及实现位置，或重新生成目录。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case')
    parser.add_argument('--export', action='store_true')
    args = parser.parse_args()
    values = export() if args.export else entries()
    if args.case and (reason:=disabled_case_reason(args.case)):
        values=[{'id':args.case,'status':'DISABLED','reason':reason,'platforms':['android','ios','web'],
                 'definition':location('web_ui_extended_cases.py',case_id=args.case)}]
    elif args.case:
        values = [v for v in values if v['id'] == args.case]
        if not values:
            parser.error('未知用例编号：'+args.case)
    print(json.dumps(values,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
