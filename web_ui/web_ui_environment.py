"""测试环境选择和操作准入；新增用例复用这里的规则。"""
from dataclasses import dataclass
import json
from pathlib import Path
from urllib.parse import urlsplit

CONFIG_PATH = Path(__file__).with_name('web_ui_config.json')


def configuration():
    """读取通用网址和执行规则配置，不在代码内固定被测站点。"""
    return json.loads(CONFIG_PATH.read_text(encoding='utf-8'))


TEST_URL = configuration()['test_url']
PROD_EXCLUDED = {
    'account_cleanup': '删除关联测试账号',
    'device_reset': '重置设备标识',
    'subscription_payment': '订阅支付',
    'coin_payment': '金币支付',
    'subscribed_video': '已登录且已订阅用户的 Video',
    'membership_extension': '后台恢复测试会员',
}


class EnvironmentExcluded(Exception):
    """操作按环境规则跳过，不能记作失败或已执行。"""


def origin(url):
    """校验完整HTTPS地址并返回规范化域名，拒绝含凭据或无效协议的地址。"""
    parts = urlsplit(url)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password:
        raise ValueError('被测地址必须为不含凭据的完整 HTTPS 地址')
    return f'https://{parts.netloc.lower()}'


def environment_plan(environment):
    """返回test/prod操作范围，供执行拦截及报告展示共用。"""
    if environment not in ('test', 'prod'):
        raise ValueError('环境只能是 test 或 prod')
    return {
        'environment': environment,
        'operations': {key: {'name': name, 'execute': environment == 'test'}
                       for key, name in PROD_EXCLUDED.items()},
        'other_ui_cases': '登录后先读取 Profile 实际会员状态；已订阅跳过强关联未订阅项，未知状态阻塞关联项',
        'agreements': '同一域名已校验通过则复用；明确复检或更换域名后再检查',
    }


@dataclass(frozen=True)
class ExecutionPolicy:
    environment: str
    base_url: str

    @classmethod
    def create(cls, environment='test', url=None):
        """根据环境及可选网址构造执行策略，缺失线上网址或环境域名冲突时停止。"""
        environment_plan(environment)
        settings = configuration()
        target = url or settings.get(environment + '_url')
        if not target:
            raise ValueError('执行 prod 用例必须显式提供 --url，不能猜测线上域名')
        target_origin = origin(target)
        other_url = settings.get('prod_url' if environment == 'test' else 'test_url')
        if other_url and target_origin == origin(other_url):
            raise ValueError('所选环境与配置中另一环境的域名相同，停止执行')
        return cls(environment, target_origin + '/')

    def verify_url(self, actual_url):
        """核对手机实际网址与所选环境域名一致，避免误测其他站点。"""
        if origin(actual_url) != origin(self.base_url):
            raise ValueError('手机前台网页域名与所选环境地址不一致，停止执行')

    def require(self, operation):
        """在动作前检查环境限制；prod禁止的操作抛出环境跳过异常。"""
        if self.environment == 'prod' and operation in PROD_EXCLUDED:
            raise EnvironmentExcluded(f'prod 环境按规则跳过：{PROD_EXCLUDED[operation]}')

    def execute(self, operation, action):
        """通过环境准入检查后才调用action，返回其结果。"""
        self.require(operation)
        return action()
