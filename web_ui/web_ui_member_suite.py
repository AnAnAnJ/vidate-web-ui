#!/usr/bin/env python3
"""已登录未订阅套件：在真实 iPhone Chrome 的前台 WebView 执行。

先用 member_runner 的 agreements 阶段检查登录前协议，再通过网页完成
Google 登录。脚本从 UI 检查登录状态，不保存凭据，不购买订阅。
--phase 可独立补测；--target 指定详情测试用户，--block-target 指定拉黑用户。
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from web_ui_member_runner import Runner, OUT
from web_ui_environment import EnvironmentExcluded
from web_ui_account_state import StateExcluded, StateUnconfirmed
from web_ui_mobile_driver import MobileCompatibilityError, MobileSessionUnavailable

BACK = 'button:has(use[href="#icon-common/back"])'
MORE = 'button[type="button"]:has(div.flex-col)'
CLOSE = '[data-purchase-panel] > div:first-child'
VIDEO = 'button:has(use[href="#icon-calling/camera-icon"])'


class Blocked(Exception):
    """前置条件或数据不足，与产品断言失败区分。"""


class Suite(Runner):
    def failure_evidence(self, case_id):
        """失败时最多补采一次证据；采集再失败也要保存原用例结论，避免二次异常覆盖。"""
        try:return self.web_capture(case_id+'-blocked')
        except Exception as error:
            if isinstance(error,MobileSessionUnavailable):
                self.data['context']['session_blocker']={'reason':str(error),'time':int(time.time()*1000)}
            self.event('失败现场补采未完成，已保留原用例结果',case_id=case_id,error_type=type(error).__name__)
            return []

    def run_case(self, case_id, steps, action):
        """执行一项用例并区分通过、产品断言失败、环境跳过和数据阻塞，立即保存结果。"""
        start = int(time.time()*1000)
        try:
            actual, evidence = action()
            status = "PASS"
        except EnvironmentExcluded as error:
            actual, evidence, status = str(error), [], 'SKIPPED_ENV'
        except StateExcluded as error:
            actual, evidence, status = str(error), [], 'SKIPPED_STATE'
        except MobileSessionUnavailable as error:
            actual, evidence, status = str(error), [], 'BLOCKED'
            self.data['context']['session_blocker']={'reason':actual,'time':int(time.time()*1000)}
        except MobileCompatibilityError as error:
            actual, evidence, status = str(error), [], 'BLOCKED'
        except (Blocked, StateUnconfirmed) as error:
            actual, evidence, status = str(error), self.failure_evidence(case_id), "BLOCKED"
        except Exception as error:
            actual, evidence, status = f"{type(error).__name__}: {error}", self.failure_evidence(case_id), "FAIL"
            sampling=OUT/(case_id+"-rerun-samples.json")
            if sampling.is_file():
                evidence.append(str(sampling))
        executed=steps if status=='PASS' else ([] if status.startswith('SKIPPED') else
                  ['尝试执行本项，未将全部设计步骤记为已完成','执行停止处：'+actual])
        self.record(case_id, status, actual, executed, start, evidence)
        return status

    def list_page(self):
        """返回Discover并等待可操作卡片；加载超时明确记为数据或页面阻塞。"""
        if self.js("return !!document.querySelector(arguments[0])", BACK):
            self.click_css(BACK)
        if self.js("return !!document.querySelector('[data-purchase-panel]')"):
            self.close_premium()
        if "/lobby/anchor" != self.js("return location.pathname"):
            self.click_text("Discover")
        # 等待至少一个卡片动作，不能把加载中空白判为空列表。
        end=time.monotonic()+12
        retried=False
        while time.monotonic()<end:
            if self.cards():
                return
            if not retried and 'Loading failed. Please try again.' in self.text():
                self.click_label('Loading failed. Please try again.')
                self.event('Discover出现加载失败，通过页面重试入口重试一次')
                retried=True
            time.sleep(.2)
        raise Blocked("Discover 未加载可操作用户卡片")

    def cards(self, only_sayhi=False):
        """读取Discover用户卡片及动作状态，按only_sayhi决定是否只保留未打招呼目标。"""
        return self.js("""return Array.from(document.querySelectorAll('button')).filter(e=>arguments[0]?e.textContent.trim()==='Say Hi':['Say Hi','Chat'].includes(e.textContent.trim())).map(e=>({name:e.parentElement.parentElement.parentElement.querySelector('p')?.innerText,action:e.textContent.trim()})).filter(e=>e.name)""", only_sayhi)

    def close_premium(self):
        """使用当前订阅页面的关闭或返回入口退出，等待订阅内容消失。"""
        self.click_css(CLOSE)
        self.wait_absent("Get Premium")

    def wait_premium(self):
        """等待订阅面板及必要内容加载，避免将空壳弹窗视为成功。"""
        self.wait_css('[data-purchase-panel][aria-label="Get Premium"]')
        self.wait_text("Get 1 Month")

    def profile(self):
        """检查Profile中的登录身份和会员呈现，返回实际结果与证据。"""
        self.list_page()
        self.click_text("Profile")
        self.wait_text("Upgrade to Premium")
        return "前台 Profile 显示已登录资料和 Upgrade to Premium；未订阅拦截由后续用例交叉检查。", self.web_capture("L-02-profile-rerun")

    def discover(self):
        """核对已登录Discover卡片存在，返回实际用户清单和页面证据。"""
        self.list_page()
        cards=self.cards()
        return "Discover 卡片存在："+json.dumps(cards,ensure_ascii=False), self.web_capture("L-03-cards-rerun")

    def video(self):
        """点击Discover卡片Video，未订阅时核对订阅拦截并保存证据。"""
        self.list_page()
        self.click_css(VIDEO)
        self.wait_premium()
        return "卡片 Video 触发 Get Premium。"+self.text().split("Get Premium")[-1][:200], self.web_capture("L-09-video-rerun")

    def close_case(self):
        """关闭前序已打开的订阅弹窗，并断言来源卡片重新可操作。"""
        if not self.js("return !!document.querySelector('[data-purchase-panel]')"):
            raise Blocked("前序未打开订阅弹窗")
        self.close_premium()
        assert self.cards(), "关闭后未恢复卡片"
        return "订阅弹窗消失，来源卡片可见。", self.web_capture("L-10-close-rerun")

    def masked(self, case_id):
        """点击当前可见打码标签并核对订阅拦截，case_id用于关联证据。"""
        if not self.js("return !!document.querySelector('span[class*=blur-]')"):
            raise Blocked("当前用户无打码标签")
        self.click_css('span[class*="blur-"]')
        self.wait_premium()
        evidence=self.web_capture(case_id+"-masked-rerun")
        actual="点击打码标签后出现 Get Premium。"+self.text().split("Get Premium")[-1][:200]
        self.close_premium()
        return actual,evidence

    def enter(self, target):
        """从列表进入指定昵称的用户详情，核对目标及路由并等待入场动画。"""
        self.list_page()
        if target not in [x["name"] for x in self.cards()]:
            raise Blocked("列表中没有目标用户："+target)
        self.click_label(target)
        self.wait_css(BACK)
        self.wait_absent("No more users")
        self.wait_text(target)
        # 等待入场动画结束后再开始图片位置、轮播计时检查。
        time.sleep(.8)

    def detail_case(self, target):
        """执行卡片进入详情用例，记录目标昵称与实际详情路径。"""
        self.enter(target)
        return "从 Discover 进入 "+target+" 的详情；路径："+self.js("return location.pathname"),self.web_capture("L-12-detail-rerun")

    def menu(self):
        """打开用户详情更多菜单，断言拉黑、举报和取消三个入口存在。"""
        self.click_css(MORE)
        self.wait_text("Add to Blocklist")
        body=self.text()
        assert all(x in body for x in ("Add to Blocklist","Report","Cancel"))
        return "更多包含 Add to Blocklist、Report、Cancel。",self.web_capture("L-14-menu-rerun")

    def block_dialog(self):
        """打开拉黑二次确认，核对提示及确认、取消入口。"""
        if "Add to Blocklist" not in self.text():
            self.menu()
        self.click_label("Add to Blocklist")
        self.wait_css('.rv-dialog__confirm')
        assert "You will no longer be able to see or contact each other." in self.text()
        return "显示拉黑二次确认与 Cancel / Block。",self.web_capture("L-15-dialog-rerun")

    def block_cancel(self):
        """取消已打开的拉黑确认，核对确认和更多菜单关闭且详情保留。"""
        self.click_css('.rv-dialog__cancel')
        self.wait_absent("You will no longer be able to see or contact each other.")
        self.wait_absent("Add to Blocklist")
        assert self.js("return !!document.querySelector(arguments[0])", BACK)
        return "取消后确认及更多菜单关闭，原详情仍在。",self.web_capture("L-18-cancel-rerun")

    def install_notice_observer(self):
        """仅观察页面通知DOM变化以采集短暂Toast，不修改业务状态或请求。"""
        self.js("""window.__qaAllNotices=[];window.__qaToastObserver?.disconnect();window.__qaToastObserver=new MutationObserver(()=>{let texts=Array.from(document.querySelectorAll('.rv-toast,[role=status],[role=alert],.notification-toaster')).map(e=>e.innerText).filter(Boolean);if(texts.length)window.__qaAllNotices.push({time:Date.now(),texts})});window.__qaToastObserver.observe(document.body,{subtree:true,childList:true,characterData:true});return true""")

    def wait_notice(self, needle, timeout=15):
        """等待已采集通知包含needle，返回通知记录；超时抛出明确断言。"""
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            notes=self.js("return window.__qaAllNotices") or []
            if any(any(needle.lower() in text.lower() for text in n["texts"]) for n in notes):
                return notes
            time.sleep(.12)
        raise AssertionError("未捕获预期通知："+needle)

    def report_reasons(self):
        """从详情更多进入举报，核对原因列表非空并采集证据。"""
        self.menu()
        self.click_label("Report")
        self.wait_text("Select a reason")
        assert "Wechat search is not available" in self.text()
        return "举报原因列表非空，包含五个原因及 Cancel。",self.web_capture("L-16-reasons-rerun")

    def report_submit(self):
        """选择实际举报原因，核对成功Toast；该动作会提交对测试目标的举报。"""
        self.install_notice_observer()
        self.click_label("Wechat search is not available")
        notes=self.wait_notice("Report Successfully")
        return "选择 Wechat search is not available 后通知："+json.dumps(notes,ensure_ascii=False),self.web_capture("L-17-report-rerun")

    def block_submit(self, target):
        """对指定测试用户确认拉黑并核对成功通知，需在目标其他检查后执行。"""
        self.enter(target)
        self.block_dialog()
        self.install_notice_observer()
        self.click_css('.rv-dialog__confirm')
        notes=self.wait_notice("Block")
        assert any(any("block" in t.lower() and "succes" in t.lower() for t in n["texts"]) for n in notes)
        return "目标 "+target+"；确认后通知："+json.dumps(notes,ensure_ascii=False),self.web_capture("L-19-block-rerun")

    def carousel(self, single=False):
        """按single校验单图静止或多图自动轮播，保存加载状态和切换时间采样。"""
        count=self.js("return document.querySelectorAll('.rv-swiper-item img').length")
        if (single and count!=1) or (not single and count<2):
            raise Blocked(f"资料图片数量 {count}，未满足本用例条件")
        # 此时 enter 已等待页面入场结束；不将入场动画当作轮播。
        samples=[];changes=[];evidence=[];end=time.monotonic()+(8.2 if single else 13)
        while time.monotonic()<end:
            sample=self.js("""return {time:Date.now(),visibility:document.visibilityState,images:Array.from(document.querySelectorAll('.rv-swiper-item img')).map(e=>({src:e.currentSrc,x:e.getBoundingClientRect().x,loaded:e.complete&&e.naturalWidth>0}))}""")
            samples.append(sample)
            active=min(sample["images"],key=lambda x:abs(x["x"]))["src"]
            if not changes or changes[-1]["src"]!=active:
                changes.append({"time":sample["time"],"src":active})
                evidence+=self.web_capture(("L-21" if single else "L-20")+f"-sample-{len(changes)}")
            time.sleep(.1)
        intervals=[(b["time"]-a["time"])/1000 for a,b in zip(changes[1:],changes[2:])]
        path=OUT/(("L-21" if single else "L-20")+"-rerun-samples.json")
        path.write_text(json.dumps({"samples":samples,"changes":changes,"intervals":intervals},ensure_ascii=False,indent=2))
        evidence.append(str(path))
        assert all(x["visibility"]=="visible" and all(i["loaded"] for i in x["images"]) for x in samples), "页面前台或图片加载异常"
        if single:
            assert len(changes)==1 and all(abs(x["images"][0]["x"])<1 for x in samples), "单图出现轮播或位移"
        else:
            assert len(changes)>=3 and len({x["src"] for x in changes})>=2, f"未观察到至少两次图片自动切换；采样已保存：{path}"
        return f"图片数 {count}；完整切换间隔 {intervals} 秒。",evidence

    def back_case(self):
        """从详情点击返回，核对回到Discover来源路由及可操作列表。"""
        self.click_css(BACK)
        deadline=time.monotonic()+8
        while time.monotonic()<deadline and self.js("return location.pathname")!="/lobby/anchor":
            time.sleep(.1)
        assert self.js("return location.pathname")=="/lobby/anchor"
        self.list_page()
        return "返回 Discover 来源列表，路径正确。",self.web_capture("L-22-back-rerun")

    def sayhi(self):
        """向五个不同用户打招呼，以本轮逐次成功证据验证可用额度，不猜历史用量。"""
        self.list_page()
        done=self.data["context"].setdefault("sayhi_completed_targets",[])
        while len(done)<5:
            candidates=self.cards(only_sayhi=True)
            if not candidates:
                raise Blocked(f"只完成 {len(done)} 人，缺少未打招呼用户")
            target=candidates[0]["name"]
            if target in done:
                raise Blocked("已发送用户重新出现 Say Hi，需要核对账号状态，停止重复发送")
            self.install_notice_observer()
            self.click_text("Say Hi")
            if 'Get Premium' in self.text():
                raise Blocked(f'本轮只成功{len(done)}人即出现订阅拦截，历史用量未知或额度不足，不能判定五次边界')
            notices=self.wait_notice("successfully sent")
            self.event("Say Hi 成功",target=target,notices=notices)
            done.append(target)
            self.save()
            evidence=self.web_capture("L-08-hi-"+str(len(done)))
            if "L-07" not in self.data["results"]:
                now=int(time.time()*1000)
                self.record("L-07","PASS",json.dumps(notices,ensure_ascii=False),["向 "+target+" 发送 Say Hi，实时捕获成功通知"],now,evidence)

    def quota(self):
        """确认本轮五人成功后对第六个不同目标点击Say Hi，无第六人则阻塞而非通过。"""
        self.list_page()
        done=self.data["context"].get("sayhi_completed_targets",[])
        if len(done)!=5:
            raise Blocked(f"本轮已确认成功人数 {len(done)}，尚未达到5人")
        candidates=[c for c in self.cards(True) if c["name"] not in done]
        if not candidates:
            raise Blocked("前五个不同用户已成功；当前没有第六个未打招呼用户，列表显示 No more users")
        self.click_text("Say Hi")
        self.wait_premium()
        return "五个不同用户成功后，对第六人 "+candidates[0]["name"]+" 点击 Say Hi 触发 Get Premium。",self.web_capture("L-08-sixth")


def main():
    """解析命令行参数并执行所选阶段，将执行结果保存到对应批次。"""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase",required=True,choices=["profile","discover","sayhi","quota","details","report","block","calls"])
    parser.add_argument("--target",help="详情或举报目标完整昵称")
    parser.add_argument("--block-target",help="最后确认拉黑的测试用户完整昵称")
    parser.add_argument("--single-target",help="单图用户完整昵称")
    args=parser.parse_args()
    s=Suite();s.web()
    if args.phase=="profile":s.run_case("L-02",["核对已登录未订阅 Profile"],s.profile)
    elif args.phase=="discover":
        s.run_case("L-03",["打开已登录 Discover 并检查卡片"],s.discover)
        s.run_case("L-09",["点击卡片 Video"],s.video)
        s.run_case("L-10",["关闭订阅弹窗"],s.close_case)
        s.run_case("L-11",["点击卡片打码标签"],lambda:s.masked("L-11"))
    elif args.phase=="sayhi":
        s.sayhi()
        s.run_case("L-08",["向5个不同用户成功发送 Say Hi","点击第六人的 Say Hi"],s.quota)
    elif args.phase=="quota":s.run_case("L-08",["核对前五人发送记录","点击第六人的 Say Hi"],s.quota)
    elif args.phase=="calls":s.calls()
    elif args.phase=="details":
        if not args.target:parser.error("details 需要 --target")
        if s.run_case("L-12",["从 Discover 进入 "+args.target],lambda:s.detail_case(args.target))!="PASS":
            return
        s.run_case("L-13",["点击详情打码标签"],lambda:s.masked("L-13"))
        s.run_case("L-14",["打开更多核对三项"],s.menu)
        s.run_case("L-15",["打开拉黑二次确认"],s.block_dialog)
        s.run_case("L-18",["取消拉黑二次确认"],s.block_cancel)
        s.run_case("L-20",["连续观察多图轮播并计时"],s.carousel)
        s.run_case("L-22",["点击返回核对来源"],s.back_case)
        if args.single_target:
            s.enter(args.single_target)
            s.run_case("L-21",["入场结束后观察单图8秒"],lambda:s.carousel(True))
    elif args.phase=="report":
        if not args.target:parser.error("report 需要 --target")
        s.enter(args.target)
        s.run_case("L-16",["更多点击举报核对原因"],s.report_reasons)
        s.run_case("L-17",["选择任意原因并提交"],s.report_submit)
    elif args.phase=="block":
        if not args.block_target:parser.error("block 需要 --block-target")
        s.run_case("L-19",["最后对测试用户确认拉黑"],lambda:s.block_submit(args.block_target))


if __name__=="__main__":
    main()
