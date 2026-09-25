"""写入后自动建立桶间关系（移植自上游 3.2.0 tools/_relation_link.py）。

**为什么模型不感知这件事**：关联不是一个决定，是一个结果。涉及同一个人、同一个地方、
同样的感受，它们本来就连着，只是被**发现**连着。所以交给后端，模型不需要调工具去"连"。

**为什么不调 LLM**：写入路径上加 LLM 会拖慢 hold、多一个会失败的外部依赖；relation 只是
hint，规则 + 向量足够。

**为什么是 fire-and-forget**：关系建不出来不该让记忆写入失败。任何异常只记日志、绝不上抛。

本 fork 适配：上游通过 `_runtime` 取全局对象，这里改为显式传入 bucket_mgr / embedding_engine。
"""
from __future__ import annotations

import logging
from datetime import datetime

from relation_store import (
    AUTO_MAX_LINKS_PER_BUCKET,
    AUTO_RELATED_MIN_SCORE,
    infer_auto_relation_type,
    merge_auto_links,
    normalize_relation_links,
    reverse_relation_type,
)
from utils import parse_iso_datetime

logger = logging.getLogger("ombre_brain.relation")

# plan 是待办、feel 是感受、letter 是信、i 是自我认知——各自成层，横向连起来只会制造噪音。
_EXCLUDED_TYPES = frozenset({"plan", "feel", "letter", "i", "i_candidate", "identity", "archived", "trashed"})

# 候选比上限大一截：类型排除、已存在关系、低于门槛都会过滤掉不少。
_SEARCH_TOP_K = 24


def _bucket_type(meta: dict) -> str:
    return str((meta or {}).get("type") or "dynamic").strip().lower()


def _created_at(meta: dict) -> datetime | None:
    raw = str((meta or {}).get("created") or "").strip()
    if not raw:
        return None
    try:
        return parse_iso_datetime(raw)
    except (TypeError, ValueError):
        return None


def _hours_apart(left: dict, right: dict) -> float | None:
    a, b = _created_at(left), _created_at(right)
    if a is None or b is None:
        return None
    return abs((a - b).total_seconds()) / 3600.0


def _eligible(meta: dict) -> bool:
    if _bucket_type(meta) in _EXCLUDED_TYPES:
        return False
    return not (meta.get("deleted_at") or meta.get("tombstone"))


async def infer_links_for(bucket_mgr, embedding_engine, bucket_id: str, content: str) -> list[dict]:
    """为一个新桶推断该建立的关系，不写盘（推断与落库分开，阈值行为可单测）。"""
    bucket_id = str(bucket_id or "").strip()
    if not bucket_id or not str(content or "").strip():
        return []
    if not embedding_engine or not getattr(embedding_engine, "enabled", False):
        return []

    pairs = await embedding_engine.search_similar(content, top_k=_SEARCH_TOP_K)
    if not pairs:
        return []

    source = await bucket_mgr.get(bucket_id)
    if not source or not _eligible(source.get("metadata") or {}):
        return []
    source_meta = source.get("metadata") or {}

    inferred: list[dict] = []
    for target_id, score in pairs:
        target_id = str(target_id or "").strip()
        if not target_id or target_id == bucket_id:
            continue
        if float(score) < AUTO_RELATED_MIN_SCORE:
            continue
        target = await bucket_mgr.get(target_id)
        if not target:
            continue
        target_meta = target.get("metadata") or {}
        if not _eligible(target_meta):
            continue
        relation_type = infer_auto_relation_type(float(score), _hours_apart(source_meta, target_meta))
        if relation_type is None:
            continue
        inferred.append({
            "target_bucket_id": target_id,
            "type": relation_type,
            "label": "",
            "status": "active",
            "auto": True,
            "score": round(float(score), 4),
        })
        if len(inferred) >= AUTO_MAX_LINKS_PER_BUCKET:
            break
    return inferred


