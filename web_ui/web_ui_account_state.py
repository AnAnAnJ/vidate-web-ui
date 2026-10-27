"""仅用可见页面识别会员和新老用户，不依赖后台注册数据。"""
from dataclasses import asdict, dataclass
import re
import time


UNSUBSCRIBED_CASES = {
    'L-08', 'L-09', 'L-10', 'L-11', 'L-13',
    'L-27', 'L-28', 'L-29', 'L-30', 'N-10', 'S-01',
}
ACTIVE_MEMBER_CASES = {'S-02', 'S-03', 'S-04', 'S-05', 'S-06', 'S-07', 'S-09'}


class StateExcluded(Exception):
    """当前身份不适用的用例。"""


class StateUnconfirmed(Exception):
    """身份无法确认或测试会员已过期，阻塞关联用例。"""


@dataclass(frozen=True)
class Membership:
    state: str
    checked_at: float
    active_until: str = ''

    def to_dict(self):
        """将会员状态、采样时间和有效期转换成可持久化字典。"""
        return asdict(self)


def membership_from_profile(text, now=None):
    """仅根据实际Profile文案识别会员；样式或有效期不足时返回unknown。"""
    body = re.sub(r'\s+', ' ', text).strip()
    premium = 'Premium Member' in body
    upgrade = 'Upgrade to Premium' in body
    expiry = re.search(r'Active until\s*:?\s*(\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}(?:[ T]+\d{1,2}:\d{2}(?::\d{2})?)?)', body)
    # 日期可能只显示到天；不能用日期推算测试环境的秒级到期。
    state = 'subscribed' if premium and expiry and not upgrade else 'unsubscribed' if upgrade and not premium else 'unknown'
    return Membership(state, time.time() if now is None else now, expiry[1] if expiry else '')


def require_case_state(case_id, membership, environment):
    """校验用例所需会员状态，身份不适用则跳过，身份未知或需恢复则阻塞。"""
    expected = 'unsubscribed' if case_id in UNSUBSCRIBED_CASES else 'subscribed' if case_id in ACTIVE_MEMBER_CASES else None
    if expected is None:
        return
    if membership.state == 'unknown':
        raise StateUnconfirmed('Profile 会员样式或有效期未确认，停止身份关联动作')
    if membership.state != expected:
        message = f'{case_id} 要求 {expected}，刷新 Profile 实际为 {membership.state}'
        if environment == 'prod' or expected == 'unsubscribed':
            raise StateExcluded(message + '；按当前身份跳过')
        raise StateUnconfirmed(message + '；测试会员可能已满5分钟到期，不使用历史订阅状态继续执行')


def test_membership_due(environment, payment_confirmed_at, now, duration=300):
    """判断test付款后是否已达到会员有效时长；prod或缺少付款时间时返回False。"""
    return environment == 'test' and payment_confirmed_at is not None and now - payment_confirmed_at >= duration


def classify_login_window(login_at, observations, observed_until, chat_window_complete):
    """仅使用登录后30秒证据；teamoffice消息或Official非零消息角标表示新用户。"""
    deadline = login_at + 30
    if any(login_at <= when <= deadline and (
               'teamoffice' in re.sub(r'\s+', '', body).lower()
               or re.search(r'(?:^|\n)\s*[1-9]\d*\s*\n\s*Official\s*(?:\n|$)', body, re.I)
           )
           for when, body in observations):
        return 'new'
    if chat_window_complete and observed_until >= deadline:
        return 'existing'
    return 'unknown'
