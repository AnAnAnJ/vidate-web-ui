/** 桌面Chrome适配：由CUA或独立入口传入标签，复用共享业务定义，不操作手机驱动。 */
import fs from 'node:fs/promises';
import path from 'node:path';
import {randomInt} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {classifyDesktopLogin,incomingControls,checkCallBilling} from './web_ui_observation.mjs';

const root=path.dirname(fileURLToPath(import.meta.url));
const unsubscribed=new Set(['L-08','L-09','L-10','L-11','L-13','L-27','L-28','L-29','L-30','N-10','S-01']);
const paidVideo=new Set(['S-04','S-05','S-06','S-09']);
const active=new Set(['S-02','S-03','S-04','S-05','S-06','S-07','S-09']);
export class Blocked extends Error {}
export class Skipped extends Error {}

/** 仅按Profile实际卡片和有效期识别会员，缺失或冲突均不猜测。 */
export function membership(text){
  const premium=text.includes('Premium Member'),upgrade=text.includes('Upgrade to Premium');
  const expiry=text.match(/Active until\s*:?\s*(\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}(?:[ T]+\d{1,2}:\d{2}(?::\d{2})?)?)/)?.[1]||'';
  return {state:premium&&expiry&&!upgrade?'subscribed':upgrade&&!premium?'unsubscribed':'unknown',active_until:expiry,checked_at:Date.now()/1000};
}

