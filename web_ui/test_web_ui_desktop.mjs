/** 桌面关键规则离线回归；不连接浏览器，不将这些结果算作UI实测通过。 */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {DesktopSuite,Blocked,Skipped,membership} from './web_ui_desktop_suite.mjs';

/** 创建无浏览器副作用的规则测试对象。 */
function suite(environment='test'){
  const value=new DesktopSuite({tab:{url:async()=> 'https://example.test/lobby/profile'},browser:{},batch:'/unused',url:'https://example.test',environment});
  value.data={context:{run_environment:environment},results:{},events:[]};value.save=async()=>{};return value;
}

test('会员卡片必须同时包含Premium Member和有效期，冲突时未知',()=>{
  assert.equal(membership('Upgrade to Premium').state,'unsubscribed');
  assert.equal(membership('Premium Member Active until 02-11-2026').state,'subscribed');
  for(const text of ['', 'Premium Member', 'Active until 02-11-2026','Premium Member Active until 02-11-2026 Upgrade to Premium'])assert.equal(membership(text).state,'unknown');
});

test('prod在任何页面动作前拦截支付和已订阅Video',async()=>{
  const value=suite('prod');value.profile=async()=>{throw Error('不应触发Profile');};
  for(const id of ['S-01','S-03','S-04','S-05','S-06','S-09'])await assert.rejects(value.guard(id),Skipped);
});

test('已订阅不能作为首次订阅或未订阅Say Hi边界前置',async()=>{
  const value=suite();value.profile=async()=>({state:'subscribed'});
  for(const id of ['S-01','L-08','L-09','N-10'])await assert.rejects(value.guard(id),Skipped);
});

test('会员状态未知时阻塞关联动作',async()=>{
  const value=suite();value.profile=async()=>({state:'unknown'});await assert.rejects(value.guard('L-09'),Blocked);
});

test('CUA选择器提前超时时继续只读等待，不重发业务动作',async()=>{
  const value=suite();let attempts=0,snapshots=0;
  value.tab.playwright={getByText:()=>({first:()=>({waitFor:async()=>{if(++attempts===1)throw Error('selector deadline exceeded');}})}),domSnapshot:async()=>{snapshots++;return '页面加载完成';}};
  await value.wait('Gems Store',1000);
  assert.equal(attempts,2);assert.equal(snapshots,1);
});

test('已有订单时不得再次购买或重读付款前置',async()=>{
  const value=suite();value.payments=async()=>({subscription:{state:'submitted'}});let checked=0;value.profile=async()=>{checked++;return {state:'unsubscribed'};};
  await assert.rejects(value.pay('subscription'),/原订单/);assert.equal(checked,0);
});

test('证据补采失败不会重发业务动作或丢失阻塞原因',async()=>{
  const value=suite();value.catalog.set('L-23',['L-23','个人资料','昵称',[],[]]);value.guard=async()=>{};let actions=0,captures=0;
  value.capture=async()=>{captures++;throw Error('截图失败');};const result=await value.run('L-23',async()=>{actions++;throw new Blocked('连接断开，动作结果未知');});
  assert.equal(actions,1);assert.equal(captures,1);assert.equal(result.status,'BLOCKED');assert.match(result.actual,/连接断开/);
});

test('Web不得恢复手机或其他网站批次',async()=>{
  const batch=await fs.mkdtemp(path.join(os.tmpdir(),'web-ui-isolation-'));
  const value=suite();value.batch=batch;
  for(const data of [{url:value.url,device:{platform:'ios'},context:{run_environment:'test'}},{url:'https://another.test/',device:{platform:'web'},context:{run_environment:'test'}}]){
    await fs.writeFile(path.join(batch,'report.json'),JSON.stringify(data));await assert.rejects(value.init(),/独立批次/);
  }
});

test('未知环境在初始化前拒绝',()=>{assert.throws(()=>suite('staging'),/test或prod/);});

test('PC游客通话补测发现已登录时不退出账号或启动匹配',async()=>{
  const value=suite();value.button=()=>({filter:()=>({count:async()=>1})});
  value.nav=async()=>assert.fail('已登录不能进入游客匹配');
  value.click=async()=>assert.fail('不能通过退出账号恢复游客前置');
  await assert.rejects(value.conversation('M'),/保留账号状态/);
});

