/** 登录窗口和通话计费的纯规则；CUA及独立运行入口使用同一判断。 */

/** 只使用真实登录后三十秒内的有效Chat采样，新用户正证据优先。 */
export function classifyLogin(loginAt,samples,observedUntil,complete){
  if(samples.some(s=>s.time>=loginAt&&s.time<=loginAt+30000&&s.healthy&&/team\s*office/i.test(s.text)))return 'new';
  return complete&&observedUntil>=loginAt+30000?'existing':'unknown';
}

/** PC按Official消息或本次欢迎来电正证据判新用户，手机三十秒规则保持不变。 */
export function classifyDesktopLogin(loginAt,samples,observedUntil,complete,incoming=[]){
  if(incoming.length||samples.some(s=>s.healthy&&(s.official_messages>0||/team\s*office/i.test(s.text))))return 'new';
  return complete&&observedUntil>=loginAt+30000?'existing':'unknown';
}

/** 来电文案与接听/拒接操作同时出现才视为来电，避免把其他Cancel误判。 */
export function incomingControls(text,labels){
  if(!/inviting you to (?:a )?private call|incoming (?:video |private )?call|来电/i.test(text))return null;
  const cancel=['Later','Cancel','Decline','Reject'].find(v=>labels.includes(v));
  const answer=['Join','Answer','Accept'].find(v=>labels.includes(v));
  return cancel&&answer?{cancel,answer}:null;
}

/** 校验一分钟计费或免费规则，缺少完整计时证据不能按余额变化猜测。 */
export function checkCallBilling({before,after,seconds,free,rate=80}){
  if(!Number.isInteger(before)||!Number.isInteger(after)||before<0||after<0)throw new Error('金币余额格式无效');
  if(seconds<60||seconds>=120)return {status:'BLOCKED',actual:'未取得一分钟且不足两分钟的完整通话计时证据：'+seconds+'秒'};
  const expected=free?0:Math.ceil(seconds/60)*rate,actual=before-after;
  return {status:actual===expected?'PASS':'FAIL',actual:`${free?'Free免费':'普通'}通话${seconds}秒，金币${before}→${after}，实际扣除${actual}，预期扣除${expected}`};
}