export class DesktopSuite {
  /** 绑定真实Chrome标签及独立Web批次，不连接Appium，不改变移动端配置。 */
  constructor({tab,browser,batch,url,environment='test',permissions=null,site=null}){
    if(!['test','prod'].includes(environment))throw new Error('环境必须明确为test或prod');
    this.tab=tab;this.browser=browser;this.batch=batch;this.url=new URL(url).origin+'/';this.environment=environment;
    this.steps=[];this.catalog=new Map();this.permissions=permissions;this.site=site||tab.webui;
  }
  /** 初始化或恢复同网站、同平台记录，阻止把Web结果混入手机批次。 */
  async init(){
    await fs.mkdir(this.batch,{recursive:true});
    const cases=JSON.parse(await fs.readFile(path.join(root,'web_ui_browser_cases.json'),'utf8'));
    const config=JSON.parse(await fs.readFile(path.join(root,'web_ui_config.json'),'utf8'));
    this.disabledCases=config.disabled_cases||{};
    this.webRules=config.platform_rules?.web||{};
    this.excluded=new Set(this.webRules.excluded_case_ids||[]);
    const overrides=config.platform_rules?.web?.case_overrides||{};
    this.catalog=new Map(cases.filter(c=>!this.excluded.has(c[0])&&!this.disabledCases[c[0]]).map(c=>[c[0],[c[0],c[1],overrides[c[0]]?.title||c[2],overrides[c[0]]?.steps||c[3],overrides[c[0]]?.expected||c[4]]]));
    try{this.data=JSON.parse(await fs.readFile(path.join(this.batch,'report.json'),'utf8'));}
    catch(e){if(e.code!=='ENOENT')throw e;this.data={url:this.url,device:{platform:'web',name:'Desktop Chrome',version:'实际Chrome',udid:'desktop-chrome'},environment:'桌面普通Chrome',context:{run_environment:this.environment,incremental_without_reset:false},results:{},events:[]};}
    if(this.data.url!==this.url||this.data.device.platform!=='web'||this.data.context.run_environment!==this.environment)throw new Error('不同网站、环境或平台必须独立批次');
    this.data.context.excluded_case_ids=[...this.excluded];
    await this.save();return this;
  }
  /** 原子写入结果，进程中断也保留完整JSON。 */
  async save(){const file=path.join(this.batch,'report.json');await fs.writeFile(file+'.tmp',JSON.stringify(this.data,null,2));await fs.rename(file+'.tmp',file);}
  /** 写入带时间的执行事件，不记录密码或验证代码。 */
  async event(message,fields={}){this.data.events.push({time:Date.now(),message,...fields});await this.save();}
  /** 读取可见正文，作为桌面业务断言依据。 */
  async text(){return this.tab.playwright.locator('body').innerText({timeoutMs:8000});}
  /** 通过可访问名称定位桌面控件，不使用手机坐标。 */
  button(name){return this.tab.playwright.getByRole('button',{name,exact:true});}
  /** 确认可见后点击并记录真实执行步骤。 */
  async click(name){const b=this.button(name).filter({visible:true});if(await b.count())await b.first().click({timeoutMs:5000});else await this.tab.playwright.getByText(name,{exact:true}).filter({visible:true}).first().click({timeoutMs:5000});this.steps.push('点击 '+name);}
  /** 仅点击真实可见CSS节点，选择器沿用既有网页实现。 */
  async css(selector){await this.tab.playwright.locator(selector).filter({visible:true}).first().click({timeoutMs:5000});this.steps.push('点击控件 '+selector);}
  /** 等待语义文案，超时保留数据阻塞而不是用固定长等待。 */
  async wait(text,timeoutMs=10000){
    const deadline=Date.now()+timeoutMs;
    while(true){
      try{return await this.tab.playwright.getByText(text,{exact:false}).first().waitFor({state:'visible',timeoutMs:Math.max(1,deadline-Date.now())});}
      catch(error){
        // CUA单次选择器调用可能先于业务等待时限结束；只延续只读等待，不重复业务动作。
        if(Date.now()>=deadline||!/timeout|timed out|deadline/i.test(error.message))throw error;
        await this.tab.playwright.domSnapshot();
      }
    }
  }
  /** 保存桌面可见截图和正文，逐项保留证据。 */
  async capture(label){const stem=path.join(this.batch,label+'-'+Date.now());await fs.writeFile(stem+'.png',await this.tab.screenshot({fullPage:false}));await fs.writeFile(stem+'.txt',await this.text());return [stem+'.png',stem+'.txt'];}
  /** 统一记录设计步骤、真实操作、预期和实际，保留旧结果审计。 */
  async record(id,status,actual,start,evidence=[]){
    const c=this.catalog.get(id);if(!c)throw new Error('未知用例 '+id);
    if(this.data.results[id])this.data.events.push({time:Date.now(),message:'补测保留前次结果',previous_result:this.data.results[id]});
    this.data.results[id]={case_id:id,module:c[1],title:c[2],planned_steps:c[3],expected:c[4],executed_steps:[...this.steps],actual,status,start,stop:Date.now(),evidence};await this.save();return {id,status,actual};
  }
  /** 检查真实Chrome域名和环境，在每次业务动作前保留身份前置。 */
  async guard(id){
    if(this.disabledCases?.[id])throw new Skipped(id+'已在所有端停用：'+this.disabledCases[id]);
    if(this.excluded?.has(id))throw new Skipped('PC按当前范围移除'+id+'；Android/iOS继续保留');
    if(new URL(await this.tab.url()).origin!==new URL(this.url).origin)throw new Blocked('前台Chrome不是本批被测域名');
    if(this.environment==='prod'&&(id==='S-01'||id==='S-03'||paidVideo.has(id)))throw new Skipped('prod按规则跳过支付及已订阅Video');
    if(id.startsWith('S-')){const selected=this.data.context.selected_case_ids;const missing=[...this.catalog.keys()].filter(k=>(!selected||selected.includes(k))&&!k.startsWith('S-')&&!this.data.results[k]);if(missing.length)throw new Blocked('尚未处理未标记项：'+missing.join(','));}
    if(unsubscribed.has(id)||active.has(id)){
      if(['L-10','L-28'].includes(id)){
        const cached=this.data.context.membership;if(!cached||Date.now()/1000-cached.checked_at>30)throw new Blocked('关闭弹窗缺少30秒内会员前置证据，保留现场不导航');
        if(cached.state!=='unsubscribed')throw new Skipped('关闭未订阅弹窗要求未订阅');return;
      }
      const current=await this.profile(true,true),expected=unsubscribed.has(id)?'unsubscribed':'subscribed';
      if(current.state==='unknown')throw new Blocked('Profile会员状态未知');
      if(current.state!==expected){if(this.environment==='prod'||expected==='unsubscribed')throw new Skipped('实际'+current.state+'，本项要求'+expected);throw new Blocked('会员已失效；首次支付核验后须使用当前UUID后台恢复864000秒，不重复支付');}
    }
  }
  /** 运行一项，区分断言失败、前置阻塞及身份跳过；失败最多补采一次。 */
  async run(id,action){
    const start=Date.now();this.steps=[];let status='PASS',actual,evidence=[];
    try{await this.guard(id);actual=await action();}
    catch(e){status=e instanceof Skipped?(this.environment==='prod'&&/prod/.test(e.message)?'SKIPPED_ENV':'SKIPPED_STATE'):e instanceof Blocked||/selector deadline|CDP command|locator\.|strict mode violation|networkidle/.test(e.message)?'BLOCKED':'FAIL';actual=e.message;}
    const nested=this.data.results[id];if(nested?.start>=start){evidence.push(...nested.evidence);actual=actual+'；'+nested.actual;}
    if(id==='S-05')evidence.push(...(this.data.context.zero_balance_evidence||[]));
    if(['N-10','S-07'].includes(id))evidence.push(...(this.data.context.gift_evidence||[]));
    if(id==='G-12')evidence.push(...(this.data.context.permission_evidence||[]));
    if(['S-04','S-05','S-06'].includes(id))evidence.push(...(this.data.context.subscribed_video_evidence?.[id]||[]));
    if(id==='L-25'||id==='L-26')evidence.push(...(this.data.context.tag_save_evidence?.[id]||[]));
    if(id==='S-04')evidence.push(...(this.data.context.first_video_cache?.evidence||[]));
    if(['S-05','S-09'].includes(id))evidence.push(...(this.data.context.call_billing_attempts||[]).filter(item=>item.started_at>=start).flatMap(item=>item.evidence));
    try{evidence.push(...await this.capture(id));}catch(e){this.data.events.push({time:Date.now(),message:'证据补采失败',case_id:id,error:e.message});}
    return this.record(id,status,actual,start,evidence);
  }
  /** 关闭登录弹窗，只使用已存在的关闭按钮，不隐藏DOM。 */
  async closeLogin(){if((await this.text()).includes('Continue with Google')){await this.tab.playwright.getByRole('dialog',{name:'Login',exact:true}).getByRole('button').last().click();this.steps.push('关闭Login弹窗');}}
  /** 桌面导航使用真实顶栏控件；游客Profile映射到桌面登录入口并记录差异。 */
  async nav(name){
    if((await this.text()).includes('Get Premium'))await this.closePremium();
    if(name==='Profile'&&!(await this.button('Profile').count())&&await this.button('Log in').count()){await this.click('Log in');this.steps.push('桌面游客无Profile按钮，使用顶栏Log in入口');return;}
    if(name==='Profile'&&!(await this.button('Profile').count())){await this.tab.playwright.getByRole('banner').getByRole('button').last().click();this.steps.push('桌面右上角头像进入Profile');return;}
    if(!await this.tab.playwright.getByRole('navigation').count()&&['Discover','Match'].includes(name)){await this.tab.goto(this.url+'lobby/'+(name==='Discover'?'anchor':'match'));this.steps.push('桌面页面返回'+name);return;}
    const nav=this.tab.playwright.getByRole('navigation').getByRole('button',{name,exact:true});
    if(await nav.count()){await nav.first().click();this.steps.push('导航 '+name);}else await this.click(name);
  }
  /** 返回Discover等待真实卡片，保留已消耗Say Hi用量。 */
  async discover(refresh=false){
    if(new URL(await this.tab.url()).pathname!=='/lobby/anchor')await this.nav('Discover');
    if(refresh){await this.tab.reload();this.steps.push('刷新Discover并等待列表');}
    const ready=this.tab.playwright.getByRole('button',{name:/^(Say Hi|Chat)$/}).first();
    try{await ready.waitFor({state:'visible',timeoutMs:8000});}catch(e){const retry=this.button('Loading failed. Please try again.');if(await retry.count()){await retry.click();this.steps.push('Discover加载失败后点击页面重试一次');}try{await ready.waitFor({state:'visible',timeoutMs:5000});}catch{throw new Blocked('Discover无可操作卡片：'+(await this.text()).slice(-350));}}
  }
  /** 读取用户卡片的可见结构，供Say Hi和详情使用同一目标。 */
  async cards(){return this.tab.playwright.evaluate(()=>Array.from(document.querySelectorAll('button')).filter(e=>['Say Hi','Chat'].includes(e.textContent.trim())).map(e=>{const card=e.parentElement.parentElement.parentElement;return {name:card.querySelector('.text-sm.font-semibold')?.innerText||card.querySelector('p')?.innerText,action:e.textContent.trim()};}).filter(e=>e.name));}
  /** 核对Discover选中态，不能以标签存在替代切换成功。 */
  async tabs(){await this.discover();const seen=[];for(const label of ['Video','Text','All']){const tab=this.tab.playwright.getByRole('tab',{name:label,exact:true});await tab.click();if(await tab.getAttribute('aria-selected')!=='true')throw new Error(label+'未选中');seen.push(label);}return '依次切换且aria-selected=true：'+seen.join('、');}
  /** 连续五次真实键盘滚动并记录可滚动容器位置。 */
  async scrollFive(){await this.discover();const samples=[];for(let i=0;i<5;i++){const target=await this.tab.playwright.evaluate(()=>{const el=Array.from(document.querySelectorAll('*')).find(e=>e.scrollHeight>e.clientHeight+20&&e.clientHeight>200)||document.body;const r=el.getBoundingClientRect();return [Math.round(r.x+r.width/2),Math.round(Math.min(r.y+r.height*.65,document.documentElement.clientHeight-80))];});if(!this.tab.scroll)throw new Blocked('桌面滚动需要CUA Tab绑定');await this.tab.scroll(target,'down',1);samples.push(await this.tab.playwright.evaluate(()=>Array.from(document.querySelectorAll('*')).filter(e=>e.scrollHeight>e.clientHeight+20&&e.clientHeight>200).map(e=>({tag:e.tagName,top:e.scrollTop,height:e.scrollHeight}))));}this.steps.push('连续真实滚轮下滑五次，记录滚动位置');if(!(await this.cards()).length)throw new Error('滚动后卡片消失');return '五次滚动后列表可操作；位置='+JSON.stringify(samples);}
  /** 游客点击卡片动作，确认登录弹窗而不是登录账号。 */
  async guestAction(kind){await this.discover();await this.button(kind).first().click();this.steps.push('游客点击 '+kind);await this.wait('Continue with Google');return '出现登录弹窗及Continue with Google';}
  /** 切换Match三个偏好，按桌面pressed态逐项断言。 */
  async matchTabs(){await this.nav('Match');for(const label of ['men','All','women']){await this.click(label);if(await this.button(label).getAttribute('aria-pressed')!=='true')throw new Error(label+'未选中');}return 'men、All、women均切换成功，无页面错误';}
  /** PC依次完成可见权限说明，点击Enjoy后等待引导关闭；不将关闭引导当成已授权。 */
  async completePermissionGuide(){
    const visible=name=>this.button(name).filter({visible:true});
    const body=await this.text();
    if(body.includes('How Can I Provide Permission?'))await this.click('How Can I Provide Permission?');
    else if(!body.includes('Important Tips')&&!await visible('Enjoy').count())throw new Blocked('当前没有PC权限说明引导，不能追溯Enjoy操作');
    for(let i=0;i<3;i++){
      if(await visible('Enjoy').count()){
        await this.click('Enjoy');
        await this.tab.playwright.getByText('Important Tips',{exact:true}).first().waitFor({state:'hidden',timeoutMs:5000});
        await this.tab.playwright.getByText('Start matching',{exact:true}).filter({visible:true}).first().waitFor({state:'visible',timeoutMs:5000});
        return 'PC点击Enjoy后权限说明关闭，Match恢复可操作；实际音视频授权由匹配用例独立核验';
      }
      if(!await visible('Next').count())throw new Blocked('PC权限说明没有可见Next或Enjoy，保留现场');
      await this.click('Next');
    }
    throw new Blocked('PC权限说明未在限定步骤内出现Enjoy，保留现场');
  }
  /** 读取免费次数；没有真实数字不能推测为可匹配。 */
  async remaining(){await this.tab.playwright.getByText(/\d+\s*free matches left/i).waitFor({state:'visible',timeoutMs:8000});const match=(await this.text()).match(/(\d+)\s*free matches left/i);if(!match)throw new Blocked('页面未显示可用免费匹配次数');return Number(match[1]);}
  /** PC进入匹配连接页即作为本次音视频已授权的业务证据，通知和礼物结果各自记录。 */
  async matchingPermissionEvidence(caseId){
    const body=await this.text();
    const media=await this.tab.playwright.locator('video').evaluateAll(es=>es.filter(e=>e.getBoundingClientRect().height>20&&e.readyState>=2&&e.videoWidth>0).length);
    const inCall=media>0&&await this.tab.playwright.getByRole('textbox',{name:'Send a message',exact:true}).count()>0;
    if(!/Call connecting|Looking for|Searching|Connecting/i.test(body)&&!await this.tab.playwright.getByText('Exit',{exact:true}).count()&&!inCall)throw new Blocked('未进入匹配连接页或视频会话，不能证明当前权限已授权');
    if(/Important Tips|How Can I Provide Permission\?|Continue with Google/.test(body))throw new Blocked('存在权限或登录阻塞弹窗，不能按后台连接页判授权');
    const evidence=await this.capture(caseId+'-permission-authorized');
    const state={state:'authorized',basis:'实际进入匹配连接页',checked_at:Date.now(),source_case_id:caseId,evidence};
    this.data.context.permissions=state;this.steps.push('实际进入匹配连接页，确认PC摄像头及麦克风权限已授权');await this.save();return state;
  }
  /** PC以一次实际匹配确认授权，保存证据后退出，不操作浏览器权限或要求通知。 */
  async authorizedMatching(){
    await this.nav('Match');const remaining=await this.remaining();if(!remaining)throw new Blocked('没有匹配次数，无法通过实际匹配确认授权');
    await this.click('Start matching');
    try{
      const end=Date.now()+5000;
      while(Date.now()<end){
        const body=await this.text();
        if(/Important Tips|How Can I Provide Permission\?|Continue with Google/.test(body))throw new Blocked('权限或登录弹窗阻塞匹配，不能判已授权');
        if(/Searching|Connecting|Call connecting/.test(body)||await this.tab.playwright.getByText('Exit',{exact:true}).count()||await this.tab.playwright.getByRole('textbox',{name:'Send a message',exact:true}).count()){
          const state=await this.matchingPermissionEvidence('N-03');return '实际进入匹配连接页或视频会话，音视频权限已授权；证据='+state.evidence.join('、');
        }
      }
      throw new Blocked('5秒内没有进入实际匹配连接页，不能判权限已授权');
    }finally{await this.tab.goto(this.url+'lobby/anchor');this.steps.push('保存证据后离开Match停止自动续配');}
  }
  /** 单次启动匹配并退出，核对扣次；游客最多8次。 */
  async matchingExit(prefix='M'){
    await this.nav('Match');const before=await this.remaining();if(!before)throw new Blocked('免费匹配次数为0');
    const key=prefix==='M'?'guest_matching_starts':'member_matching_starts';if((this.data.context[key]||0)>=8)throw new Blocked('已达到8次匹配上限');
    await this.click('Start matching');this.data.context[key]=(this.data.context[key]||0)+1;await this.save();
    await this.wait('Exit',10000);const started='启动前'+before+'次，进入含Exit的连接页';
    await this.matchingPermissionEvidence(prefix==='M'?'M-12':'N-07');
    const first=prefix==='M'?'M-11':'N-06';await this.record(first,'PASS',started,Date.now(),await this.capture(first));
    await this.click('Exit');await this.wait('Start matching');await this.tab.playwright.getByText((before-1)+' Free matches left',{exact:true}).waitFor({state:'visible',timeoutMs:8000});const after=await this.remaining();if(after!==before-1)throw new Error('匹配次数'+before+'→'+after+'，预期减1');await this.nav('Discover');return '退出连接页并等待异步次数更新，免费次数'+before+'→'+after;
  }
  /** 刷新Profile等待真实会员/余额稳定，restore时回到原来源页面。 */
  async profile(refresh=true,restore=false){
    const before=await this.tab.url();if(!String(before).includes('/lobby/profile'))await this.tab.goto(this.url+'lobby/profile');else if(refresh)await this.tab.reload();
    await this.wait('Gems Store',12000);
    if(this.data.context.member_identity)await this.tab.playwright.getByText(this.data.context.member_identity,{exact:true}).waitFor({state:'visible',timeoutMs:12000});
    else{const name=this.tab.playwright.getByRole('paragraph').first().locator('span',{});await name.waitFor({state:'visible',timeoutMs:12000});const identity=(await name.innerText()).trim();if(!identity)throw new Blocked('Profile昵称尚未加载');this.data.context.member_identity=identity;}
    const body=await this.text();
    const state=membership(body);this.data.context.membership=state;await this.save();this.steps.push('刷新Profile核对实际会员：'+state.state);
    if(restore&&before!==await this.tab.url())await this.tab.goto(before);return state;
  }
  /** 读取唯一金币余额，避免把未加载占位值作为到账结果。 */
  async balance(){const text=await this.tab.playwright.getByRole('button',{name:/Gems Store/}).innerText();const numbers=text.match(/\d[\d,]*/g)||[];if(numbers.length!==1)throw new Blocked('金币格式不能唯一识别：'+text);return Number(numbers[0].replaceAll(',',''));}
  /** 打开及确认订阅面板，不提交支付。 */
  async premium(){await this.wait('Get Premium');await this.tab.playwright.locator('[data-premium-content]').waitFor({state:'visible',timeoutMs:8000});return '未订阅动作触发Get Premium订阅面板';}
  /** 使用真实关闭入口退出订阅面板。 */
  async closePremium(){const close=this.tab.playwright.locator('div.cursor-pointer:has(>svg>use[href="#icon-common/close"])').filter({visible:true});if(await close.count())await close.first().click();else await this.tab.playwright.getByRole('banner').locator('button:has(use[href="#icon-common/back"])',{}).click();this.steps.push('点击订阅真实关闭或顶栏返回');await this.tab.playwright.getByText('Get Premium',{exact:true}).first().waitFor({state:'hidden',timeoutMs:5000});return '订阅面板关闭，恢复来源页';}
  /** 成功通知需在点击后立即观察，缺失不通过再次操作补提示。 */
  async notice(pattern,timeoutMs=8000){const end=Date.now()+timeoutMs;while(Date.now()<end){const found=(await this.text()).split('\n').find(t=>pattern.test(t));if(found)return found;}throw new Error('未捕获成功通知 '+pattern);}
  /** 独立执行一次Say Hi并即时采集通知，不为单项测试额外消耗五次额度。 */
  async sayHiOnce(){
    await this.discover();const done=this.data.context.sayhi_completed_targets||=[];
    const target=(await this.cards()).find(c=>c.action==='Say Hi'&&!done.includes(c.name));
    if(!target)throw new Blocked('没有尚未打招呼的用户');
    const card=this.tab.playwright.getByText(target.name,{exact:true}).locator('xpath=..',{});
    await card.getByRole('button',{name:'Say Hi',exact:true}).click();this.steps.push('向'+target.name+'发送Say Hi');
    const note=await this.notice(/successfully sent/i);const evidence=await this.capture('L-07-notice');
    await card.getByRole('button',{name:'Chat',exact:true}).waitFor({state:'visible',timeoutMs:12000});
    done.push(target.name);this.data.context.sayhi_completed_targets=done;await this.save();
    await this.record('L-07','PASS',target.name+'：'+note,Date.now(),evidence);return target.name+'：'+note;
  }
  /** 连续五名Say Hi及刷新后的第六名边界，缺数据明确跳过。 */
  async sayHi(){
    await this.discover();const done=this.data.context.sayhi_completed_targets||=[];
    while(done.length<5){const target=(await this.cards()).find(c=>c.action==='Say Hi'&&!done.includes(c.name));if(!target)throw new Blocked('本轮仅成功'+done.length+'人，无更多未打招呼用户');
      const locator=this.tab.playwright.getByText(target.name,{exact:true});const card=locator.locator('xpath=..',{});await card.getByRole('button',{name:'Say Hi',exact:true}).click();this.steps.push('向'+target.name+'发送Say Hi');
      await card.getByRole('button',{name:'Chat',exact:true}).waitFor({state:'visible',timeoutMs:12000});done.push(target.name);this.data.context.sayhi_completed_targets=done;await this.save();
      if(!this.data.results['L-07']){const note=await this.notice(/successfully sent/i);await this.record('L-07','PASS',note,Date.now(),await this.capture('L-07'));}
    }
    await this.discover(true);const sixth=(await this.cards()).find(c=>c.action==='Say Hi'&&!done.includes(c.name));if(!sixth)throw new Skipped('五名成功，刷新后没有第六个不同用户，按用户规则跳过');
    await this.tab.playwright.getByText(sixth.name,{exact:true}).locator('xpath=..',{}).getByRole('button',{name:'Say Hi',exact:true}).click();await this.premium();return '前五名成功，第六名'+sixth.name+'触发订阅';
  }
  /** 进入指定卡片详情并核对返回按钮及目标身份。 */
  async detail(target=null){await this.discover();target||=(await this.cards())[0]?.name;if(!target)throw new Blocked('没有用户详情数据');await this.tab.playwright.getByText(target,{exact:true}).first().click();this.steps.push('打开'+target+'详情');await this.tab.playwright.locator('button:has(use[href="#icon-common/back"])').waitFor({state:'visible',timeoutMs:8000});await this.wait(target);this.data.context.detail_target=target;return '进入'+target+'详情，网址='+await this.tab.url();}
  /** 打开更多菜单并核对三项操作。 */
  async menu(){if(!(await this.text()).includes('Add to Blocklist'))await this.css('button[type="button"]:has(div.flex-col)');for(const label of ['Add to Blocklist','Report'])await this.wait(label);return '更多已打开，包含Add to Blocklist、Report';}
  /** 拉黑二次确认不等于提交，提交放在目标其他检查之后。 */
  async blockDialog(){if(!(await this.text()).includes('Add to Blocklist'))await this.menu();await this.click('Add to Blocklist');await this.wait('You will no longer be able to see or contact each other.');return '出现拉黑二次确认，包含Block和Cancel';}
  /** 取消拉黑并断言详情仍保留。 */
  async blockCancel(){await this.css('.rv-dialog__cancel');await this.wait(this.data.context.detail_target);return '取消二次确认，仍保留原用户详情';}
  /** 查看举报原因，只有真实原因非空才通过。 */
  async reportReasons(){await this.menu();await this.click('Report');await this.wait('Select a reason');await this.wait('Wechat search is not available');return '举报原因列表非空';}
  /** 提交已授权的测试目标举报并捕获成功提示。 */
  async reportSubmit(){await this.click('Wechat search is not available');return '所选原因Wechat search is not available；'+await this.notice(/Report Successfully/i);}
  /** 最后确认拉黑并捕获成功提示。 */
  async blockSubmit(){await this.blockDialog();await this.css('.rv-dialog__confirm');return '测试目标'+this.data.context.detail_target+'；'+await this.notice(/block.*succes|succes.*block/i);}
  /** 读取真实图片位置，只观察自动切换，不手动滑动。 */
  async carousel(single=false){
    const grid=this.tab.playwright.locator('main img.cursor-pointer');
    if(await grid.count()){
      const sample=()=>grid.evaluateAll(es=>es.map(e=>({src:e.currentSrc,x:e.getBoundingClientRect().x,width:e.getBoundingClientRect().width,loaded:e.complete&&e.naturalWidth>0})));
      const initial=await sample();if(single?initial.length!==1:initial.length<2)throw new Blocked('桌面照片数'+initial.length+'不满足单图/多图前置');
      const samples=[],end=Date.now()+8100;while(Date.now()<end){const images=await sample();if(images.some(i=>!i.loaded))throw new Error('照片未加载或空白');samples.push({time:Date.now(),images});await this.tab.playwright.domSnapshot();}
      await fs.writeFile(path.join(this.batch,(single?'L-21':'L-20')+'-desktop-grid.json'),JSON.stringify(samples,null,2));this.steps.push('桌面详情'+initial.length+'张照片，连续观察8秒');
      const unchanged=samples.every(s=>JSON.stringify(s.images)===JSON.stringify(initial));if(single&&!unchanged)throw new Error('单图发生变化');if(!single&&unchanged)throw new Error('桌面照片并排网格展示，8秒没有自动轮播，与iOS预期不一致');return '单图稳定，无跳动或空白';
    }
    const sample=()=>this.tab.playwright.evaluate(()=>Array.from(document.querySelectorAll('.rv-swiper-item img')).map(e=>({src:e.currentSrc,x:e.getBoundingClientRect().x,loaded:e.complete&&e.naturalWidth>0})));
    const initial=await sample();if(single?initial.length!==1:initial.length<2)throw new Blocked('当前图片数'+initial.length+'，不满足'+(single?'单图':'多图')+'前置');
    const changes=[],samples=[],end=Date.now()+(single?8100:11000);
    while(Date.now()<end){const images=await sample();if(!images.length||images.some(i=>!i.loaded))throw new Error('图片未加载或出现空白');const active=[...images].sort((a,b)=>Math.abs(a.x)-Math.abs(b.x))[0].src;samples.push(images);if(changes.at(-1)?.src!==active)changes.push({time:Date.now(),src:active});await this.tab.playwright.domSnapshot();}
    if(single?changes.length!==1:changes.length<3)throw new Error('自动切换次数不符：'+changes.length);await fs.writeFile(path.join(this.batch,(single?'L-21':'L-20')+'-samples.json'),JSON.stringify({samples,changes},null,2));return '图片'+initial.length+'张，实际自动变化'+(changes.length-1)+'次';
  }
  /** 详情按原返回按钮退出，验证Discover来源路由。 */
  async back(){await this.css('button:has(use[href="#icon-common/back"])');if(!String(await this.tab.url()).includes('/lobby/anchor'))throw new Error('未返回Discover');return '返回Discover来源页';}
  /** 随机英文昵称和签名使用桌面真实输入，保存后刷新核对。 */
  async edit(kind){
    await this.profile();let value;
    if(kind==='nickname'){await this.click('Edit Info');value=['Ethan','Oliver','Lucas'][randomInt(3)]+' '+['Reed','Brooks','Lane'][randomInt(3)];await this.tab.playwright.getByRole('textbox',{name:'Please enter 1-20 characters',exact:true}).fill(value);}
    else{await this.click('Edit');value=['Keep growing, one day at a time.','Dream big and keep moving.','Every small step brings progress.'][randomInt(3)];await this.tab.playwright.locator('[contenteditable=true]').fill(value);}
    this.steps.push('填写随机英文'+kind+'：'+value);await this.click('Save');await this.wait(value);if(kind==='nickname')this.data.context.member_identity=value;await this.profile(true);await this.wait(value);return '保存返回和刷新Profile均回显 '+value;
  }
  /** 选择及取消标签串行保存；逐次等待真实请求、核对界面并留出1.5秒间隔。 */
  async tags(cancel=false){
    if(!this.site?.profileSave)throw new Blocked('缺少标签保存请求观察适配器，不以高亮替代接口完成');
    await this.profile();const tag=label=>this.tab.playwright.getByRole('button',{name:label+' '+label,exact:true});const state=label=>tag(label).evaluate(e=>({html:e.outerHTML,count:document.body.innerText.match(/(\d)\/6/)?.[1]}));
    let labels=this.data.context.selected_tags_this_run||[];
    if(!cancel){labels=[];for(const label of ['Latex','Flirting','Blindfolding','Biting']){if(!((await state(label)).html.includes('border-[#FF66D6]')))labels.push(label);if(labels.length===2)break;}if(labels.length<2)throw new Blocked('缺少两个未选标签');this.data.context.selected_tags_this_run=[];}
    if(!labels.length)throw new Blocked('没有本轮选择标签');const samples=[],caseId=cancel?'L-26':'L-25';
    const interval=this.webRules?.tag_click_interval_ms??1500;
    if(interval<1000||interval>2000)throw new Blocked('标签点击间隔必须为1至2秒');
    const file=path.join(this.batch,caseId+'-save-requests.json');
    this.data.context.tag_save_evidence||={};this.data.context.tag_save_evidence[caseId]=[file];
    let finalCount;
    try{for(const label of labels){
      const before=await state(label),clickedAt=Date.now(),sample={label,clicked_at:clickedAt,before};samples.push(sample);
      try{sample.request=await this.site.profileSave(()=>tag(label).click());}
      catch(error){throw new Blocked('标签'+label+'保存请求未确认：'+error.message);}
      this.steps.push((cancel?'取消':'选择')+'标签'+label+'，保存请求HTTP '+sample.request.status+'完成');
      if(sample.request.status<200||sample.request.status>=300)throw new Error(label+'保存接口HTTP '+sample.request.status);
      await tag(label).locator('div[class*="border-[#FF66D6]"]',{}).first().waitFor({state:cancel?'hidden':'visible',timeoutMs:5000});
      finalCount=Number(before.count)+(cancel?-1:1);
      await this.tab.playwright.getByText(finalCount+'/6',{exact:true}).waitFor({state:'visible',timeoutMs:5000});
      sample.after=await state(label);
      if(Number(sample.after.count)!==finalCount||before.html===sample.after.html||sample.after.html.includes('border-[#FF66D6]')===cancel)throw new Error(label+'计数或高亮不符合预期');
      if(cancel)this.data.context.selected_tags_this_run=this.data.context.selected_tags_this_run.filter(item=>item!==label);
      else this.data.context.selected_tags_this_run.push(label);
      await this.save();
      // 接口完成后仍留出指定间隔，避免前一次异步保存覆盖后一次选择。
      await new Promise(resolve=>setTimeout(resolve,interval));
      sample.next_click_not_before=Date.now();
    }
    await this.profile(true);
    for(const label of labels){const persisted=await state(label);if(persisted.html.includes('border-[#FF66D6]')===cancel||Number(persisted.count)!==finalCount)throw new Error(label+'刷新后标签或计数未保存');}
    this.steps.push('刷新Profile，标签高亮和计数保持');return JSON.stringify(samples);
    }finally{await fs.writeFile(file,JSON.stringify(samples,null,2));await this.save();}
  }
  /** 在同一连续执行段完成问候和小窗操作，避免工具往返错过短通话。 */
  async conversation(prefix='M',gift=false){
    if(prefix==='M'&&await this.button('Connect').filter({visible:true}).count())throw new Blocked('M-13/M-14要求当前为游客；检测到已登录，保留账号状态，不自动退出登录补测');
    await this.nav('Match');const before=await this.remaining();if(!before)throw new Blocked('没有免费匹配次数');
    const key=prefix==='M'?'guest_matching_starts':prefix==='S'?'subscribed_matching_starts':'member_matching_starts';if((this.data.context[key]||0)>=(prefix==='S'?2:8))throw new Blocked('达到本轮匹配启动上限');
    await this.click('Start matching');this.data.context[key]=(this.data.context[key]||0)+1;await this.save();const end=Date.now()+15000;
    try{
      await this.matchingPermissionEvidence(prefix==='M'?'M-13':gift&&prefix==='N'?'N-10':prefix==='S'?'S-07':'N-08');
      while(Date.now()<end){
        const input=this.tab.playwright.getByRole('textbox',{name:'Send a message',exact:true});
        if(await input.count()){
          const source=()=>this.tab.playwright.evaluate(()=>Array.from(document.querySelectorAll('video')).filter(e=>e.getBoundingClientRect().width>0).map(e=>({self:!e.currentSrc,ready:e.readyState,width:e.getBoundingClientRect().width,x:e.getBoundingClientRect().x})));
          const videos=await source();if(videos.length<2)continue;
          await this.matchingPermissionEvidence(prefix==='M'?'M-13':gift&&prefix==='N'?'N-10':prefix==='S'?'S-07':'N-08');
          if(gift){
            const result=await this.sendGift(prefix==='S');
            this.data.context.gift_evidence=await this.capture('gift-result');
            await this.tab.goto(this.url+'lobby/profile');await this.profile();const after=await this.balance(),before=this.data.context.gift_balance_before;
            this.data.context.gift_evidence.push(...await this.capture('gift-balance-after'));
            if(this.data.context.gift_branch==='positive'&&before-after!==20)throw new Error('星星礼物成功消息后扣费不符：'+before+'→'+after+'，预期扣20');
            if(this.data.context.gift_branch==='zero'&&after!==before)throw new Error('金币不足仍扣费：'+before+'→'+after);
            return result+'；实际金币'+before+'→'+after;
          }
          const message=['Hi!','Hello!','Hey!'][randomInt(3)];await input.fill(message);await this.css('button:has(use[href="#icon-chat/send-active"])');await this.wait(message,1500);
          const messageId=prefix==='M'?'M-13':'N-08';await this.record(messageId,'PASS','发送'+message+'，本人会话区可见；不代表对方已收信',Date.now(),await this.capture(messageId));
          const beforeSwap=await source();const selfIndex=beforeSwap.findIndex(v=>v.self);if(selfIndex<0)throw new Blocked('没有本人视频');await this.tab.playwright.locator('video').nth(selfIndex).click();const after=await source();
          const selfBefore=beforeSwap.find(v=>v.self),selfAfter=after.find(v=>v.self);if(!selfAfter||selfBefore.width===selfAfter.width)throw new Error('本人小窗点击后尺寸未交换');
          const swapId=prefix==='M'?'M-14':'N-09';await this.record(swapId,'PASS',JSON.stringify({before:beforeSwap,after}),Date.now(),await this.capture(swapId));
          return '问候与本人小窗交换已在同一通话完成';
        }
        if((await this.text()).includes('Continue with Google'))throw new Blocked('登录弹窗遮挡匹配');
      }
      throw new Blocked('15秒内没有进入可操作双视频通话，不自动重复启动');
    }finally{await this.tab.goto(this.url+'lobby/anchor');this.steps.push('离开Match停止自动续配');}
  }
  /** 发送星星礼物，仅把会话区消息作为成功，礼物面板图标不算。 */
  async sendGift(subscribed){
    await fs.writeFile(path.join(this.batch,'gift-before.txt'),await this.tab.playwright.domSnapshot());
    const direct=this.tab.playwright.locator('li img[src$="gift-star.png"]').filter({visible:true});
    if(!await direct.count())await this.css('svg:has(use[href*="gift"])');await fs.writeFile(path.join(this.batch,'gift-panel.txt'),await this.tab.playwright.domSnapshot());
    const star=this.tab.playwright.locator('img[src$="gift-star.png"]').filter({visible:true});if(!await star.count())throw new Blocked('桌面礼物面板未找到可确认的星星礼物，保留现场');await star.first().click();
    const send=this.button('Send').filter({visible:true});if(await send.count())await send.click();this.steps.push('选择星星礼物并按界面要求发送');
    const before=this.data.context.gift_balance_before;
    const end=Date.now()+5000;let body='',message=null;
    while(Date.now()<end){
      body=await this.text();
      if(/Sending Gifts is exclusive to Premiums/i.test(body))throw new Error(subscribed?'会员已确认但仍提示仅会员可送礼':'未订阅送礼被会员限制拦截；最新规则应按金币余额判断，余额'+before);
      await fs.writeFile(path.join(this.batch,'gift-after.txt'),await this.tab.playwright.domSnapshot());
      if(/insufficient|not enough|金币不足/i.test(body)){
        if(subscribed||before>=20)throw new Error('余额足够但礼物发送失败：'+body.slice(-400));
        this.data.context.gift_branch='zero';return 'PC未订阅、余额'+before+'：发送失败，出现金币不足提示；有金币分支本次尚未覆盖';
      }
      message=await this.tab.playwright.locator('img[src$="gift-star.png"]').evaluateAll(es=>es.filter(e=>e.getBoundingClientRect().height>0&&!e.closest('li,[role=dialog],[data-rsbs-overlay]')).map(e=>({src:e.getAttribute('src'),context:e.parentElement.parentElement.innerText,html:e.parentElement.parentElement.outerHTML.slice(0,2000)})));
      if(message.length){this.data.context.gift_message=message;this.data.context.gift_branch='positive';return (subscribed?'已订阅':'PC未订阅有金币')+'：会话区出现星星礼物成功消息；发送前余额'+before+'；'+JSON.stringify(message);}
    }
    throw new Blocked('已发送一次礼物，但未获得会话成功消息或金币不足提示；不为补证据重发');
  }
  /** 打开会员金币页，只接受七个有价格的实际充值档位。 */
  async gemTiers(){await this.profile();await this.tab.playwright.getByRole('button',{name:/Gems Store/}).click();await this.tab.playwright.locator('li[data-id]').first().waitFor({state:'visible',timeoutMs:8000});const tiers=await this.tab.playwright.locator('[data-id]').evaluateAll(es=>es.filter(e=>e.getBoundingClientRect().height>0&&/[¥$€£]\d/.test(e.innerText)).map(e=>({id:e.getAttribute('data-id'),text:e.innerText})));if(tiers.length!==7)throw new Error('金币档位不是7个带价格项：'+JSON.stringify(tiers));return tiers;}
  /** 读取本批单笔支付记录，只允许原订单恢复，不能为补证据重复付款。 */
  async payments(){try{return JSON.parse(await fs.readFile(path.join(this.batch,'payment-attempts.json'),'utf8'));}catch(e){if(e.code==='ENOENT')return {};throw e;}}
  /** 支付状态即时原子保存，提交前先保存submitted节点。 */
  async savePayments(data){const file=path.join(this.batch,'payment-attempts.json');await fs.writeFile(file+'.tmp',JSON.stringify(data,null,2));await fs.rename(file+'.tmp',file);}
  /** test沙箱真实支付，只执行一笔；会员恢复不走此方法。 */
  async pay(kind='subscription'){
    if(this.environment!=='test')throw new Skipped('prod跳过支付');const payments=await this.payments();if(payments[kind])throw new Blocked('已有原订单，只允许补核验，不重复支付');
    const state=await this.profile();if(state.state!==(kind==='subscription'?'unsubscribed':'subscribed'))throw new Blocked('付款前会员条件不满足');const before=await this.balance();
    let coins=300,tier=null;if(kind==='coin'){const tiers=await this.gemTiers();for(const t of tiers)t.coins=Number((t.text.match(/^\s*(\d[\d,]*)\s*$/m)||[])[1]?.replaceAll(',',''));tier=tiers.filter(t=>t.coins>0).sort((a,b)=>a.coins-b.coins)[0];if(!tier)throw new Blocked('无法读取档位承诺金币');coins=tier.coins;}
    payments[kind]={state:'prepared',before,coins,membership_before:state,created_at:Date.now()/1000};await this.savePayments(payments);
    if(kind==='subscription'){await this.click('Upgrade to Premium');await this.click('1 week');}
    else await this.css('[data-id="'+tier.id+'"]');
    await this.click('Credit Card');const previous=new Set((await this.browser.tabs.list()).map(p=>p.id));
    if(kind==='subscription')await this.tab.playwright.locator('[data-premium-subscribe]').click();else await this.click('Purchase');let provider;const end=Date.now()+20000;
    while(Date.now()<end){const pages=await this.browser.tabs.list();const found=pages.find(p=>!previous.has(p.id)&&new URL(p.url||this.url).origin==='https://cashier-sandbox.waffo.com');if(found){provider=await this.browser.tabs.get(found.id);break;}}
    if(!provider)throw new Blocked('未找到已确认的Waffo沙箱，停止支付');await provider.playwright.getByText(/Test Mode|测试模式/).first().waitFor({state:'visible',timeoutMs:8000});const body=await provider.playwright.locator('body').innerText();if(!/Test Mode|测试模式/.test(body)||!/No real payment will be processed|不会处理真实付款/.test(body))throw new Blocked('未确认无真实付款的测试模式');
    const english=body.includes('Test Mode');await provider.playwright.getByRole('button',{name:english?'Success':'成功',exact:true}).first().click();
    const saveInfo=provider.playwright.getByRole('checkbox',{name:/保存.*信息|save.*information/i});await saveInfo.waitFor({state:'visible',timeoutMs:5000});await saveInfo.uncheck();
    payments[kind].state='submitted';payments[kind].submitted_at=Date.now()/1000;await this.savePayments(payments);
    await provider.playwright.getByRole('button',{name:english?'Pay':'支付',exact:true}).click();await provider.playwright.getByText(/Payment Successful|Payment successful|支付成功/).first().waitFor({state:'visible',timeoutMs:45000});
    payments[kind].state='confirmed';payments[kind].confirmed_at=Date.now()/1000;await this.savePayments(payments);await fs.writeFile(path.join(this.batch,kind+'-sandbox-success.png'),await provider.screenshot({fullPage:false}));
    const done=provider.playwright.getByRole('link',{name:english?'Done':'完成',exact:true});if(await done.count())await done.click();else await provider.playwright.getByRole('button',{name:english?'Done':'完成',exact:true}).click();
    const returnEnd=Date.now()+15000;while(Date.now()<returnEnd){const merchant=await this.text();const success=kind==='subscription'?/Premium unlocked|Subscription successful|Premium successfully/i.test(merchant):/Top Up Successful|Diamonds Added/i.test(merchant);if(success){payments[kind].success_dialog=true;payments[kind].success_evidence=await this.capture(kind+'-merchant-success');await this.savePayments(payments);}if(new URL(await this.tab.url()).pathname==='/lobby/profile'){payments[kind].automatic=true;await this.savePayments(payments);if(success)break;}}
    return this.verifyPayment(kind);
  }
  /** 只补核验原订单，缺少回站或成功提示时阻塞并保留已成功订单。 */
  async verifyPayment(kind){
    const payments=await this.payments(),p=payments[kind];if(!p||!['confirmed','verified'].includes(p.state))throw new Blocked('没有原订单成功检查点');if(kind==='subscription'&&p.membership_before.state!=='unsubscribed')throw new Blocked('缺少原订单未订阅前置');
    const returned=p.automatic===true;if(!returned)throw new Blocked('支付未确认自动回站，禁止重复付款');const body=await this.text(),success=p.success_dialog===true||(kind==='subscription'?/Premium unlocked|Subscription successful/i.test(body):/Top Up Successful|Diamonds Added/i.test(body));if(body.includes('Start Exploring'))await this.click('Start Exploring');
    const state=await this.profile(),after=await this.balance();p.after=after;p.profile=state;p.automatic=returned;p.success_dialog=success;await this.savePayments(payments);
    if(after!==p.before+p.coins||(kind==='subscription'&&state.state!=='subscribed'))throw new Blocked('原订单到账/会员未确认：'+JSON.stringify(p));p.state='verified';await this.savePayments(payments);
    if(kind==='subscription'){this.data.context.initial_subscription_verified_at=p.confirmed_at;await this.save();}if(!success)throw new Blocked('原订单已到账，但成功提示缺证据，不重复付款');return JSON.stringify(p);
  }
  /** 跟踪PC独立Google窗口；确认账号及常规继续操作，关窗本身不能判定登录成功。 */
  async confirmLoginPopup(account){
    const pages=await this.browser.tabs.list();const found=pages.find(p=>{try{return new URL(p.url).hostname==='accounts.google.com';}catch{return false;}});
    if(!found)return {popup:false};const popup=await this.browser.tabs.get(found.id);const snapshot=await popup.playwright.domSnapshot();
    await fs.writeFile(path.join(this.batch,'google-popup-latest.txt'),snapshot);
    if(/captcha|验证码|verify it.s you|fingerprint|button "(?:I agree|Agree|Accept|同意|接受)"/i.test(snapshot))throw new Blocked('Google独立窗口需要用户完成验证或协议确认，保留窗口');
    const identity=popup.playwright.getByText(account,{exact:false});if(await identity.count()){await identity.first().click();this.steps.push('在独立Google窗口选择指定账号');return {popup:true,action:'account'};}
    for(const label of ['Continue','继续','Confirm','确认']){const next=popup.playwright.getByRole('button',{name:label,exact:true});if(await next.count()){await next.click();this.steps.push('在独立Google窗口点击'+label+'，仍等待被测站回调');return {popup:true,action:label};}}
    return {popup:true,action:'waiting'};
  }
  /** 在用户点击Google之前建立检查点；已经登录时禁止重新开始身份计时。 */
  async armLoginObservation(){
    const snapshot=await this.tab.playwright.domSnapshot();
    if(!/button "Log in"|Continue with Google/.test(snapshot))throw new Blocked('启动观察时已经登录或页面未就绪，不能追溯新老用户');
    if(this.data.context.login_classification)throw new Blocked('本批已有登录窗口，不重新计时');
    this.data.context.login_watch_armed_at=Date.now()/1000;
    this.data.context.login_observation={samples:[],healthy:true,last_sample_at:null,incoming:[],callback_continuous:true,last_callback_poll_at:Date.now()};
    await this.save();return {state:'armed'};
  }
  /** 捕获来电后先保存弹窗截图再操作；同时兼容桌面的Later/Join和Cancel/Answer。 */
  async pollIncoming(body=null){
    body??=await this.text();const labels=await this.tab.playwright.getByRole('button').filter({visible:true}).allTextContents({timeoutMs:2000});
    const controls=incomingControls(body,labels.map(t=>t.trim()));
    const context=this.data.context,watch=context.login_observation;
    if(!watch)return false;
    if(!controls){watch.popup_visible=false;return false;}
    if(watch.popup_visible)return true;
    watch.popup_visible=true;
    const item={seen_at:Date.now(),controls,evidence:await this.capture('incoming-visible'),action:null};
    watch.incoming.push(item);
    if(watch.incoming.slice(0,-1).some(e=>e.action==='answer')){item.action='not_repeated';await this.save();return true;}
    item.action='answer';item.clicked_at=Date.now();
    // 限时弹窗先点击，再保存检查点，避免磁盘写入和身份等待消耗接听时机。
    await this.click(controls.answer);
    const end=Date.now()+6000;
    while(Date.now()<end){
      const text=await this.text();
      if(!incomingControls(text,labels.map(t=>t.trim()))){item.closed=true;break;}
    }
    {
      const readyEnd=Date.now()+6000;
      do{item.media=await this.tab.playwright.locator('video').evaluateAll(es=>es.filter(e=>e.getBoundingClientRect().height>20).map(e=>({ready:e.readyState,width:e.videoWidth})));item.connected=item.media.some(e=>e.ready>=2&&e.width>0);if(item.connected)break;}while(Date.now()<readyEnd);
    }
    if(context.login_classification)context.login_classification.state='new';
    item.evidence.push(...await this.capture('incoming-after-answer'));
    watch.popup_visible=!item.closed;await this.save();return true;
  }
  /** 只读Official行的实际消息角标，不把No Chats yet当成Official无消息。 */
  async officialMessages(){
    return this.tab.playwright.evaluate(()=>{
      const title=Array.from(document.querySelectorAll('*')).find(e=>!e.children.length&&/^Official$|team\s*office/i.test(e.textContent.trim()));
      if(!title)return null;
      const row=title.parentElement;
      const numbers=Array.from(row.querySelectorAll('*')).filter(e=>!e.children.length&&e.getBoundingClientRect().height>0&&/^\d+$/.test(e.textContent.trim())).map(e=>Number(e.textContent));
      return numbers.length?Math.max(...numbers):0;
    });
  }
  /** 使用可恢复的短观察段跟踪真实回调，同时采集Chat和来电，切段不会重启计时。 */
  async observeLogin(timeoutMs=20000){
    const context=this.data.context;
    if(!context.login_watch_armed_at)throw new Blocked('必须在点击Google前启动本轮登录观察');
    const watch=context.login_observation||=( {samples:[],healthy:true,last_sample_at:null,incoming:[]} );
    if(context.login_classification?.completed)return context.login_classification;
    const sliceEnd=Date.now()+Math.min(timeoutMs,20000);
    while(Date.now()<sliceEnd){
      if(!context.login_classification){
        const checked=Date.now();
        if(watch.last_callback_poll_at&&checked-watch.last_callback_poll_at>5000)watch.callback_continuous=false;
        watch.last_callback_poll_at=checked;
        const snapshot=await this.tab.playwright.domSnapshot();
        if(/button "Log in"|dialog "Login"/.test(snapshot)||!snapshot.includes('banner')){
          try{if(!watch.waiting_user)await this.confirmLoginPopup(context.google_account||'songjill0221@gmail.com');}
          catch(error){watch.waiting_user=error.message;await this.save();}
          continue;
        }
        context.login_classification={state:'unknown',login_at:Date.now()/1000,login_time_verified:watch.callback_continuous!==false};await this.save();
        await this.pollIncoming();await this.tab.goto(this.url+'lobby/sessions');
      }
      const classification=context.login_classification,loginAt=classification.login_at*1000;
      const body=await this.text(),now=Date.now();
      const valid=new URL(await this.tab.url()).pathname==='/lobby/sessions'&&/Chats|Messages|No messages|team\s*office/i.test(body)&&!/Loading failed|Network error/i.test(body);
      if((watch.last_sample_at&&now-watch.last_sample_at>5000)||(!watch.last_sample_at&&now-loginAt>5000))watch.healthy=false;
      if(!valid&&watch.last_sample_at)watch.healthy=false;
      const official=await this.officialMessages();
      watch.samples.push({time:now,healthy:valid,official_messages:official,text:/team\s*office/i.test(body)?'teamoffice':''});watch.last_sample_at=now;
      classification.state=classifyDesktopLogin(loginAt,watch.samples,now,watch.healthy&&valid,watch.incoming);
      await this.pollIncoming(body);
      if(classification.state==='new'||now>=loginAt+30000){
        classification.completed=true;classification.observed_until=now/1000;classification.chat_window_valid=watch.healthy&&valid;
        classification.evidence=await this.capture('login-chat-classification');
        await fs.writeFile(path.join(this.batch,'login-chat-observations.json'),JSON.stringify(watch,null,2));await this.save();return classification;
      }
      await this.save();
    }
    return context.login_classification||{state:'waiting_login',completed:false};
  }
  /** 将观察记录写为首次来电接听和身份时限证据；PC不再生成L-05，不混淆漏捕与未出现。 */
  async finishIncoming(){
    const c=this.data.context.login_classification,watch=this.data.context.login_observation;
    const items=watch.incoming.filter(e=>e.seen_at>=c.login_at*1000&&e.seen_at<=c.login_at*1000+180000);
    if(items.length)c.state='new';
    const evidence=[...(c.evidence||[]),...items.flatMap(e=>e.evidence)];
    const first=items[0],answered=first?.action==='answer'&&first.closed&&first.connected;
    for(const id of ['L-04','L-06']){
      let status='BLOCKED',actual=`实际捕获来电${items.length}次；新老身份=${c.state}；首次接听并加载视频=${!!answered}`;
      if(c.state==='new'){
        status=id==='L-06'?'SKIPPED_STATE':answered?'PASS':'BLOCKED';
        actual+='；本次Official消息或欢迎来电确认新用户'+(id==='L-06'?'，老用户分支不适用':'');
      }else if(c.state==='existing'){
        status=id==='L-06'?(items.length?'FAIL':watch.continuous?'PASS':'BLOCKED'):'SKIPPED_STATE';
        actual+='；本次完整Chat窗口为老用户';
      }else actual+='；身份窗口不完整，来电操作证据单独保留，不能追溯身份';
      await this.record(id,status,actual,c.login_at*1000,evidence);
    }
    watch.finalized=true;await this.save();return {completed:true,events:items.length};
  }
  /** 延续登录时已经启动的三分钟观察；每段最多20秒，独立运行器可连续调用。 */
  async incoming(timeoutMs=20000){
    const c=this.data.context.login_classification,watch=this.data.context.login_observation;
    if(!c||!watch)throw new Blocked('没有本次登录连续观察检查点，不能追溯');
    if(watch.finalized)return {completed:true};
    const deadline=c.login_at*1000+180000,end=Math.min(Date.now()+Math.min(timeoutMs,20000),deadline);
    watch.continuous??=watch.healthy;
    while(Date.now()<end){
      if(watch.last_incoming_sample_at&&Date.now()-watch.last_incoming_sample_at>5000)watch.continuous=false;
      if(!watch.last_incoming_sample_at&&Date.now()-(watch.last_sample_at||c.login_at*1000)>5000)watch.continuous=false;
      await this.pollIncoming();watch.last_incoming_sample_at=Date.now();await this.save();
      if(watch.incoming.some(e=>e.action==='answer'&&e.connected)){await this.tab.goto(this.url+'lobby/anchor');return this.finishIncoming();}
    }
    if(Date.now()>=deadline)return this.finishIncoming();
    return {completed:false,seconds_left:Math.max(0,(deadline-Date.now())/1000)};
  }
  /** 读取已经接通的视频和唯一通话计时，不用拨号等待时间代替通话时长。 */
  async callClock(){
    return this.tab.playwright.evaluate(()=>{
      const videos=Array.from(document.querySelectorAll('video')).filter(e=>e.getBoundingClientRect().height>20&&e.readyState>=2&&e.videoWidth>0);
      const timers=Array.from(document.querySelectorAll('span,p,div')).filter(e=>e.getBoundingClientRect().height>0).map(e=>e.textContent.trim()).filter(t=>/^\d{1,2}:\d{2}(?::\d{2})?$/.test(t));
      const unique=[...new Set(timers)];if(videos.length<2||unique.length!==1)return null;
      const freeLabel=Array.from(document.querySelectorAll('span,p,div')).filter(e=>!e.children.length&&e.getBoundingClientRect().height>0).map(e=>e.textContent.trim()).find(t=>/^(?:free(?: (?:video )?call)?|免费(?:通话)?)$/i.test(t));
      const parts=unique[0].split(':').map(Number);return {label:unique[0],seconds:parts.reduce((n,v)=>n*60+v,0),free:!!freeLabel,free_label:freeLabel||''};
    });
  }
  /** 从实际卡片选择普通或Free通话，最多换三名离线主播，不把免费匹配当耗币。 */
  async dialBilling(free,attempt){
    await this.discover();const targets=await this.tab.playwright.evaluate(()=>Array.from(document.querySelectorAll('button')).filter(e=>e.textContent.trim()==='Video'&&e.getBoundingClientRect().height>0).map((e,index)=>({index,text:e.parentElement.parentElement.parentElement.innerText})));
    const knownFree=this.data.context.billing_free_targets||[];
    const selected=targets.filter(t=>(/\bfree\b|免费/i.test(t.text)===free)&&!(!free&&knownFree.includes(t.text))).slice(0,3);
    // 在返回Profile前持久化筛选现场；无候选也必须有列表截图和真实筛选记录。
    const searchEvidence=await this.capture(free?'free-call-discover':'paid-call-discover');
    const selectionFile=searchEvidence[0].replace(/\.png$/,'.json');
    attempt.candidate_search={free,url:await this.tab.url(),observed_at:Date.now(),targets,selected};
    await fs.writeFile(selectionFile,JSON.stringify(attempt.candidate_search,null,2));
    attempt.evidence.push(...searchEvidence,selectionFile);await this.save();
    this.steps.push('Discover筛选'+(free?'Free':'普通')+'主播：可见Video卡片'+targets.length+'个，候选'+selected.length+'个；已保存列表现场和筛选记录');
    if(!selected.length)throw new Blocked('本次可见列表未筛选到'+(free?'明确带Free标识':'没有Free标识')+'的主播卡片，未拨号；列表证据已保存');
    for(const target of selected){
      const evidence=await this.capture(free?'free-call-card':'paid-call-card');
      attempt.evidence.push(...evidence);await this.save();
      await this.button('Video').nth(target.index).click();this.steps.push('选择'+(free?'Free':'普通')+'通话，卡片='+target.text);
      const end=Date.now()+30000;
      while(Date.now()<end){
        const body=await this.text();if(/offline|not online/i.test(body)){await this.discover();break;}
        if(body.includes('Get Premium'))throw new Blocked('拨打前会员已失效，需要恢复后重新核对');
        if(/not enough|insufficient|out of gems|buy gems/i.test(body))throw new Blocked('通话前出现金币不足，未取得通话计费前置');
        const clock=await this.callClock();if(clock)return {target,clock};
      }
      if(new URL(await this.tab.url()).pathname!=='/lobby/anchor')await this.tab.goto(this.url+'lobby/anchor');
    }
    throw new Blocked('最多三名目标均未接通，不能通过拨号时长推断扣费');
  }
  /** 通话一分钟后正常挂断并核对真实余额；证据不足时保留扣费事实但不判通过。 */
  async billingCall(free=false){
    // S-10：免费一分钟停用，旧的直接调用也不进入通话。
    if(free)throw new Skipped('S-10已在所有端停用：免费一分钟停用');
    if(this.environment!=='test')throw new Skipped('prod跳过已订阅Video及通话耗币');
    const state=await this.profile();if(state.state!=='subscribed')throw new Blocked('需要先刷新Profile确认会员');
    const before=await this.balance();if(!free&&before<80)throw new Blocked('余额'+before+'不足一分钟80金币，不能继续普通通话');
    if(!this.data.context.first_subscribed_video)throw new Blocked('需先处理S-04首次已订阅Video余额提示，再测试计费');
    const attempt={before,free,started_at:Date.now(),evidence:await this.capture('billing-before')};
    const journal=this.data.context.call_billing_attempts||=[];journal.push(attempt);await this.save();
    let issue=null;
    try{
      const dial=await this.dialBilling(free,attempt);attempt.target=dial.target;attempt.actual_free=dial.clock.free;
      attempt.evidence.push(...await this.capture('billing-connected'));
      if(dial.clock.free!==free){
        if(dial.clock.free){this.data.context.billing_free_targets||=[];this.data.context.billing_free_targets.push(dial.target.text);}
        throw new Blocked('实际通话'+(dial.clock.free?'有Free免费标识，不作为耗币通话':'没有Free标识，不能作为免费通话'));
      }
      const deadline=Date.now()+75000;let last=dial.clock;
      while(Date.now()<deadline){
        const clock=await this.callClock();if(!clock)throw new Blocked('通话提前结束或缺少唯一计时，最近记录'+last.label);
        last=clock;attempt.seconds=clock.seconds;
        if(clock.free!==free)throw new Blocked('通话中Free标识发生变化，不能作为单一计费类型核验');
        if(clock.seconds>=60){attempt.evidence.push(...await this.capture('billing-minute'));break;}
      }
      if(!(attempt.seconds>=60))throw new Blocked('未观察到完整一分钟通话');
    }catch(error){issue=error;}
    finally{
      const hangup=this.tab.playwright.locator('button:has(use[href="#icon-calling/call-ing-hangup"]),svg:has(use[href="#icon-calling/call-ing-hangup"])').filter({visible:true});
      if(await hangup.count())await hangup.first().click();
      await this.tab.goto(this.url+'lobby/profile');await this.profile();attempt.after=await this.balance();
      attempt.evidence.push(...await this.capture('billing-after'));attempt.finished_at=Date.now();await this.save();
    }
    if(issue)throw new Blocked(issue.message+'；实际金币'+before+'→'+attempt.after+'，已正常退出并保留余额证据');
    const result=checkCallBilling({...attempt,rate:80});attempt.result=result;await this.save();
    if(result.status==='BLOCKED')throw new Blocked(result.actual);if(result.status==='FAIL')throw new Error(result.actual);
    return result.actual;
  }
  /** PC小额整20余额通过真实有价礼物准备0，限制单次匹配和礼物数，不修改后台余额。 */
  async spendGiftToZero(before){
    if(this.environment!=='test')throw new Skipped('prod禁止消费准备');
    if(!(before>0&&before<=300&&before%20===0))throw new Blocked('余额不满足有限礼物消费条件：'+before);
    const gifts=[...Array(Math.floor(before/100)).fill({file:'gift-bear.png',price:100}),...Array((before%100)/20).fill({file:'gift-star.png',price:20})];
    await this.nav('Match');await this.remaining();await this.click('Start matching');
    const evidence=this.data.context.zero_balance_evidence||=[];
    try{
      const deadline=Date.now()+12000;let connected=false;
      while(Date.now()<deadline){if(await this.tab.playwright.getByRole('textbox',{name:'Send a message',exact:true}).count()){connected=true;break;}await this.tab.playwright.domSnapshot();}
      if(!connected)throw new Blocked('消费准备没有进入可操作通话，不重复启动');
      for(const gift of gifts){
        const item=this.tab.playwright.locator('li:has(img[src$="'+gift.file+'"])').filter({visible:true});
        if(Number((await item.innerText()).trim())!==gift.price)throw new Blocked('礼物实际价格不符，不发送');
        await item.locator('img').first().click();this.steps.push('真实消费礼物'+gift.file+'，页面价格'+gift.price);
        const end=Date.now()+2500;let sent=false;
        while(Date.now()<end){
          if(/exclusive to Premiums|insufficient|not enough/i.test(await this.text()))throw new Blocked('真实消费被拦截，不重复发送');
          sent=await this.tab.playwright.locator('img[src$="'+gift.file+'"]').evaluateAll(es=>es.some(e=>e.getBoundingClientRect().height>0&&!e.closest('li')));if(sent)break;
        }
        if(!sent)throw new Blocked('已发送一次但成功消息不明，不重发');evidence.push(...await this.capture('zero-balance-gift'));
      }
    }finally{await this.tab.goto(this.url+'lobby/profile');}
    await this.profile();const after=await this.balance();evidence.push(...await this.capture('zero-balance-after'));await this.save();
    if(after!==0)throw new Blocked('消费后实际余额'+after+'，没有达到0，不追加盲目消费');return after;
  }
  /** 为无金币边界通过真实普通通话消费，预算不足或免费数据不会伪造余额0。 */
  async spendToZero(){
    if(this.environment!=='test')throw new Skipped('prod禁止通话消费准备');
    const settings=JSON.parse(await fs.readFile(path.join(root,'web_ui_config.json'),'utf8'));
    const budget=settings.call_billing?.max_spend_seconds||600,end=Date.now()+budget*1000;
    await this.profile();let balance=await this.balance();
    if(balance>0&&balance<=300&&balance%20===0)return this.spendGiftToZero(balance);
    while(balance>0){
      if(Date.now()+65000>end)throw new Blocked('真实通话消费预算'+budget+'秒已用尽，当前余额'+balance);
      if(balance<80)throw new Blocked('余额'+balance+'不足80金币，无法通过正常整分钟通话继续消费到0，保留实际数据');
      const before=balance,attemptsBefore=this.data.context.call_billing_attempts?.length||0;let actual;
      try{actual=await this.billingCall(false);}catch(error){const attempts=this.data.context.call_billing_attempts||[],attempt=attempts.at(-1);if(attempts.length>attemptsBefore&&attempt.actual_free===true&&attempt.after===before){this.steps.push('实际通话为Free且未扣费，退出后换下一位普通主播');continue;}throw error;}
      this.steps.push('无金币前置：'+actual);
      balance=await this.balance();if(balance>=before)throw new Blocked('普通通话后余额未减少，停止继续消费');
    }
    return balance;
  }
  /** 首次Video前只清理被测源缓存，清理后必须复核同一账号和有效会员。 */
  async prepareFirstVideo(){
    if(this.environment!=='test')throw new Skipped('prod跳过S-04缓存前置及已订阅Video');
    if(!this.site?.identity||!this.site?.clearSiteCache)throw new Blocked('S-04缺少本站缓存清理及账号复核适配器，未点击Video');
    const origin=new URL(this.url).origin,detailsPath=this.webRules?.account_details_path||'/web/v1/accounts/details';
    let checkpoint=this.data.context.first_video_cache;
    if(checkpoint&&checkpoint.origin!==origin)throw new Blocked('缓存清理检查点属于其他域名');
    if(!checkpoint?.cleared_at){
      const before=await this.site.identity({origin,detailsPath});
      checkpoint={origin,uuid:before.uuid,evidence:await this.capture('S-04-before-cache')};
      const cleared=await this.site.clearSiteCache({origin,deviceKeys:['ios_deviceId','android_deviceId','client_id','deviceId']});
      if(cleared.origin!==origin||!cleared.cleared_at)throw new Blocked('未获得同源缓存清理完成证据');
      Object.assign(checkpoint,cleared);this.data.context.first_video_cache=checkpoint;await this.save();
      this.steps.push('清理当前被测源页面缓存和本地业务状态，保留设备标识及认证Cookie/IndexedDB');
    }else this.steps.push('复用本批首次Video前缓存清理检查点，不重复清理登录数据');
    try{
      const after=await this.site.identity({origin,detailsPath});
      if(!after.uuid||after.uuid!==checkpoint.uuid)throw new Blocked('清理缓存后账号UUID不一致，未点击Video');
      const state=await this.profile(true);
      if(state.state!=='subscribed')throw new Blocked('清理缓存后会员未确认有效，未点击Video');
      checkpoint.verified_at=Date.now();checkpoint.evidence.push(...await this.capture('S-04-after-cache'));
      const file=path.join(this.batch,'S-04-cache-preflight.json');
      await fs.writeFile(file,JSON.stringify(checkpoint,null,2));if(!checkpoint.evidence.includes(file))checkpoint.evidence.push(file);
      await this.save();this.steps.push('刷新后复核UUID相同、已登录且会员有效');return checkpoint;
    }catch(error){throw new Blocked('S-04缓存已清理但身份/会员复核未完成；若退出登录先登录原账号，再继续本批：'+error.message);}
  }
  /** 已订阅Video按首次、无金币及可负担拨号分别检查；无金币数据通过真实通话准备。 */
  async subscribedVideo(mode){
    if(mode==='first'){if(this.data.context.first_subscribed_video)throw new Blocked('本轮首次Video已消耗，不能追溯');await this.prepareFirstVideo();}
    const state=await this.profile();if(state.state!=='subscribed')throw new Blocked('会员未确认，不执行Video');let before=await this.balance();
    const caseId={first:'S-04',empty:'S-05',dial:'S-06'}[mode];
    this.data.context.subscribed_video_evidence||={};
    const evidence=this.data.context.subscribed_video_evidence[caseId]=[];
    if(mode==='empty'&&before!==0)before=await this.spendToZero();
    if(mode==='first'&&this.data.context.first_subscribed_video)throw new Blocked('本轮首次Video已消耗，不能追溯');
    await this.discover();const cards=await this.cards();const attempts=mode==='dial'?Math.min(3,cards.length):1;
    try{for(let i=0;i<attempts;i++){if(mode==='first'){this.data.context.first_subscribed_video=true;await this.save();}await this.button('Video').nth(i).click();this.steps.push('已订阅点击'+cards[i]?.name+'的Video');let body=await this.text();
        const feedbackEnd=Date.now()+6000;
        while(Date.now()<feedbackEnd&&!/connecting|calling|offline|not online|gems|diamonds|balance|not enough|insufficient|out of|no gems|buy gems/i.test(body)){body=await this.text();}
        if(mode==='first'){await this.wait('Balance',6000);const text=await this.tab.playwright.getByText('Balance',{exact:true}).filter({visible:true}).evaluate(e=>{let panel=e;while(panel.parentElement&&(!panel.innerText.includes('Call rate:')||!panel.querySelector('button')))panel=panel.parentElement;return panel.innerText;});if(!new RegExp('\\b'+before+'\\b').test(text)||!/gems|diamonds|balance/i.test(text))throw new Error('首次余额弹窗与当前金币不符：'+text);evidence.push(...await this.capture('S-04-balance-dialog'));await this.save();return '首次Video弹窗显示剩余金币'+before;}
        if(mode==='empty'){if(!/not enough|insufficient|out of|no gems|buy gems/i.test(body))throw new Error('0金币未显示金币不足弹窗');evidence.push(...await this.capture('S-05-empty-dialog'));await this.save();return '非首次0金币弹出无金币提示';}
        if(/offline|not online/i.test(body)){await this.discover();continue;}
        if(!/connecting|calling|dial|\b00:\d\d\b/i.test(body))throw new Blocked('未确认拨号已开始或目标离线：'+body.slice(-500));evidence.push(...await this.capture('S-06-dial'));await this.save();return '余额足够，目标'+cards[i]?.name+'进入拨号/通话';}
      throw new Blocked('最多三名主播均离线或缺数据，不继续拨号');
    }finally{await this.tab.goto(this.url+'lobby/anchor');}
  }
  /** 统一桌面用例分发，业务预期复用iOS编号，原生权限由Chrome专用适配器执行。 */
  async execute(id){
    if(this.disabledCases?.[id])throw new Skipped(id+'已在所有端停用：'+this.disabledCases[id]);
    if(this.excluded?.has(id))throw new Skipped('PC已移除用例'+id+'；手机用例保留');
    const actions={
      'G-03':()=>this.tabs(),'G-04':async()=>{await this.discover();const cards=await this.cards();if(!cards.length)throw new Error('卡片不存在');return JSON.stringify(cards);},'G-05':()=>this.scrollFive(),
      'G-06':()=>this.guestAction('Say Hi'),'G-07':()=>this.guestAction('Video'),'G-17':async()=>{await this.nav('Profile');await this.wait('Continue with Google');return '桌面登录入口弹出登录框';},
      'M-09':()=>this.matchTabs(),'N-04':()=>this.matchTabs(),
      'M-10':async()=>{await this.nav('Match');await this.tab.playwright.getByRole('main').getByRole('button',{name:'Discover',exact:true}).click();await this.discover();return 'Match中间Discover跳转列表';},
      'M-12':()=>this.matchingExit('M'),'N-07':()=>this.matchingExit('N'),
      'M-13':()=>this.conversation('M'),'N-08':()=>this.conversation('N'),
      'L-02':async()=>{const state=await this.profile();if(state.state==='unknown')throw new Blocked('Profile登录会员状态未知');return '本轮Google登录回站，Profile会员='+state.state;},
      'L-03':async()=>{await this.discover();const cards=await this.cards();if(!cards.length)throw new Error('已登录卡片不存在');return JSON.stringify(cards);},
      'L-07':()=>this.sayHiOnce(),'L-08':()=>this.sayHi(),'L-09':async()=>{await this.discover();await this.button('Video').first().click();return this.premium();},'L-10':()=>this.closePremium(),
      'L-11':async()=>{await this.discover();if(!await this.tab.playwright.locator('span[class*="blur-"]').count())throw new Blocked('桌面卡片未展示打码标签，背景模糊不能当作标签');await this.css('span[class*="blur-"]');return this.premium();},
      'L-12':()=>this.detail(),'L-13':async()=>{await this.tab.playwright.locator('span[class*="blur-"]').first().waitFor({state:'visible',timeoutMs:5000});await this.css('span[class*="blur-"]');return this.premium();},
      'L-14':async()=>{await this.menu();if(!await this.tab.playwright.getByText('Cancel',{exact:true}).count())throw new Error('桌面更多只有拉黑和举报，没有Cancel，与iOS预期不一致');return '更多包含拉黑、举报、Cancel';},'L-15':()=>this.blockDialog(),'L-18':()=>this.blockCancel(),'L-16':()=>this.reportReasons(),'L-17':()=>this.reportSubmit(),'L-19':()=>this.blockSubmit(),
      'L-20':()=>this.carousel(false),'L-21':()=>this.carousel(true),'L-22':()=>this.back(),
      'L-23':()=>this.edit('nickname'),'L-24':()=>this.edit('signature'),'L-25':()=>this.tags(),'L-26':()=>this.tags(true),
      'L-27':async()=>{await this.click('Upgrade to Premium');return this.premium();},'L-28':()=>this.closePremium(),
      'L-29':async()=>{await this.profile();await this.tab.playwright.getByRole('button',{name:/Gems Store/}).click();return this.premium();},
      'L-30':async()=>{if(!(await this.text()).includes('Get Premium'))await this.click('Upgrade to Premium');const states=[];for(const tier of ['1 week','1 Month','3 Months']){await this.click(tier);const texts=await this.tab.playwright.locator('[data-premium-content] button').allTextContents({timeoutMs:2000});const payment=texts.find(t=>t.startsWith('Get '));if(!payment?.toLowerCase().includes(tier.toLowerCase()))throw new Error('支付按钮未同步'+tier);states.push(payment);}await this.closePremium();return states.join('；');},
      'N-05':async()=>{await this.nav('Match');await this.tab.playwright.getByRole('main').getByRole('button',{name:'Discover',exact:true}).click();await this.discover();return 'Match中间Discover跳转列表';},
      'N-02':()=>this.completePermissionGuide(),'N-03':()=>this.authorizedMatching(),
      'N-10':async()=>{await this.profile();this.data.context.gift_balance_before=await this.balance();return this.conversation('N',true);},
      'S-01':()=>this.pay('subscription'),'S-02':async()=>JSON.stringify(await this.gemTiers()),'S-03':()=>this.pay('coin'),
      'S-04':()=>this.subscribedVideo('first'),'S-05':()=>this.subscribedVideo('empty'),'S-06':()=>this.subscribedVideo('dial'),
      'S-09':()=>this.billingCall(false),
      'S-07':async()=>{await this.profile();this.data.context.gift_balance_before=await this.balance();if(this.data.context.gift_balance_before<20)throw new Blocked('不足20金币，不启动送礼匹配');return this.conversation('S',true);}
    };
    if(['G-10','G-11','G-12','N-01'].includes(id))return this.run(id,async()=>{if(!this.permissions)throw new Blocked('Chrome原生权限适配器未绑定，不能以DOM替代权限弹窗');return this.permissions.execute(id,this);});
    if(['M-11','N-06'].includes(id))return this.run(id,async()=>{throw new Blocked('与对应Exit用例共享一次启动，由M-12/N-07连续阶段记录连接页');});
    if(['M-14','N-09'].includes(id))return this.run(id,async()=>{throw new Blocked('由同次通话M-13/N-08连续阶段记录，不能追溯');});
    if(!actions[id])throw new Blocked('该项必须在对应有界观察中处理：'+id);
    return this.run(id,actions[id]);
  }
  /** 所有尚未实际操作的用例都明确记录阻塞，不能用移动端结果代替。 */
  async blockRemaining(reason,ids=null){const out=[];for(const id of ids||this.catalog.keys())if(!this.data.results[id]){this.steps=[];out.push(await this.record(id,'BLOCKED',reason,Date.now()));}return out;}
}

