#!/usr/bin/env node
/** 脱离Codex运行桌面Chrome用例；编号、DOM定位和报告格式复用桌面套件。 */
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {parseArgs} from 'node:util';
import {createDesktopSuite,Blocked} from '../web_ui_desktop_suite.mjs';
import {standaloneBrowser} from '../web_ui_playwright_adapter.mjs';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');

/** 校验输入编号和环境，列目录或计划时不会启动浏览器及修改测试数据。 */
export async function plan(args=process.argv.slice(2)){
  const {values}=parseArgs({args,options:{list:{type:'boolean'},plan:{type:'boolean'},cases:{type:'string'},env:{type:'string',default:'test'},url:{type:'string'},'batch-dir':{type:'string'},'user-data-dir':{type:'string'},mode:{type:'string',default:'incremental'},help:{type:'boolean'}}});
  if(!['test','prod'].includes(values.env)||!['full','incremental'].includes(values.mode))throw Error('环境只能为test/prod，模式只能为full/incremental');
  const index=JSON.parse(await fs.readFile(path.join(root,'web_ui_case_index.json'),'utf8'));
  const config=JSON.parse(await fs.readFile(path.join(root,'web_ui_config.json'),'utf8'));
  const excluded=new Set(config.platform_rules?.web?.excluded_case_ids||[]);
  if(values.help)return {help:true};
  const ids=values.cases?.split(',').map(v=>v.trim()).filter(Boolean)||[];
  if(new Set(ids).size!==ids.length)throw Error('用例编号重复，请每项只填写一次');
  for(const id of ids){if(config.disabled_cases?.[id])throw Error(id+'已在所有端停用：'+config.disabled_cases[id]);if(!index.some(c=>c.id===id))throw Error('未知用例编号：'+id);if(excluded.has(id))throw Error('PC已移除用例：'+id+'；Android/iOS仍保留');}
  const url=values.url||config[values.env+'_url'];
  if(!url&&!values.list)throw Error('prod网址必须由用户提供--url，不能推测');
  if(url&&(new URL(url).protocol!=='https:'||new URL(url).username||new URL(url).password))throw Error('网址必须是无凭据的完整HTTPS地址');
  const other=config[(values.env==='test'?'prod':'test')+'_url'];
  if(url&&other&&new URL(url).origin===new URL(other).origin)throw Error('环境与另一环境的域名相同，停止执行');
  const cases=(ids.length?ids.map(id=>index.find(c=>c.id===id)):index.filter(c=>!excluded.has(c.id)&&!config.disabled_cases?.[c.id])).map(c=>({...c,...config.platform_rules?.web?.case_overrides?.[c.id]}));
  return {values,url,cases,list:values.list,plan:values.plan};
}

/** 跟踪登录回调到来电窗口，进程持续运行而不是依赖模型下一轮回复。 */
async function login(suite){
  const snapshot=await suite.tab.playwright.domSnapshot();
  if(!/button "Log in"|Continue with Google/.test(snapshot)){await suite.execute('L-02');return;}
  if(!suite.data.context.login_watch_armed_at)await suite.armLoginObservation();
  if(!snapshot.includes('Continue with Google'))await suite.nav('Profile');
  process.stdout.write('登录观察已启动。请在Chrome点击Google并完成账号/协议确认；不要关闭本进程。\n');
  const deadline=Date.now()+300000;let classification;
  do{classification=await suite.observeLogin(20000);}while(!classification.completed&&Date.now()<deadline);
  if(!classification.completed)throw new Blocked('五分钟内未完成真实回调，保留检查点，不重新计时');
  if(suite.data.context.selected_case_ids.some(id=>['L-04','L-06'].includes(id))){
    let incoming;do{incoming=await suite.incoming(20000);}while(!incoming.completed);
  }
  await suite.execute('L-02');
}