test('PC六项专属移除保持隔离，S-10在所有端停用',async()=>{
  const batch=await fs.mkdtemp(path.join(os.tmpdir(),'web-ui-pc-scope-'));
  const value=await suite().init.call(Object.assign(suite(),{batch}));
  for(const id of ['L-14','N-01','L-20','L-11','G-11','L-05']){assert.equal(value.catalog.has(id),false);await assert.rejects(value.execute(id),Skipped);}
  assert.equal(value.catalog.has('S-10'),false);
  value.profile=async()=>assert.fail('停用用例不能读取会员或触碰页面');
  for(const action of [()=>value.execute('S-10'),()=>value.guard('S-10'),()=>value.billingCall(true)])await assert.rejects(action(),/免费一分钟停用/);
  assert.equal(value.catalog.size,57);
  assert.match(value.catalog.get('N-10')[4].join('；'),/匹配连接页/);
  assert.match(value.catalog.get('N-10')[4].join('；'),/余额足够时发送成功/);
  assert.match(value.catalog.get('N-02')[4].join('；'),/Enjoy/);
  assert.match(value.catalog.get('L-04')[2],/接听首次/);
  assert.match(value.catalog.get('L-25')[3].join('；'),/1.5秒/);
  assert.match(value.catalog.get('S-04')[3].join('；'),/清理当前被测源/);
  const original=JSON.parse(await fs.readFile(new URL('./web_ui_browser_cases.json',import.meta.url),'utf8'));
  assert.ok(original.some(c=>c[0]==='L-05'));assert.ok(!original.some(c=>c[0]==='S-10'));
  assert.match(original.find(c=>c[0]==='L-04')[3].join('；'),/取消/);
  assert.match(original.find(c=>c[0]==='N-02')[4].join('；'),/权限请求/);
  assert.match(original.find(c=>c[0]==='N-02')[3].join('；'),/Try Again/);
});

test('PC的N-02无需原生适配器，直接处理已显示的Enjoy并等待关闭',async()=>{
  const value=suite();let showing=true,hiddenChecked=false,readyChecked=false;
  value.text=async()=>showing?'Important Tips Allow Cam and Mic Enjoy':'Match Start matching';
  value.button=name=>({filter:()=>({count:async()=>Number(name==='Enjoy'&&showing||name==='Start matching'&&!showing),first:()=>({waitFor:async()=>{assert.equal(showing,false);readyChecked=true;}})})});
  value.click=async name=>{assert.equal(name,'Enjoy');showing=false;value.steps.push('点击 '+name);};
  value.tab.playwright={getByText:name=>name==='Start matching'?{filter:()=>({first:()=>({waitFor:async()=>{assert.equal(showing,false);readyChecked=true;}})})}:{first:()=>({waitFor:async options=>{assert.equal(options.state,'hidden');assert.equal(showing,false);hiddenChecked=true;}})}};
  value.run=async(id,action)=>action();
  assert.match(await value.execute('N-02'),/Enjoy后权限说明关闭/);
  assert.equal(hiddenChecked,true);assert.equal(readyChecked,true);
});

test('PC没有权限说明时N-02阻塞，不推测历史点击成功',async()=>{
  const value=suite();value.text=async()=> 'Match Start matching';
  value.button=()=>({filter:()=>({count:async()=>0})});
  value.click=async()=>assert.fail('没有引导不应点击');
  await assert.rejects(value.completePermissionGuide(),Blocked);
});

test('通用Free标识识别工具保留现场，S-10执行入口已全平台停用',async()=>{
  const value=suite();value.batch=await fs.mkdtemp(path.join(os.tmpdir(),'web-ui-free-evidence-'));
  value.discover=async()=>{};value.capture=async label=>[path.join(value.batch,label+'.png'),path.join(value.batch,label+'.txt')];
  value.tab.playwright={evaluate:async()=>[{index:0,text:'Test host Video 80/min'}]};
  value.button=()=>assert.fail('无Free候选不应拨号');
  const attempt={evidence:[]};
  await assert.rejects(value.dialBilling(true,attempt),/未拨号/);
  assert.equal(attempt.evidence.length,3);
  const record=JSON.parse(await fs.readFile(attempt.evidence[2],'utf8'));
  assert.deepEqual(record.selected,[]);assert.equal(record.targets[0].text,'Test host Video 80/min');
  assert.match(attempt.evidence[0],/free-call-discover\.png$/);
});

