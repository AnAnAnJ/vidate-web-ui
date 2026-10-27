/** 登录来电漏捕、计费边界及独立适配器的离线规则回归，不代替UI实测。 */
import test from 'node:test';
import assert from 'node:assert/strict';
import {DesktopSuite} from './web_ui_desktop_suite.mjs';
import {classifyLogin,classifyDesktopLogin,incomingControls,checkCallBilling} from './web_ui_observation.mjs';
import {nativeOptions,wrapLocator,isProfileSaveRequest,desktopSite} from './web_ui_playwright_adapter.mjs';
import {plan} from './web_ui_standalone/web_ui_run.mjs';
import vm from 'node:vm';

test('teamoffice只在本次登录后三十秒内作为新用户正证据',()=>{
  assert.equal(classifyLogin(100000,[{time:130000,healthy:true,text:'Team Office'}],130000,true),'new');
  assert.equal(classifyLogin(100000,[{time:130001,healthy:true,text:'Team Office'}],130001,true),'existing');
  assert.equal(classifyLogin(100000,[{time:101000,healthy:false,text:'teamoffice'}],130000,false),'unknown');
});

test('观察中断或尚未满三十秒不能以无消息判老用户',()=>{
  assert.equal(classifyLogin(0,[],29999,true),'unknown');
  assert.equal(classifyLogin(0,[],30000,false),'unknown');
  assert.equal(classifyLogin(0,[],30000,true),'existing');
});

test('PC的Official和欢迎来电正证据不因登录观察间断丢失，手机规则不变',()=>{
  assert.equal(classifyDesktopLogin(0,[{healthy:true,official_messages:2}],90000,false),'new');
  assert.equal(classifyDesktopLogin(0,[],90000,false,[{action:'cancel'}]),'new');
  assert.equal(classifyDesktopLogin(0,[{healthy:true,official_messages:0}],30000,true),'existing');
  assert.equal(classifyDesktopLogin(0,[],30000,false),'unknown');
  assert.equal(classifyLogin(0,[{time:1000,healthy:true,text:'Official 2'}],30000,false),'unknown');
});

test('同时识别Later/Join、Cancel/Answer，其他弹窗不误判为来电',()=>{
  assert.deepEqual(incomingControls('Lily is inviting you to private call.',['Later','Join']),{cancel:'Later',answer:'Join'});
  assert.deepEqual(incomingControls('Incoming video call',['Cancel','Answer']),{cancel:'Cancel',answer:'Answer'});
  assert.equal(incomingControls('Block this user?',['Cancel','Answer']),null);
});

test('分类未知时立即接听首次来电，只记录L-04与L-06且不重复接听',async()=>{
  let body='Lily is inviting you to private call.';const actions=[];
  const buttons={filter:()=>buttons,allTextContents:async()=>['Later','Join']};
  const tab={playwright:{getByRole:()=>buttons,locator:()=>({evaluateAll:async()=>[{ready:4,width:640}]})}};
  const value=new DesktopSuite({tab,browser:{},batch:'/unused',url:'https://example.test'});
  value.data={context:{login_classification:{state:'unknown',login_at:Date.now()/1000-1},login_observation:{incoming:[]}},results:{},events:[]};
  value.save=async()=>{};value.capture=async label=>[label+'.png'];value.text=async()=>body;
  value.click=async label=>{actions.push(label);body='Chats';};
  await value.pollIncoming();assert.deepEqual(actions,['Join']);
  const event=value.data.context.login_observation.incoming[0];assert.equal(event.closed,true);assert.equal(event.connected,true);assert.equal(event.evidence[0],'incoming-visible.png');
  const records=[];value.record=async(id,status)=>records.push({id,status});
  await value.finishIncoming();assert.deepEqual(records,[{id:'L-04',status:'PASS'},{id:'L-06',status:'SKIPPED_STATE'}]);
  body='Lily is inviting you to private call.';await value.pollIncoming();assert.deepEqual(actions,['Join']);
});

test('普通通话按分钟向上取整，Free不扣费',()=>{
  for(const [before,after,free,status] of [[400,320,false,'PASS'],[400,400,false,'FAIL'],[400,400,true,'PASS'],[400,320,true,'FAIL']])assert.equal(checkCallBilling({before,after,seconds:60,free}).status,status);
});