/** 根据共享编号执行一次连续阶段，附带用例在同一通话或登录观察内记录。 */
async function executeCases(suite,ids){
  for(const id of ids){
    if(suite.data.results[id]){process.stdout.write(id+' 已有本批结果，保留；补测请使用新批次。\n');continue;}
    try{
      if(id==='L-01'){
        const cache=JSON.parse(await fs.readFile(path.join(root,'web_ui_agreement_cache.json'),'utf8'));
        const source=cache[new URL(suite.url).origin];
        if(source?.status!=='PASS'||!source.evidence?.length)throw new Blocked('没有同域名有效协议证据，需要先校验两份协议');
        for(const file of source.evidence)await fs.access(file);
        await suite.record(id,'PASS','复用同域名协议成果，来源时间'+new Date(source.stop).toISOString()+'；本次未重新打开',Date.now(),source.evidence);
        Object.assign(suite.data.results[id],{verification_mode:'reused',source_stop:source.stop});await suite.save();
      }else if(id==='L-02')await login(suite);
      else if(['L-04','L-06'].includes(id)){
        if(!suite.data.context.login_classification?.completed)throw new Blocked('缺少本批真实登录窗口；需从未登录状态运行L-02并连续选择来电用例');
        let state;do{state=await suite.incoming(20000);}while(!state.completed);
      }else if(['M-11','N-06'].includes(id))await suite.execute(id==='M-11'?'M-12':'N-07');
      else if(['M-14','N-09'].includes(id))await suite.execute(id==='M-14'?'M-13':'N-08');
      else await suite.execute(id);
    }catch(error){suite.steps=[];await suite.record(id,'BLOCKED',error.message,Date.now());}
    process.stdout.write(JSON.stringify(suite.data.results[id]||{case_id:id,status:'BLOCKED'},null,0)+'\n');
  }
}

/** 创建独立Chrome测试资料，不借用手机会话；完整test批次校验清理重置证据。 */
async function main(){
  const selected=await plan();
  if(selected.help){process.stdout.write('node web_ui_run.mjs --list | --plan --cases G-03,L-02 | --cases G-03,G-04 --batch-dir /独立批次 [--env test|prod --url https://域名 --mode incremental|full]\n');return;}
  if(selected.list||selected.plan){process.stdout.write(JSON.stringify(selected.cases,null,2)+'\n');return;}
  const {values,url,cases}=selected;if(!values.cases||!values['batch-dir'])throw Error('实际执行必须指定--cases和--batch-dir；--list查看编号与源码位置');
  const batch=path.resolve(values['batch-dir']);
  let preflight;
  if(values.env==='test'&&values.mode==='full'){
    preflight=JSON.parse(await fs.readFile(path.join(batch,'device-preflight.json'),'utf8'));
    if(preflight.status!=='reset_complete'||preflight.environment!=='test'||new URL(preflight.website).origin!==new URL(url).origin)throw Error('完整test批次缺少匹配的清理/重置前置；沿用数据补测使用--mode incremental');
  }
  const {chromium}=await import('playwright');
  const context=await chromium.launchPersistentContext(path.resolve(values['user-data-dir']||path.join(root,'web_ui_chrome_profile')),{channel:'chrome',headless:false,args:['--disable-save-password-bubble'],viewport:null});
  try{
    const browser=standaloneBrowser(context),pages=context.pages(),page=pages.find(p=>{try{return new URL(p.url()).origin===new URL(url).origin;}catch{return false;}})||pages[0]||await context.newPage();
    if(page.url()==='about:blank')await page.goto(url,{waitUntil:'domcontentloaded'});
    if(new URL(page.url()).origin!==new URL(url).origin)throw Error('Chrome当前前台域名与执行网址不同，停止避免测试其他网站');
    if(preflight){
      const config=JSON.parse(await fs.readFile(path.join(root,'web_ui_config.json'),'utf8'));
      const values=await page.evaluate(keys=>keys.map(key=>localStorage.getItem(key)).filter(Boolean),config.device_id_keys||['ios_deviceId','android_deviceId','client_id','deviceId']);
      const ids=[...new Set(values.map(value=>{try{return JSON.parse(value);}catch{return value;}}).filter(value=>typeof value==='string').map(value=>value.trim()).filter(Boolean))];
      if(ids.length!==1||ids[0]!==preflight.old_client_id||preflight.platform!=='web')throw Error('完整test批次的实际client_id或平台与重置证据不符，停止执行');
    }
    const permissions={execute:async()=>{throw new Blocked('独立Playwright不能证明Chrome原生权限弹窗；需人工留证或使用CUA原生权限适配器，不能用grantPermissions伪造实测');}};
    const suite=await createDesktopSuite({tab:browser.wrap(page),browser,batch,url,environment:values.env,permissions});
    suite.data.context.selected_case_ids=[...new Set([...(suite.data.context.selected_case_ids||Object.keys(suite.data.results)),...cases.map(c=>c.id)])];suite.data.context.incremental_without_reset=values.mode==='incremental';suite.data.device.version=await context.browser()?.version()||'系统Chrome';suite.data.context.runner='standalone-playwright';await suite.save();
    await executeCases(suite,cases.map(c=>c.id));
    const results=cases.map(c=>suite.data.results[c.id]);
    process.exitCode=results.some(r=>r?.status==='FAIL')?1:results.some(r=>!r||r.status==='BLOCKED')?2:0;
  }finally{await context.close();}
}

if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url))main().catch(error=>{process.stderr.write(error.message+'\n');process.exitCode=1;});