test('标签保存请求未完成时不连点，间隔1.5秒且刷新核对服务端保存结果',async()=>{
  const value=suite();value.batch=await fs.mkdtemp(path.join(os.tmpdir(),'web-ui-tags-'));
  let selected=new Set(),persisted=new Set(),pending=false;const clicks=[];let profiles=0;
  value.profile=async()=>{profiles++;selected=new Set(persisted);};
  const locator=label=>({evaluate:async()=>({html:selected.has(label)?'selected border-[#FF66D6]':'unselected',count:String(selected.size)}),
    click:async()=>{assert.equal(pending,false);pending=true;clicks.push(Date.now());selected.add(label);},
    locator:()=>({first:()=>({waitFor:async()=>assert.ok(selected.has(label))})})});
  value.tab.playwright={getByRole:(_,{name})=>locator(name.split(' ')[0]),getByText:text=>({waitFor:async()=>assert.equal(text,selected.size+'/6')})};
  value.site={profileSave:async action=>{await action();await new Promise(resolve=>setTimeout(resolve,20));persisted=new Set(selected);pending=false;return {status:200,completed_at:Date.now()};}};
  await value.tags();assert.equal(clicks.length,2);assert.ok(clicks[1]-clicks[0]>=1500);
  assert.equal(profiles,2);assert.deepEqual([...persisted],['Latex','Flirting']);
  const evidence=JSON.parse(await fs.readFile(value.data.context.tag_save_evidence['L-25'][0],'utf8'));
  assert.equal(evidence.length,2);assert.ok(evidence.every(item=>item.request.status===200));
});

test('没有请求或接口失败时停止下一次标签点击并保存现场',async()=>{
  for(const response of [null,{status:500}]){
    const value=suite();value.batch=await fs.mkdtemp(path.join(os.tmpdir(),'web-ui-tags-failed-'));value.profile=async()=>{};let clicks=0;
    const tag={evaluate:async()=>({html:'unselected',count:'0'}),click:async()=>clicks++};value.tab.playwright={getByRole:()=>tag};
    value.site={profileSave:async action=>{await action();if(!response)throw Error('无对应保存响应');return response;}};
    await assert.rejects(value.tags(),response?/HTTP 500/:Blocked);assert.equal(clicks,1);
    assert.equal(JSON.parse(await fs.readFile(value.data.context.tag_save_evidence['L-25'][0],'utf8')).length,1);
  }
});

test('S-04缓存清理或账号复核缺失时不消耗首次Video',async()=>{
  const value=suite();value.discover=async()=>assert.fail('不应点击Video');
  await assert.rejects(value.subscribedVideo('first'),/缓存清理/);assert.equal(value.data.context.first_subscribed_video,undefined);
  value.data.context.first_subscribed_video=true;value.site={clearSiteCache:async()=>assert.fail('首次已消耗不应清缓存')};
  await assert.rejects(value.subscribedVideo('first'),/已消耗/);
});

test('S-04缓存清理后账号不一致即阻塞，重新登录原账号后仅复核、不再次清理',async()=>{
  const value=suite();value.batch=await fs.mkdtemp(path.join(os.tmpdir(),'web-ui-cache-'));
  let clears=0,reads=0,uuid='other';value.capture=async label=>[path.join(value.batch,label+'.png')];value.profile=async()=>({state:'subscribed'});
  value.site={identity:async()=>({uuid:++reads===1?'original':uuid}),clearSiteCache:async({origin})=>{clears++;return {origin,cleared_at:Date.now()};}};
  await assert.rejects(value.prepareFirstVideo(),/UUID不一致/);assert.equal(clears,1);assert.equal(value.data.context.first_subscribed_video,undefined);
  uuid='original';await value.prepareFirstVideo();assert.equal(clears,1);assert.ok(value.data.context.first_video_cache.verified_at);
  assert.ok(value.data.context.first_video_cache.evidence.some(file=>file.endsWith('S-04-cache-preflight.json')));
});
