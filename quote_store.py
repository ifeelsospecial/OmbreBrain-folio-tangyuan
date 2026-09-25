"""引语（quotes）的归一化与校验（移植自上游 3.1.0 / 3.4.0 storage/quote_store.py）。

引语是**当时说出口、并且当时就知道它重要**的那几句话，原样存进桶的 frontmatter。
和本 fork 的 `source` 工具（系统自动存的整段对话原文）区别在**谁决定记住**：
source 是系统存全量；引语是写入那一刻挑出来的几句，决定权在写的人。

所以引语平时不返回：breath 浮现 / dream / catalog / feel 都读不到它，
只有 `breath_search(query, quotes=True)` 命中这条记忆时才原样附上。

上限存在的理由不是性能，是防止退化成"存原文"：每桶最多 3 条、每条最多 100 字，
**超限直接拒绝，不截断**——截断过的引语已经不是原话。

本 fork 适配：去掉上游依赖 You/them 的第三方署名分块渲染，render_quotes 简化为逐行原样输出。
"""
from __future__ import annotations

from typing import Any

MAX_QUOTES = 3
MAX_QUOTE_CHARS = 100
MAX_SPEAKER_CHARS = 40
MAX_AT_CHARS = 32


def normalize_quotes(value: Any) -> list[dict[str, str]]:
    """校验并去重桶 frontmatter 中的引语。

    接受两种写法，方便调用方少写一层结构：
    - ``["我不会走的", "你根本不懂"]``
    - ``[{"text": "我不会走的", "speaker": "她", "at": "2026-08-18"}]``

    返回归一化后的 ``[{"text": ..., "speaker": ..., "at": ...}]``，
    可选字段为空时不落进结果，避免 frontmatter 里堆一片空值。

    顺序保持输入顺序——说话是有先后的，重排会改变意思。
    """
    if value in (None, "", []):
        return []
    if not isinstance(value, list):
        raise ValueError("quotes 必须是列表")
    if len(value) > MAX_QUOTES:
        raise ValueError(
            f"引语最多 {MAX_QUOTES} 条（给了 {len(value)} 条）。"
            "「当时就知道重要」的话不会多——如果有很多句都想留下，"
            "那多半是想存原文，而原文层是只写不读的。"
        )

    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in value:
        if isinstance(item, str):
            item = {"text": item}
        if not isinstance(item, dict):
            raise ValueError("quotes 每项必须是字符串或对象")

        text = str(item.get("text") or "").strip()
        if not text:
            raise ValueError("quotes 每项必须有非空的 text")
        if len(text) > MAX_QUOTE_CHARS:
            raise ValueError(
                f"单条引语最多 {MAX_QUOTE_CHARS} 字（这条 {len(text)} 字）。"
                "引语是一句话，不是一段话；这里不会替你截断，"
                "因为截断过的引语已经不是原话了。"
            )

        speaker = str(item.get("speaker") or "").strip()[:MAX_SPEAKER_CHARS]
        at = str(item.get("at") or "").strip()[:MAX_AT_CHARS]

        key = (text, speaker)
        if key in seen:
            continue
        seen.add(key)

        entry: dict[str, str] = {"text": text}
        if speaker:
            entry["speaker"] = speaker
        if at:
            entry["at"] = at
        normalized.append(entry)

    return normalized


def quotes_from_metadata(metadata: dict | None) -> list[dict[str, str]]:
    """从桶 metadata 里宽容地读出引语；坏的那条跳过，好的照常返回。

    读取路径不该因为一条写坏的引语而整体失败——记忆本身比引语重要，
    而且磁盘上的 frontmatter 是可以被人手工编辑的。

    注意这里**逐条**抢救，不是整体 try/except：
    一条坏数据让整桶引语全部消失，那不叫宽容，那是把问题放大了。

    超量时取前 `MAX_QUOTES` 条。这是对已损坏数据的兜底，
    与写入路径「超限直接拒绝」不冲突——写入是我此刻的输入，该收到明确报错；
    读取面对的是既成事实，报错也改变不了磁盘上的内容。
    """
    if not isinstance(metadata, dict):
        return []
    raw = metadata.get("quotes")
    if not isinstance(raw, list):
        return []
    salvaged: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in raw:
        try:
            for quote in normalize_quotes([item]):
                key = (quote["text"], quote.get("speaker", ""))
                if key in seen:
                    continue
                seen.add(key)
                salvaged.append(quote)
        except ValueError:
            continue
        if len(salvaged) >= MAX_QUOTES:
            break
    return salvaged[:MAX_QUOTES]


def render_quotes(quotes: list[dict[str, str]]) -> str:
    """逐字渲染，不摘要不改写。"""
    lines = []
    for quote in quotes or []:
        tail = " · ".join(x for x in (quote.get("speaker", ""), quote.get("at", "")) if x)
        lines.append(f"「{quote['text']}」" + (f" —— {tail}" if tail else ""))
    return "\n".join(lines)
