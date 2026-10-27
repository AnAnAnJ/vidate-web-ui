/** 通过CUA原生DevTools观察当前桌面Chrome，不连接CDP或读取认证信息。 */
import fs from 'node:fs/promises';
import path from 'node:path';

/** 从最新原生树定位唯一控件；每次操作后重新读取，避免复用过期编号。 */
function elementId(tree,pattern){
  const matches=tree.split('\n').filter(line=>pattern.test(line));
  if(matches.length!==1)throw Error('原生控件不唯一：'+pattern+'，数量='+matches.length);
  return Number(matches[0].match(/^\s*(\d+)\s/)[1]);
}

/** 仅用于已经打开Network面板的本轮被测标签；缺少证据时停止，不猜测请求成功。 */
export function cuaProfileSave({tab,app,batch,endpoint}){
  let sequence=0;
  /** 点击前清日志，点击后等待保存请求HTTP响应及完成时间，并保存不含认证头的证据。 */
  return async function profileSave(action){
    const target=new URL(endpoint),pageOrigin=new URL(await tab.url()).origin;
    let tree=await app.getAXState({emit:false,disableDiffing:true});
    if(!tree.includes('DevTools - '+pageOrigin.replace('https://',''))||!/标签 \(selected\) 网络/.test(tree))throw Error('当前原生窗口不是被测页Network面板');
    await app.click(elementId(tree,/文本输入区.*过滤/));
    await app.pressKey('super+a');await app.typeText(target.pathname);
    tree=await app.getAXState({emit:false,disableDiffing:true});
    await app.click(elementId(tree,/按钮 清除网络日志/));
    await app.getAXState({emit:false});
    const clickedAt=Date.now();await action();
    const deadline=Date.now()+8000;let status,duration;
    do{
      tree=await app.getAXState({emit:false,disableDiffing:true});
      const lines=tree.split('\n'),start=lines.findIndex(line=>line.includes('单元格 '+endpoint));
      if(start>=0){
        const next=lines.findIndex((line,index)=>index>start&&/\s\d+ row(?:\s|$)/.test(line));
        const row=lines.slice(start,next<0?undefined:next).join('\n');
        status=Number(row.match(/单元格 (\d{3})\b/)?.[1]);
        duration=row.match(/文本 ([\d,.]+\s*(?:ms|s))\s*$/m)?.[1];
        if(status&&duration)break;
      }
    }while(Date.now()<deadline);
    if(!status||!duration)throw Error('未捕获保存请求的HTTP状态及完成时间');
    const stem=path.join(batch,'L-25-network-'+(++sequence));
    await fs.writeFile(stem+'.txt',tree);
    await fs.writeFile(stem+'.png',await app.getScreenshot({emit:false}));
    return {url:endpoint,status,duration,clicked_at:clickedAt,completed_at:Date.now(),evidence:[stem+'.txt',stem+'.png']};
  };
}

/** 原生Network监听真实页面刷新，只读取用户信息主对象UUID，不读取或重放认证头。 */
export function cuaAccountIdentity({tab,app,batch,detailsPath}){
  let sequence=0;
  /** 使用本轮页面请求核对账号；只保存主UUID及接口路径，不保存可能包含认证信息的响应截图。 */
  return async function identity({origin}){
    if(new URL(await tab.url()).origin!==origin)throw Error('用户信息读取源不一致');
    let tree=await app.getAXState({emit:false,disableDiffing:true});
    if(!tree.includes('DevTools - '+origin.replace('https://','')))throw Error('原生DevTools不是当前被测源');
    if(!/标签 \(selected\) 网络/.test(tree)){
      await app.click(elementId(tree,/标签 .*网络, Value: 0/));
      tree=await app.getAXState({emit:false,disableDiffing:true});
    }
    await app.click(elementId(tree,/文本输入区.*过滤/));await app.pressKey('super+a');await app.typeText(detailsPath);
    tree=await app.getAXState({emit:false,disableDiffing:true});
    await app.click(elementId(tree,/按钮 清除网络日志/));await app.getAXState({emit:false});
    await tab.reload();const end=Date.now()+10000;let request;
    do{
      tree=await app.getAXState({emit:false,disableDiffing:true});
      request=tree.split('\n').filter(line=>line.includes('单元格 https://')&&line.includes(detailsPath)).at(-1);
      if(request&&/单元格 200 OK/.test(tree))break;
    }while(Date.now()<end);
    if(!request)throw Error('没有捕获真实用户信息请求');
    await app.click(Number(request.match(/^\s*(\d+)/)[1]));
    tree=await app.getAXState({emit:false,disableDiffing:true});
    if(!/标签 \(selected\) 响应/.test(tree)){
      await app.click(elementId(tree,/标签 .*响应, Value: 0/));
      tree=await app.getAXState({emit:false,disableDiffing:true});
    }
    const renderDeadline=Date.now()+4000;let uuid;
    do{
      const lines=tree.split('\n'),data=lines.findIndex(line=>/text "data"\s*:\s*\{/.test(line));
      uuid=data<0?null:lines.slice(data+1).find(line=>/text "uuid"\s*:\s*"/.test(line))?.match(/"uuid"\s*:\s*"([^"]+)"/)?.[1];
      if(uuid)break;
      tree=await app.getAXState({emit:false,disableDiffing:true});
    }while(Date.now()<renderDeadline);
    if(!uuid)throw Error('当前用户信息主对象data.uuid缺失');
    const stem=path.join(batch,'S-04-identity-'+(++sequence));
    const summary={origin,detailsPath,uuid,observed_at:Date.now()};
    await fs.writeFile(stem+'.json',JSON.stringify(summary,null,2));
    await fs.writeFile(stem+'.txt','用户信息响应主对象\ndata.uuid = '+uuid+'\n请求路径 = '+detailsPath);
    return {...summary,evidence:[stem+'.json',stem+'.txt']};
  };
}
