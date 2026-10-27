#!/usr/bin/env node
/** 监听手机真实用户信息响应，仅保存主层UUID及昵称，不保存凭据或完整响应。 */
import fs from 'node:fs/promises';

/** 解析显式参数，避免固定被测网站或手机。 */
function argument(name) {
  const index = process.argv.indexOf(name);
  if (index < 0 || !process.argv[index + 1]) throw Error(`缺少参数 ${name}`);
  return process.argv[index + 1];
}

const endpoint = argument('--cdp-url');
const expectedOrigin = new URL(argument('--url')).origin;
const expectedName = argument('--nickname');
const output = argument('--output');
const ws = new WebSocket(endpoint);
const pending = new Map();
const requests = new Map();
const captures = [];
let sequence = 0;

/** 为CDP请求设置超时，出错时不输出响应正文。 */
function send(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++sequence;
    const timer = setTimeout(() => { pending.delete(id); reject(Error(`${method} 超时`)); }, 10000);
    pending.set(id, { resolve, reject, timer });
    ws.send(JSON.stringify({ id, method, params }));
  });
}

/** 只读取用户主对象data.uuid，排除hobby_tag等嵌套业务对象的UUID。 */
function userIdentity(body, source) {
  const data = body.data;
  if (!data || typeof data.uuid !== 'string' || !/^\d+$/.test(data.uuid)) throw Error('用户主层UUID格式不明确');
  const nickname = data.nickname ?? data.nick_name ?? data.nickName ?? data.name;
  if (nickname !== expectedName) throw Error('接口昵称与当前Profile不一致，停止关联后台操作');
  return { ...source, uuid: data.uuid, nickname, is_member: data.is_member,
    member_expired_at: data.member_expired_at, json_path: 'response.data.uuid',
    site_origin: expectedOrigin, captured_at: new Date().toISOString(), source: '手机Chrome实际用户信息接口响应' };
}

/** 消费网络事件并在响应加载完成后提取最小身份凭据。 */
async function receive(event) {
  const message = JSON.parse(event.data);
  if (message.id) {
    const request = pending.get(message.id);
    if (!request) return;
    pending.delete(message.id); clearTimeout(request.timer);
    if (message.error) request.reject(Error(message.error.message)); else request.resolve(message.result);
    return;
  }
  if (message.method === 'Network.responseReceived') {
    const response = message.params.response;
    const url = new URL(response.url);
    if (url.pathname === '/vidate/web/v1/accounts/details' && response.status === 200)
      requests.set(message.params.requestId, { interface: url.origin + url.pathname, status: response.status });
  }
  if (message.method === 'Network.loadingFinished' && requests.has(message.params.requestId)) {
    const source = requests.get(message.params.requestId);
    requests.delete(message.params.requestId);
    try {
      const response = await send('Network.getResponseBody', { requestId: message.params.requestId });
      const body = JSON.parse(response.base64Encoded ? Buffer.from(response.body, 'base64').toString() : response.body);
      captures.push(userIdentity(body, source));
    } catch (error) {
      // 同一请求的旧事件可能已被浏览器释放；不记录响应数据，等待本次刷新响应。
      if (!error.message.includes('No resource with given identifier')) throw error;
    }
  }
}

/** 核对手机域名，监听一次真实刷新，并保存唯一当前用户身份。 */
async function main() {
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(Error('连接手机CDP超时')), 10000);
    ws.onopen = () => { clearTimeout(timer); resolve(); };
    ws.onerror = () => { clearTimeout(timer); reject(Error('连接手机CDP失败')); };
  });
  let captureError;
  ws.onmessage = event => receive(event).catch(error => { captureError = error; });
  const location = await send('Runtime.evaluate', { expression: 'location.origin', returnByValue: true });
  if (location.result.value !== expectedOrigin) throw Error('手机当前页面域名与批次不符');
  await send('Network.enable');
  await send('Page.reload');
  const deadline = Date.now() + 15000;
  while (!captures.length && !captureError && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 100));
  if (captureError) throw captureError;
  const unique = [...new Map(captures.map(item => [item.uuid, item])).values()];
  if (unique.length !== 1) throw Error('未捕获唯一用户主层UUID');
  await fs.writeFile(output, JSON.stringify(unique[0], null, 2));
  console.log(JSON.stringify(unique[0]));
}

try { await main(); } finally { ws.close(); }
