#!/usr/bin/env python3
"""从本套件实测结果生成独立 Allure，保留原游客报告。"""
from __future__ import annotations

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

from web_ui_member_cases import CASES
from web_ui_report_branding import apply_branding

ROOT = Path(__file__).resolve().parent
ART = ROOT / "iphone_ui_artifacts/member_2026_10_19"
RESULTS = ROOT / "allure-member-results"
REPORT = ROOT / "allure-member-report"
SINGLE = ROOT / "allure-member-single-file"
STATUS = {"PASS": "passed", "FAIL": "failed", "BLOCKED": "broken", "NOT_APPLICABLE": "skipped", "NOT_RUN": "skipped", "SKIPPED_ENV": "skipped", "SKIPPED_STATE": "skipped"}


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def attachment(target, path, label):
    source = hashlib.sha256(path.read_bytes()).hexdigest()[:24] + "-attachment" + path.suffix
    shutil.copyfile(path, target / source)
    return {"name": label, "source": source, "type": mimetypes.guess_type(path.name)[0] or "text/plain"}


def make_results(target, data):
    for case_id, group, title, planned, expected in CASES:
        result = data["results"].get(case_id, {"status": "NOT_RUN", "actual": "本轮尚未执行", "executed_steps": [], "start": 0, "stop": 0, "evidence": []})
        status = STATUS[result["status"]]
        identity = str(uuid.uuid5(uuid.NAMESPACE_URL, "randomvideochat.member." + case_id))
        esc = html.escape
        def ul(items):
            return "<ol>" + "".join("<li>"+esc(x)+"</li>" for x in items) + "</ol>"
        desc = f"<p><strong>编号：</strong>{case_id}　<strong>身份：</strong>已登录－未订阅　<strong>结果：</strong>{esc(result['status'])}</p>"
        desc += "<h3>设计步骤</h3>" + ul(planned) + "<h3>本次执行步骤</h3>" + ul(result["executed_steps"])
        desc += "<h3>预期结果</h3>" + ul(expected) + "<h3>实际结果与判定依据</h3><p>"+esc(result["actual"])+"</p>"
        if result.get("timing_note"):
            desc += "<p><small>时间说明："+esc(result["timing_note"])+"</small></p>"
        attachments = [attachment(target, Path(path), Path(path).name) for path in result["evidence"]]
        out = {"uuid": identity, "historyId": hashlib.sha256(case_id.encode()).hexdigest(), "testCaseId": case_id,
               "fullName": "randomvideochat.member."+case_id, "name": case_id+"｜"+title,
               "status": status, "stage": "finished", "start": result["start"], "stop": result["stop"],
               "descriptionHtml": desc, "labels": [{"name": k, "value": v} for k,v in (
                   ("parentSuite","已登录－未订阅"),("suite",group),("feature",group),
                   ("framework","Appium XCUITest"),("host","iPhone 13"),("tag",result["status"]))],
               "parameters": [{"name":"执行状态","value":result["status"]}],
               "steps": [{"name":str(i)+"、"+step,"status":"passed"} for i,step in enumerate(result["executed_steps"],1)],
               "attachments": attachments}
        out["steps"].append({"name":"核对预期与实际", "status":status,
                             "parameters":[{"name":"预期结果","value":"；".join(expected)},{"name":"实际结果","value":result["actual"]}]})
        if status != "passed":
            out["statusDetails"] = {"message":result["actual"], "known":result["status"] in ("BLOCKED","NOT_APPLICABLE")}
        write_json(target/(identity+"-result.json"),out)
    environment = {"测试身份":"网页已登录、未订阅；普通 Chrome", "设备":"iPhone 13 / iOS 18.7.8", "URL":data["url"],
                   "判定映射":"通过=passed；失败=failed；前置阻塞=broken；条件不适用/未执行=skipped",
                   "账号条件":data["context"].get("registration","未确认"),
                   "Say_Hi初始已用":str(data["context"].get("initial_say_hi_used","未知")),
                   "证据范围":"前台 iPhone Chrome 网页控件、页面文本、真机截图；服务端内部状态未核验",
                   "已知失败":"无",
                   "未完成":"L-04/L-05 来电需重发；L-06旧用户分支不适用"}
    prop = "\n".join(k+"="+v for k,v in environment.items())
    (target/"environment.properties").write_text("".join(f"\\u{ord(c):04x}" if ord(c)>127 else c for c in prop),encoding="ascii")
    write_json(target/"executor.json",{"name":"iPhone Chrome 真机 UI 自动化", "type":"local", "buildName":"2026-10-19 已登录未订阅", "reportName":"Web UI · 已登录未订阅 UI 报告"})
    write_json(target/"categories.json",[{"name":"前置条件或数据阻塞","matchedStatuses":["broken"]},{"name":"实际行为不符合预期","matchedStatuses":["failed"]}])


def main():
    data=json.loads((ART/"report.json").read_text())
    with tempfile.TemporaryDirectory(prefix="rvc-member-allure-") as temp:
        results=Path(temp)/"results";results.mkdir()
        make_results(results,data)
        # 仅复制当前批次结果，固定 UUID 可避免增量生成重复用例。
        shutil.copytree(results,RESULTS,dirs_exist_ok=True)
        for dest,single in [(REPORT,False),(SINGLE,True)]:
            generated=Path(temp)/("single" if single else "report")
            command=["/Users/mac/Downloads/allure-2.35.1/bin/allure","generate",str(results),"-o",str(generated),"--lang","zh","--name","Web UI · 已登录未订阅 UI 测试报告"]
            if single:command.append("--single-file")
            subprocess.run(command,check=True)
            apply_branding(generated,ROOT)
            shutil.copytree(generated,dest,dirs_exist_ok=True)
    print(dict(Counter(r["status"] for r in data["results"].values())))
    print(SINGLE/"index.html")


if __name__ == "__main__":
    main()
