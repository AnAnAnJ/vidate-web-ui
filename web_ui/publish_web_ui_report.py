#!/usr/bin/env python3
"""复用现有18080报告服务及外部/allure/代理，发布前保留原报告备份。"""
import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen


def summary(url):
    """读取指定报告地址的Allure统计，用于校验发布前后内容一致。"""
    with urlopen(url.rstrip('/')+'/widgets/summary.json',timeout=20) as response:
        return json.load(response)


def main():
    """备份原报告后发布指定批次，并核对本地及外部Allure标题和统计一致。"""
    root=Path(__file__).resolve().parent
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--batch-dir',type=Path)
    p.add_argument('--destination',type=Path,default=Path('/Users/mac/Documents/Codex/2026-08-20/new-chat/outputs/api_auto_test/reports/allure-report'))
    p.add_argument('--external-url',default='http://sj-app-down.jm.ijunj.com/allure/')
    args=p.parse_args()
    batch=args.batch_dir or Path(json.loads((root.parent/'work/current-ui-rerun.json').read_text())['batch'])
    source=batch/'allure-full-rerun-report'
    expected=json.loads((source/'widgets/summary.json').read_text())
    destination=args.destination
    if destination.exists():
        backup=destination.parent/'allure-backups'/datetime.now().strftime('%Y%m%d-%H%M%S')
        shutil.copytree(destination,backup)
    else:backup=None
    shutil.copytree(source,destination,dirs_exist_ok=True)
    local=summary('http://127.0.0.1:18080/')
    external=summary(args.external_url)
    for actual in (local,external):
        if actual['statistic']!=expected['statistic'] or actual['reportName']!=expected['reportName']:
            raise RuntimeError('发布后报告统计或标题不一致')
    record={'url':args.external_url,'batch':str(batch),'destination':str(destination),'backup':str(backup) if backup else None,'statistic':external['statistic'],'reportName':external['reportName']}
    (batch/'publication.json').write_text(json.dumps(record,ensure_ascii=False,indent=2))
    print(json.dumps(record,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
