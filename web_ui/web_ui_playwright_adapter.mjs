/** 将独立Playwright页面转换为桌面套件接口，不改变CUA、Android或iOS执行器。 */

/** 转换两种驱动的超时参数，保留正则、函数和其他业务参数。 */
export function nativeOptions(value){
  if(!value||Object.getPrototypeOf(value)!==Object.prototype)return value;
  if(!Object.hasOwn(value,'timeoutMs'))return value;
  const {timeoutMs,...rest}=value;return {...rest,timeout:timeoutMs};
}

/** 保留同步定位器链，只对实际异步动作返回Promise。 */
export function wrapLocator(target){
  return new Proxy(target,{get(object,key){
    if(key==='then')return undefined;
    const value=object[key];if(typeof value!=='function')return value;
    return (...args)=>{
      const result=value.apply(object,args.map(nativeOptions));
      const wrap=v=>Array.isArray(v)?v.map(wrap):v&&typeof v.locator==='function'&&typeof v.click==='function'?wrapLocator(v):v;
      return result&&typeof result.then==='function'?result.then(wrap):wrap(result);
    };
  }});
}

/** 只匹配资料标签保存的真实XHR/fetch写请求，排除轮询、埋点及其他业务。 */
export function isProfileSaveRequest(request){
  const url=new URL(request.url());
  return ['xhr','fetch'].includes(request.resourceType())&&['POST','PUT','PATCH'].includes(request.method())&&
    /\/(?:accounts?|profiles?|users?)(?:\/|$)/i.test(url.pathname)&&
    /hobby|tags?|interests?/i.test(url.pathname+' '+(request.postData()||''));
}

/** 独立Chrome的请求观察和同源缓存清理，不借用手机驱动或浏览器全局清理。 */
export function desktopSite(page){
  /** 动作前开始监听，仅保存请求路径、HTTP状态和时刻，不保存认证头或请求正文。 */
  async function profileSave(action){
    const response=page.waitForResponse(value=>isProfileSaveRequest(value.request()),{timeout:8000});
    const [result]=await Promise.all([response,action()]);
    const error=await result.finished();if(error)throw Error('标签保存响应未完整接收');
    const url=new URL(result.url());
    return {url:url.origin+url.pathname,method:result.request().method(),status:result.status(),completed_at:Date.now()};
  }
  /** 核对标签当前源，防止清理或身份读取作用于第三方页面。 */
  function verifyOrigin(origin){if(new URL(page.url()).origin!==origin)throw Error('当前标签不是被测源');}
  /** 监听一次真实页面刷新，只提取用户信息主对象data.uuid，不主动重放接口。 */
  async function identity({origin,detailsPath}){
    verifyOrigin(origin);const session=await page.context().newCDPSession(page);
    try{
      await session.send('Network.enable');await session.send('Network.setCacheDisabled',{cacheDisabled:true});
      const response=page.waitForResponse(value=>new URL(value.url()).pathname.endsWith(detailsPath)&&value.request().resourceType()!=='document'&&value.status()===200,{timeout:10000});
      const [result]=await Promise.all([response,page.reload({waitUntil:'domcontentloaded'})]);
      verifyOrigin(origin);const body=await result.json(),uuid=body?.data?.uuid;
      if(typeof uuid!=='string'||!uuid.trim())throw Error('用户信息主对象data.uuid缺失，可能已退出登录');
      return {uuid};
    }finally{try{await session.send('Network.setCacheDisabled',{cacheDisabled:false});}finally{await session.detach();}}
  }
  /** 清理当前源Local/SessionStorage和资源缓存，保留设备键、认证Cookie及IndexedDB。 */
  async function clearSiteCache({origin,deviceKeys}){
    verifyOrigin(origin);const session=await page.context().newCDPSession(page);
    try{
      // 先校验存储中的设备值；冲突时不删除任何业务数据。
      const state=await page.evaluate(({origin,deviceKeys})=>{
        if(location.origin!==origin)throw Error('缓存清理源不一致');
        const retained=Object.fromEntries(deviceKeys.map(key=>[key,localStorage.getItem(key)]).filter(([,value])=>value!==null));
        const ids=Object.values(retained).map(value=>{try{return JSON.parse(value);}catch{return value;}});
        if(new Set(ids).size!==1||ids.some(value=>typeof value!=='string'||!value.trim()))throw Error('设备标识缺失或冲突，停止缓存清理');
        return {retained,localCount:localStorage.length,sessionCount:sessionStorage.length};
      },{origin,deviceKeys});
      await session.send('Storage.clearDataForOrigin',{origin,storageTypes:'cache_storage,service_workers'});
      const remaining=await page.evaluate(({origin,retained})=>{
        if(location.origin!==origin)throw Error('缓存清理前页面已切换域名');
        localStorage.clear();sessionStorage.clear();
        for(const [key,value] of Object.entries(retained))localStorage.setItem(key,value);
        return {localCount:localStorage.length,sessionCount:sessionStorage.length};
      },{origin,retained:state.retained});
      if(remaining.localCount!==Object.keys(state.retained).length||remaining.sessionCount!==0)throw Error('本站业务缓存清理未完成');
      return {origin,cleared_at:Date.now(),cleared:['local_storage_except_device_keys','session_storage','cache_storage','service_workers'],
        retained_device_keys:Object.keys(state.retained),retained_auth_storage:['cookies','indexeddb'],http_cache:'身份复核刷新时仅本标签禁用HTTP缓存'};
    }finally{await session.detach();}
  }
  return {profileSave,identity,clearSiteCache};
}

/** 为独立Chrome上下文建立稳定标签编号，并复用同一登录和支付弹窗跟踪逻辑。 */
export function standaloneBrowser(context){
  const ids=new WeakMap(),pages=new Map();let next=0;
  const identify=page=>{if(!ids.has(page)){ids.set(page,String(++next));pages.set(ids.get(page),page);}return ids.get(page);};
  /** 只封装指定页面，不切换用户其他Chrome窗口。 */
  function tab(page){
    const id=identify(page),playwright={};
    for(const name of ['locator','getByRole','getByText','getByLabel','getByPlaceholder','getByTestId','frameLocator'])playwright[name]=(...args)=>wrapLocator(page[name](...args));
    playwright.evaluate=(fn,arg)=>page.evaluate(fn,arg);
    playwright.domSnapshot=()=>page.locator('body').ariaSnapshot();
    return {id,playwright,webui:desktopSite(page),url:async()=>page.url(),goto:url=>page.goto(url,{waitUntil:'domcontentloaded'}),reload:()=>page.reload({waitUntil:'domcontentloaded'}),back:()=>page.goBack({waitUntil:'domcontentloaded'}),screenshot:options=>page.screenshot(options),close:()=>page.close(),
      scroll:async(point,direction,count=1)=>{await page.mouse.move(...point);await page.mouse.wheel(0,(direction==='up'?-1:1)*600*count);}};
  }
  return {tabs:{list:async()=>context.pages().map(p=>({id:identify(p),url:p.url()})),get:async id=>{const page=pages.get(id);if(!page||page.isClosed())throw Error('测试标签已关闭：'+id);return tab(page);},new:async()=>tab(await context.newPage())},wrap:tab};
}
