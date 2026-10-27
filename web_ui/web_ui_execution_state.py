"""保存支付及等待检查点；同一设备的阶段执行互斥，防止中断后重复操作。"""
import fcntl
import hashlib
import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from web_ui_account_state import StateUnconfirmed


def atomic_json(path, data):
    """先写同目录临时文件再原子替换，进程中断时保留上一份完整记录。"""
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,delete=False) as stream:
        temporary=Path(stream.name)
        json.dump(data,stream,ensure_ascii=False,indent=2)
        stream.flush();os.fsync(stream.fileno())
    temporary.replace(path)


@contextmanager
def exclusive_file(path):
    """以非阻塞文件锁保护执行；锁由操作系统随进程退出释放，无需删除锁文件。"""
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+') as stream:
        try:fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise StateUnconfirmed('同一设备或记录已有执行进程，停止重复启动；不抢占现有任务。') from None
        try:yield
        finally:fcntl.flock(stream,fcntl.LOCK_UN)


def device_run(session_file):
    """按平台及UDID锁定手机，两个批次也不能同时操作同一设备。"""
    path=Path(session_file);connection=json.loads(path.read_text())
    device=connection.get('device',{})
    if not device.get('udid') or device.get('platform') not in {'android','ios'}:
        raise StateUnconfirmed('连接记录缺少真实平台或设备标识，不能启动阶段。')
    identity=device['platform']+':'+device['udid']
    key=hashlib.sha256(identity.encode()).hexdigest()[:24]
    return exclusive_file(path.parent/'web_ui_locks'/(key+'.lock'))


class PaymentJournal:
    """每批每种支付只建立一次记录；未知结果保持阻塞，不通过重付补证据。"""
    def __init__(self, batch):
        """将支付记录绑定独立批次，不修改历史批次或网站状态。"""
        self.path=Path(batch)/'payment-attempts.json'

    def read(self, kind):
        """读取已保存的单笔支付，不访问页面或触发支付。"""
        data=json.loads(self.path.read_text()) if self.path.exists() else {}
        return data.get(kind)

    def begin(self, kind, before, coins):
        """在打开购买流程前落盘；已有任何状态都禁止新建第二笔。"""
        if kind not in {'subscription','coin'} or before<0 or coins<=0:
            raise ValueError('支付种类、原余额或承诺金币不合法')
        with exclusive_file(self.path.with_suffix('.lock')):
            data=json.loads(self.path.read_text()) if self.path.exists() else {}
            if kind in data:raise StateUnconfirmed('本批已有'+kind+'支付记录，只能补核验，禁止重复付款。')
            item={'state':'prepared','before':before,'coins':coins,'created_at':time.time()}
            data[kind]=item;atomic_json(self.path,data)
            return item

    def update(self, kind, **fields):
        """按单向状态保存提交、成功和核验节点，拒绝重复提交或倒退。"""
        with exclusive_file(self.path.with_suffix('.lock')):
            data=json.loads(self.path.read_text()) if self.path.exists() else {}
            if kind not in data:raise StateUnconfirmed('支付前缺少已持久化的准备记录，停止提交。')
            item=data[kind]
            target=fields.get('state',item['state'])
            allowed={'prepared':{'submitted'},'submitted':{'confirmed'},'confirmed':{'verified'},'verified':set()}
            if 'state' in fields and target not in allowed[item['state']]:
                raise StateUnconfirmed('支付状态不允许重复提交或倒退：'+item['state']+' → '+target)
            item.update(fields,updated_at=time.time());atomic_json(self.path,data)
            return item
