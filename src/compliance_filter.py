"""
阿里云内容安全 (Green) TextModerationPlus 文本合规过滤。

用于推送邮件前过滤可能触发阿里云邮件审核拦截的违规内容，避免整封邮件发送失败。

环境变量：
    INBRIEF_ALIYUN_AK              主账号或子账号 AccessKey ID
    INBRIEF_ALIYUN_SK              对应 AccessKey Secret
    INBRIEF_GREEN_ENDPOINT         可选，默认 green-cip.cn-shanghai.aliyuncs.com
    INBRIEF_GREEN_SERVICE          可选，默认 comment_detection (TextModeration 普通版通用场景)

未配置或 SDK 缺失时，函数返回 safe=True 并打 warning，不阻断业务。
"""
import json
import logging
import os
from typing import Optional, Tuple

_client = None
_client_init_failed = False


def _get_client():
    global _client, _client_init_failed
    if _client is not None or _client_init_failed:
        return _client

    ak = os.environ.get('INBRIEF_ALIYUN_AK')
    sk = os.environ.get('INBRIEF_ALIYUN_SK')
    if not ak or not sk:
        logging.warning("Compliance filter: INBRIEF_ALIYUN_AK/SK not set, skipping moderation")
        _client_init_failed = True
        return None

    try:
        from alibabacloud_green20220302.client import Client
        from alibabacloud_tea_openapi import models as open_api_models
    except ImportError:
        logging.warning("Compliance filter: alibabacloud_green20220302 SDK not installed, skipping moderation")
        _client_init_failed = True
        return None

    endpoint = os.environ.get('INBRIEF_GREEN_ENDPOINT', 'green-cip.cn-shanghai.aliyuncs.com')
    config = open_api_models.Config(access_key_id=ak, access_key_secret=sk, endpoint=endpoint)
    _client = Client(config)
    return _client


def check_text(text: str) -> Tuple[bool, Optional[str]]:
    """
    检查单段文本是否合规。

    Returns:
        (safe, labels)
        safe=True 表示通过；safe=False 表示命中违规，labels 为命中标签字符串。
        SDK 未配置 / 调用异常时返回 (True, None) 以避免阻断业务，但会打 warning。
    """
    if not text or not text.strip():
        return True, None

    client = _get_client()
    if client is None:
        return True, None

    try:
        from alibabacloud_green20220302 import models as green_models
        # 改用普通版 TextModeration (text_standard 单计费 0.00075/次)，
        # 不再用 Plus（Plus 会额外触发 text_llm_basic 0.00125/次，单次合计 0.002）。
        # comment_detection 是普通版 TextModeration 的通用文本场景，覆盖政治/广告/低俗/辱骂/涉恐等
        # (nlp_detection 只在 Plus 版才可用，普通版会报 service is invalid)
        service = os.environ.get('INBRIEF_GREEN_SERVICE', 'comment_detection')
        # comment_detection 文档限 600 字符，emoji / 4 字节 unicode 可能按 2 计长，保守用 500 切片
        max_len = 500
        chunks = [text[i:i + max_len] for i in range(0, len(text), max_len)] or [text]
        for idx, snippet in enumerate(chunks):
            request = green_models.TextModerationRequest(
                service=service,
                service_parameters=json.dumps({'content': snippet}),
            )
            response = client.text_moderation(request)
            body = response.body
            if body.code != 200:
                logging.warning(
                    f"Compliance filter: API returned code={body.code} msg={body.message} "
                    f"(chunk {idx+1}/{len(chunks)}, chunk_len={len(snippet)})"
                )
                continue
            data = body.data
            # body.data 在不同 SDK 版本可能是 dict 也可能是对象，统一抽字段
            def _pick(obj, key):
                if obj is None:
                    return None
                if isinstance(obj, dict):
                    return obj.get(key)
                return getattr(obj, key, None)

            labels_str = (_pick(data, 'labels') or '').strip()
            risk_level = (_pick(data, 'riskLevel') or '').strip()
            reason = (_pick(data, 'reason') or '').strip()
            # 阿里云返回的 riskLevel 实测可能是英文 (high/medium/low/none)
            # 也可能是中文 (高风险/中风险/低风险/无风险)，全部覆盖。
            # 只拦高风险：中/低风险也都带 label 且常误杀正常内容，故只看 riskLevel，不再用 labels 判定。
            rl_lower = risk_level.lower()
            high_risk_values = {'high', '高风险'}
            hit = rl_lower in high_risk_values or risk_level in high_risk_values
            # debug 用：把每次调用的关键字段记下来，便于核对后台
            logging.info(
                f"Compliance filter raw: chunk {idx+1}/{len(chunks)} "
                f"labels={labels_str!r} riskLevel={risk_level!r} reason={reason!r} hit={hit}"
            )
            if hit:
                detail = labels_str or f'riskLevel={risk_level}' or reason
                return False, detail
        return True, None
    except Exception as e:
        logging.warning(f"Compliance filter: moderation call failed, defaulting to safe. err={e}")
        return True, None


def check_summary(title_en: str = '', title_zh: str = '',
                  summary_en: str = '', summary_zh: str = '',
                  long_summary_en: str = '', long_summary_zh: str = '') -> Tuple[bool, Optional[str]]:
    """
    检查 LLM 生成的标题 + 短摘要 + 长摘要是否合规。命中违规即判定整篇不安全。
    用于文章入库前的过滤。原文不审（不对外展示）。
    只审中文版本——阿里云审核对中文识别更准，且中英文内容同源，审一种即可。
    """
    combined = '\n'.join(f for f in [title_zh, summary_zh, long_summary_zh] if f)
    safe, labels = check_text(combined)
    preview = (title_zh or title_en or '')[:40]
    if safe:
        logging.info(f"Compliance filter PASS [{preview}] len={len(combined)}")
    else:
        logging.warning(f"Compliance filter BLOCK [{preview}] labels={labels}")
    return safe, labels