test('普通70秒扣160，不能按完整分钟80误判',()=>{
  assert.equal(checkCallBilling({before:280,after:120,seconds:70,free:false}).status,'PASS');
  assert.equal(checkCallBilling({before:280,after:200,seconds:70,free:false}).status,'FAIL');
});

test('短通话、跨两分钟和无效余额不能被一分钟断言误判通过',()=>{
  for(const seconds of [0,59,120])assert.equal(checkCallBilling({before:400,after:320,seconds,free:false}).status,'BLOCKED');
  assert.throws(()=>checkCallBilling({before:-1,after:0,seconds:60,free:false}),/无效/);
});

test('独立适配器保留同步定位器链并转换动作超时',async()=>{
  let options;const native={locator:()=>native,click:async value=>{options=value;},first:()=>native,count:async()=>2};
  const wrapped=wrapLocator(native);assert.equal(await wrapped.first().count(),2);
  await wrapped.first().click({timeoutMs:1200});assert.deepEqual(options,{timeout:1200});
  const pattern=/a/;assert.equal(nativeOptions(pattern),pattern);
});

test('独立计划可按编号找到实际源码，未知编号和prod默认域名被拦截',async()=>{
  const selected=await plan(['--plan','--cases','S-05,S-09']);assert.equal(selected.cases.length,2);
  assert.ok(selected.cases.every(c=>c.implementation.web.line>0&&c.implementation.android.line>0&&c.implementation.ios.line>0));
  await assert.rejects(plan(['--plan','--cases','S-999']),/未知用例/);
  await assert.rejects(plan(['--plan','--cases','L-05']),/PC已移除/);
  await assert.rejects(plan(['--plan','--cases','S-10']),/免费一分钟停用/);
  await assert.rejects(plan(['--plan','--env','prod','--cases','S-05']),/网址必须/);
});

test('标签请求匹配排除读取、埋点和其他账户写入',()=>{
  const request=(pathname,body,method='POST',type='fetch')=>({url:()=> 'https://api.example'+pathname,postData:()=>body,method:()=>method,resourceType:()=>type});
  assert.ok(isProfileSaveRequest(request('/web/v1/accounts/update','{"hobby_tag":[1]}')));
  assert.ok(isProfileSaveRequest(request('/web/v1/accounts/tags','', 'PATCH')));
  for(const item of [request('/web/v1/accounts/details','', 'GET'),request('/analytics/event','{"tags":[1]}'),request('/web/v1/accounts/update','{"nickname":"test"}'),request('/accounts/tags','', 'POST','document')])assert.equal(isProfileSaveRequest(item),false);
});

test('缓存清理限定当前源并保留设备键，不调用浏览器全局清理或清除认证库',async()=>{
  /** 在隔离JavaScript上下文执行页面脚本，不连接实际浏览器。 */
  const storage=initial=>{const items=new Map(Object.entries(initial));return {get length(){return items.size;},getItem:key=>items.get(key)??null,setItem:(key,value)=>items.set(key,value),clear:()=>items.clear()};};
  const localStorage=storage({client_id:'device-a',firstVideo:'seen'}),sessionStorage=storage({firstVideo:'seen'}),calls=[];
  const page={url:()=> 'https://example.test/lobby/profile',context:()=>({newCDPSession:async()=>({send:async(method,args)=>calls.push({method,args}),detach:async()=>{}})}),
    evaluate:async(fn,arg)=>vm.runInNewContext('('+fn.toString()+')(arg)',{location:{origin:'https://example.test'},localStorage,sessionStorage,arg})};
  const site=desktopSite(page);
  await assert.rejects(site.clearSiteCache({origin:'https://other.test',deviceKeys:['client_id']}),/不是被测源/);assert.equal(calls.length,0);
  const result=await site.clearSiteCache({origin:'https://example.test',deviceKeys:['client_id']});
  assert.equal(localStorage.getItem('client_id'),'device-a');assert.equal(localStorage.getItem('firstVideo'),null);assert.equal(sessionStorage.length,0);
  assert.deepEqual(calls,[{method:'Storage.clearDataForOrigin',args:{origin:'https://example.test',storageTypes:'cache_storage,service_workers'}}]);
  assert.ok(result.cleared_at);assert.deepEqual(result.retained_auth_storage,['cookies','indexeddb']);
});