async def link_new_bucket(bucket_mgr, embedding_engine, bucket_id: str, content: str) -> int:
    """推断并双向写入关系，返回实际建立的条数。调用方应 create_task，不要 await。"""
    try:
        inferred = await infer_links_for(bucket_mgr, embedding_engine, bucket_id, content)
    except Exception as exc:  # noqa: BLE001 - fire-and-forget，绝不影响写入
        logger.warning(f"auto relation inference failed / 自动关系推断失败: {type(exc).__name__}: {exc}")
        return 0

    built = 0
    for link in inferred:
        target_id = link["target_bucket_id"]
        reverse = {**link, "target_bucket_id": bucket_id, "type": reverse_relation_type(link["type"])}

        def _mutation(left_post, right_post, _link=link, _reverse=reverse):
            try:
                left_links = normalize_relation_links(left_post.metadata.get("relation_links"))
                right_links = normalize_relation_links(right_post.metadata.get("relation_links"))
            except ValueError:
                # 存量数据写坏了不该拖累新关系，但也不在这里悄悄修复它
                return False, False, 0
            merged_left = merge_auto_links(left_links, [_link])
            merged_right = merge_auto_links(right_links, [_reverse])
            left_changed = merged_left != left_links
            right_changed = merged_right != right_links
            if left_changed:
                left_post["relation_links"] = normalize_relation_links(merged_left)
            if right_changed:
                right_post["relation_links"] = normalize_relation_links(merged_right)
            return left_changed, right_changed, int(left_changed or right_changed)

        try:
            result = await bucket_mgr.mutate_relation_pair(bucket_id, target_id, _mutation)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"auto relation write failed / 自动关系写入失败 {bucket_id}->{target_id}: {type(exc).__name__}: {exc}")
            continue
        built += int(result or 0)

    if built:
        logger.info(f"auto relations built / 自动建立关系: {bucket_id} -> {built} 条")
    return built


# ============================================================
# 存量回填（本 fork 新增）
# ------------------------------------------------------------
# 上游的自动关系只在【新建桶】时推断一次，存量记忆永远没有关系。这里对存量做一次补建：
# 按 created 从旧到新遍历，每条只和【比它早】的桶连——每一对只从较新的一侧处理一次，
# 方向与实时写入一致（新桶 continuation_of 旧桶）。复用同一套阈值与 merge_auto_links，
# 已有关系（含手动改过的）不会被改写；重复运行是安全的。
# 每条调用一次 embedding 查询；中途失败只记日志，继续下一条。
# ============================================================

async def backfill_links(bucket_mgr, embedding_engine, progress: dict, pause_s: float = 0.2) -> dict:
    import asyncio

    progress.update({"running": True, "processed": 0, "built": 0, "errors": 0, "total": 0,
                     "skipped": 0, "last_error": ""})
    if not embedding_engine or not getattr(embedding_engine, "enabled", False):
        progress.update({"running": False, "last_error": "embedding 未启用，无法推断关系"})
        return progress

    buckets = await bucket_mgr.list_all(include_archive=False)
    eligible = [b for b in buckets if _eligible(b.get("metadata") or {}) and str(b.get("content") or "").strip()]
    # 按 (created, id) 定一个全序: 同一秒建的桶(grow 批量写入很常见)也分得出先后, 每一对恰好处理一次
    eligible.sort(key=lambda b: (str((b.get("metadata") or {}).get("created") or ""), b["id"]))
    rank = {b["id"]: i for i, b in enumerate(eligible)}
    progress["total"] = len(eligible)

    for bucket in eligible:
        bucket_id = bucket["id"]
        try:
            inferred = await infer_links_for(bucket_mgr, embedding_engine, bucket_id, bucket.get("content", ""))
            older = [l for l in inferred if rank.get(l["target_bucket_id"], len(rank)) < rank[bucket_id]]
            if not older:
                progress["skipped"] += 1
            for link in older:
                progress["built"] += await _write_pair(bucket_mgr, bucket_id, link)
        except Exception as exc:  # noqa: BLE001
            progress["errors"] += 1
            progress["last_error"] = f"{bucket_id}: {type(exc).__name__}: {exc}"[:300]
            logger.warning(f"relation backfill failed / 关系回填失败 {bucket_id}: {exc}")
        progress["processed"] += 1
        if pause_s:
            await asyncio.sleep(pause_s)  # 给 embedding API 留喘息，避免限流

    progress["running"] = False
    logger.info(f"relation backfill done / 关系回填完成: {progress}")
    return progress


async def _write_pair(bucket_mgr, bucket_id: str, link: dict) -> int:
    target_id = link["target_bucket_id"]
    reverse = {**link, "target_bucket_id": bucket_id, "type": reverse_relation_type(link["type"])}

    def _mutation(left_post, right_post):
        try:
            left_links = normalize_relation_links(left_post.metadata.get("relation_links"))
            right_links = normalize_relation_links(right_post.metadata.get("relation_links"))
        except ValueError:
            return False, False, 0
        merged_left = merge_auto_links(left_links, [link])
        merged_right = merge_auto_links(right_links, [reverse])
        left_changed, right_changed = merged_left != left_links, merged_right != right_links
        if left_changed:
            left_post["relation_links"] = normalize_relation_links(merged_left)
        if right_changed:
            right_post["relation_links"] = normalize_relation_links(merged_right)
        return left_changed, right_changed, int(left_changed or right_changed)

    return int(await bucket_mgr.mutate_relation_pair(bucket_id, target_id, _mutation) or 0)