/** Chrome原生权限单独适配，不能将桌面权限文案写入Android/iOS驱动。 */
export class ChromePermissions {
  /** 只接收已选定Chrome应用和本轮测试标签标记。 */
  constructor(app,tabMarker='🧪 Web UI 测试'){this.app=app;this.tabMarker=tabMarker;}
  /** 每次读取最新AX索引，避免窗口切换后点击过期控件。 */
  async state(){return this.app.getAXState({emit:false,disableDiffing:true});}
  /** 在最新原生树中查找唯一控件并点击，匹配失败即停止。 */
  async click(pattern){const state=await this.state();const line=state.split('\n').find(l=>pattern.test(l));if(!line)throw new Blocked('原生Chrome缺少权限控件 '+pattern);await this.app.click(Number(line.trim().match(/^\d+/)[0]));return this.state();}
  /** 将本轮测试页置于真实前台，并核对域名，避免修改其他网站权限。 */
  async focus(suite){
    let state=await this.state();const host=new URL(suite.url).hostname;
    const front=()=>state.split('\n').some(l=>/地址和搜索栏/.test(l)&&l.includes(host));
    if(front())return;
    const title=await suite.tab.title();
    let line=state.split('\n').find(l=>l.includes('标签 ')&&l.includes(title)&&l.includes('属于“'+this.tabMarker+'”'));
    if(!line){
      const menu=state.split('\n').find(l=>/^\s*\d+ 窗口$/.test(l));
      if(!menu)throw new Blocked('缺少Chrome窗口菜单，无法定位本轮测试窗口');
      await this.app.click(Number(menu.trim().match(/^\d+/)[0]));state=await this.state();
      line=state.split('\n').find(l=>l.includes(title+', ID: makeKeyAndOrderFront:'));
      if(!line)throw new Blocked('窗口菜单中未找到被测网页，不能修改其他域名权限');
      await this.app.click(Number(line.trim().match(/^\d+/)[0]));state=await this.state();
      if(front())return;
      line=state.split('\n').find(l=>l.includes('标签 ')&&l.includes(title)&&l.includes('属于“'+this.tabMarker+'”'));
    }
    if(!line)throw new Blocked('本轮测试标签不可见');
    await this.app.click(Number(line.trim().match(/^\d+/)[0]));state=await this.state();
    if(!front())throw new Blocked('原生Chrome未确认本轮域名，不修改权限');
  }
  /** 通过站点信息重置本域权限，后续必须真实触发浏览器请求。 */
  async reset(suite){await this.focus(suite);let state=await this.state(),line=state.split('\n').find(l=>/弹出式按钮 查看网站信息/.test(l));if(!line)throw new Blocked('缺少Chrome站点信息入口');await this.app.click(Number(line.trim().match(/^\d+/)[0]));state=await this.state();line=state.split('\n').find(l=>/按钮 重置权限$/.test(l));if(!line)throw new Blocked('站点面板未提供可用重置权限入口');await this.app.click(Number(line.trim().match(/^\d+/)[0]));state=await this.state();if(!/可以询问能否使用/.test(state))throw new Blocked('未确认权限恢复询问态');await this.app.pressKey('Escape');await suite.tab.reload();suite.steps.push('通过Chrome站点面板重置本被测域名权限为询问');}
  /** 在浏览器实际权限提示中拒绝或允许，不设置假权限或模拟媒体。 */
  async answer(allow){let opened=false;const pattern=allow?/按钮 (在访问期间允许|仅此一次|访问该网站时允许|仅这次访问时允许|访问此网站时允许|允许这一次|允许|Allow this time|Allow on every visit)$/:/按钮 (一律不允许|不允许|禁止|不允许这次|Don't allow|Block)$/;const end=Date.now()+6000;while(Date.now()<end){const state=await this.state(),line=state.split('\n').find(l=>pattern.test(l));if(line){await this.app.click(Number(line.trim().match(/^\d+/)[0]));return this.state();}if(!opened&&/收到了权限请求/.test(state)){opened=true;await this.app.pressKey('super+alt+Up');}}throw new Blocked('未捕获真实Chrome权限按钮');}
  /** 在Chrome完整站点设置页允许本域权限，返回原标签捕获真实重载通知。 */
  async allowFromSiteSettings(suite){
    await this.focus(suite);await this.app.pressKey('super+t');
    await this.app.paste('chrome://settings/content/siteDetails?site='+encodeURIComponent(new URL(suite.url).origin));await this.app.pressKey('Return');
    let state=await this.state();
    if(!state.includes('chrome://settings/content/siteDetails?site=')||!state.includes(new URL(suite.url).hostname))throw new Blocked('未核对完整站点设置的目标域名');
    for(const name of ['摄像头','麦克风']){
      let line=state.split('\n').find(l=>l.includes('弹出式按钮 '+name+', Value:'));
      if(!line)throw new Blocked('站点设置缺少'+name+'选项');
      if(!line.includes('Value: 允许')){
        await this.app.click(Number(line.trim().match(/^\d+/)[0]));state=await this.state();
        line=state.split('\n').find(l=>/^\s*\d+ 允许(?:,.*)?$/.test(l));
        if(!line)throw new Blocked('实际菜单没有允许选项');
        await this.app.click(Number(line.trim().match(/^\d+/)[0]));state=await this.state();suite.steps.push('Chrome完整站点设置允许'+name);
      }
    }
    if(!['摄像头','麦克风'].every(name=>state.includes('弹出式按钮 '+name+', Value: 允许')))throw new Blocked('未确认两项权限均已允许');
    await this.app.pressKey('super+w');await this.state();await this.focus(suite);return this.state();
  }
  /** PC拒绝检查说明引导，允许检查Chrome重载提示；手机流程保持独立。 */
  async execute(id,suite){
    const index=['G-10','G-11','G-12'].includes(id)?['G-10','G-11','G-12'].indexOf(id):['N-01','N-02','N-03'].indexOf(id);
    if(index===0){await this.reset(suite);await suite.nav('Match');await suite.remaining();await suite.click('Start matching');await this.answer(false);await suite.wait('Important Tips',8000);const body=await suite.text();if(!body.includes('How Can I Provide Permission?'))throw new Error('PC拒绝后缺少How Can I Provide Permission?入口');return '真实Chrome拒绝请求后显示Important Tips和How Can I Provide Permission?，符合PC预期';}
    if(index===1)return suite.completePermissionGuide();
    const state=await this.allowFromSiteSettings(suite);if(!/重新加载此页面，即可在该网站上应用更新后的设置|Reload this page to apply your updated settings/i.test(state))throw new Blocked('尚未捕获PC更改站点权限后的重新加载提示，需要通过原生Chrome站点面板允许权限');const evidence=path.join(suite.batch,'G-12-chrome-reload.png');await fs.writeFile(evidence,await this.app.getScreenshot({emit:false}));suite.data.context.permission_evidence=[evidence];await this.click(/按钮 (重新加载|Reload)$/);suite.steps.push('Chrome顶部显示更新设置提示后点击重新加载');return 'Chrome显示重新加载此页面，即可在该网站上应用更新后的设置；已点击重新加载。原生截图：'+evidence;
  }
}

/** 从通用配置创建桌面套件；网址可覆盖，prod地址未配置时禁止猜测。 */
export async function createDesktopSuite(options){
  const config=JSON.parse(await fs.readFile(path.join(root,'web_ui_config.json'),'utf8'));
  const environment=options.environment||'test',url=options.url||config[environment+'_url'];
  if(!url)throw new Error('必须提供'+environment+'实际网址');
  if(!options.batch)throw new Error('必须指定独立Web批次目录');
  return new DesktopSuite({...options,url,environment}).init();
}
