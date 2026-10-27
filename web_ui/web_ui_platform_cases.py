"""按平台筛选业务范围，PC变化不会修改Android/iOS的定义。"""
from web_ui_environment import configuration


def disabled_case_reason(case_id):
    """读取所有平台共同停用的用例备注，旧编号入口也必须在设备动作前停止。"""
    return configuration().get('disabled_cases',{}).get(case_id)


def platform_pause_reason(case_id, platform):
    """返回平台明确暂缓的原因；保留用例目录，仅在执行入口记录跳过。"""
    rules=configuration().get('platform_rules',{}).get(platform,{})
    return rules.get('pause_reason','用户要求本平台暂时跳过此项') if case_id in rules.get('paused_case_ids',[]) else None


def platform_cases(cases, platform):
    """应用平台专属排除和步骤覆盖，返回新目录而不改写共用原始列表。"""
    rules = configuration().get('platform_rules', {}).get(platform, {})
    excluded = set(rules.get('excluded_case_ids', []))
    excluded.update(configuration().get('disabled_cases',{}))
    overrides = rules.get('case_overrides', {})
    return [(cid, module, overrides.get(cid, {}).get('title', title), overrides.get(cid, {}).get('steps', list(steps)),
             overrides.get(cid, {}).get('expected', list(expected)))
            for cid, module, title, steps, expected in cases if cid not in excluded]
