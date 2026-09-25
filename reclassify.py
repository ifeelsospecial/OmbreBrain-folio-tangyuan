"""给「未分类」记忆重新分类（本 fork 新增，取代只能在 Docker 里跑的 reclassify_api.py）。

只动 domain 与 tags：名字、情绪坐标、重要度、激活时间都保留——
普通 update 会刷新 last_active，79 条旧记忆会一下子"变新"，打乱衰减与浮现。
tags 合并不覆盖（保留原有的，包括 __ 开头的系统标签）；新 domain 仍是「未分类」的跳过。
分完把文件挪进对应领域的目录，和新建时的落盘位置一致。
"""
from __future__ import annotations

import asyncio
import logging

import frontmatter

logger = logging.getLogger("ombre_brain.reclassify")

_UNCATEGORIZED = {"未分类", ""}
_TYPES = ("dynamic", "permanent")
_MAX_TAGS = 10


def is_uncategorized(meta: dict) -> bool:
    domains = [str(d).strip() for d in (meta or {}).get("domain") or [] if d is not None]
    return not domains or all(d in _UNCATEGORIZED for d in domains)


async def reclassify_uncategorized(bucket_mgr, dehydrator, progress: dict, pause_s: float = 0.5) -> dict:
    progress.update({"running": True, "processed": 0, "total": 0, "changed": 0, "skipped": 0,
                     "errors": 0, "last_error": ""})
    if not getattr(dehydrator, "api_available", False):
        progress.update({"running": False, "last_error": "AI 接口不可用，无法重新分类"})
        return progress

    buckets = [
        b for b in await bucket_mgr.list_all(include_archive=False)
        if (b.get("metadata") or {}).get("type", "dynamic") in _TYPES
        and is_uncategorized(b.get("metadata") or {})
        and str(b.get("content") or "").strip()
    ]
    progress["total"] = len(buckets)

    for bucket in buckets:
        bid = bucket["id"]
        try:
            analysis = await dehydrator.analyze(bucket["content"])
            domains = [str(d).strip() for d in (analysis.get("domain") or [])
                       if d and str(d).strip() not in _UNCATEGORIZED][:2]
            if not domains:
                progress["skipped"] += 1
            else:
                new_tags = [str(t).strip() for t in (analysis.get("tags") or []) if str(t).strip()]

                def _fn(post, _domains=domains, _new=new_tags):
                    old = [str(t) for t in (post.get("tags") or [])]
                    merged = old + [t for t in _new if t not in old]
                    post["domain"] = _domains
                    post["tags"] = merged[:max(_MAX_TAGS, len(old))]
                    return True

                if await bucket_mgr.rewrite_bucket_metadata(bid, _fn):
                    await _move_to_domain_dir(bucket_mgr, bid, domains)
                    progress["changed"] += 1
                else:
                    progress["skipped"] += 1
        except Exception as exc:  # noqa: BLE001
            progress["errors"] += 1
            progress["last_error"] = f"{bid}: {type(exc).__name__}: {exc}"[:300]
            logger.warning(f"reclassify failed / 重新分类失败 {bid}: {exc}")
        progress["processed"] += 1
        if pause_s:
            await asyncio.sleep(pause_s)

    progress["running"] = False
    logger.info(f"reclassify done / 重新分类完成: {progress}")
    return progress


async def _move_to_domain_dir(bucket_mgr, bucket_id: str, domains: list) -> None:
    async with bucket_mgr._bucket_turn(bucket_id):
        path = bucket_mgr._find_bucket_file(bucket_id)
        if not path:
            return
        btype = frontmatter.load(path).get("type", "dynamic")
        base = bucket_mgr.permanent_dir if btype == "permanent" else bucket_mgr.dynamic_dir
        bucket_mgr._move_bucket(path, base, domains)
        bucket_mgr._invalidate_active_cache()
