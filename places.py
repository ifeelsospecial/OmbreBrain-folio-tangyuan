"""足迹：从记忆里认出现实地点，给记忆星图之外的「足迹地图」用（本 fork 新增）。

- 只对「出行」领域或带明显去向/场所字眼的记忆调用 AI，控制花费；
- 在后台跑，失败只记日志，不影响存记忆；
- 结果写进 frontmatter 的 `places`（空列表 = 看过、没有地点，回填时不再重复看）；
- 只改 places，不刷新激活时间。
坐标是 AI 给的近似值（城市中心或场所附近），只用于在地图上摆位置，不追求精确。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re

logger = logging.getLogger("ombre_brain.places")

_TYPES = ("dynamic", "permanent")
_HINT = re.compile(
    r"(去了|到了|来到|抵达|回到|飞到|飞去|出发|机场|车站|火车|航班|酒店|民宿|旅行|旅游|出差|"
    r"美术馆|博物馆|公园|广场|大街|海边|海滩|城堡|教堂|餐厅|咖啡馆|市场|"
    r"伦敦|巴黎|爱丁堡|曼彻斯特|柏林|阿姆斯特丹|罗马|巴塞罗那|布鲁塞尔|北京|上海)"
)
MAX_PLACES = 5

PROMPT = (
    "你在帮一个记忆库标注地理位置。读下面这段记忆，找出其中【她实际身处、去过或正前往】的现实地点。\n"
    "规则：\n"
    "- 只要现实世界里能定位的地点：城市、景点、场馆、机场、街区等\n"
    "- 「家」「公司」「学校」这类泛指不要，除非文中能确定是哪个城市，那就给城市\n"
    "- 只是提到、没有去（比如『想去巴黎』『新闻里的东京』）不要\n"
    "- lat / lon 给近似坐标；拿不准具体场所就给所在城市中心\n"
    f"- 最多 {MAX_PLACES} 个；没有就返回空列表\n"
    '只输出 JSON：{"places":[{"name":"泰特现代美术馆","city":"伦敦","country":"英国","lat":51.5076,"lon":-0.0994}]}'
)


def should_extract(meta: dict, content: str) -> bool:
    meta = meta or {}
    if meta.get("type", "dynamic") not in _TYPES:
        return False
    domains = [str(d) for d in meta.get("domain") or []]
    return "出行" in domains or bool(_HINT.search(str(content or "")))


def normalize_places(value) -> list[dict]:
    out, seen = [], set()
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            lat, lon = float(item.get("lat")), float(item.get("lon"))
        except (TypeError, ValueError):
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
            continue
        name = str(item.get("name") or item.get("city") or "").strip()[:60]
        city = str(item.get("city") or "").strip()[:40]
        country = str(item.get("country") or "").strip()[:40]
        if not name:
            continue
        key = (name, city)
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": name, "city": city, "country": country, "lat": round(lat, 5), "lon": round(lon, 5)})
        if len(out) >= MAX_PLACES:
            break
    return out


async def extract_places(dehydrator, content: str) -> list[dict]:
    from utils import clean_llm_json
    response = await dehydrator.client.chat.completions.create(
        model=dehydrator.model,
        messages=[{"role": "system", "content": PROMPT}, {"role": "user", "content": str(content)[:2000]}],
        max_tokens=600,
        temperature=0.0,
    )
    raw = (response.choices[0].message.content or "") if response.choices else ""
    if not raw.strip():
        return []
    data = json.loads(clean_llm_json(raw))
    return normalize_places(data.get("places") if isinstance(data, dict) else data)


async def attach_places(bucket_mgr, dehydrator, bucket_id: str, content: str) -> int:
    """认地点并写回; 返回认出的个数。任何异常只记日志。"""
    if not getattr(dehydrator, "api_available", False):
        return 0
    try:
        places = await extract_places(dehydrator, content)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"place extraction failed / 地点识别失败 {bucket_id}: {type(exc).__name__}: {exc}")
        return 0

    def _fn(post, _p=places):
        post["places"] = _p
        # 城市名并进标签: 聊到"伦敦"时检索能直接命中在伦敦的记忆; 星图里同城记忆也会因共享标签连起来
        cities = [c for c in dict.fromkeys(pl["city"] for pl in _p) if c]
        if cities:
            old = [str(t) for t in (post.get("tags") or [])]
            post["tags"] = old + [c for c in cities if c not in old]
        return True

    try:
        await bucket_mgr.rewrite_bucket_metadata(bucket_id, _fn)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"place write failed / 地点写入失败 {bucket_id}: {exc}")
        return 0
    return len(places)


async def backfill_places(bucket_mgr, dehydrator, progress: dict, pause_s: float = 0.3) -> dict:
    progress.update({"running": True, "processed": 0, "total": 0, "found": 0, "with_places": 0,
                     "errors": 0, "last_error": ""})
    if not getattr(dehydrator, "api_available", False):
        progress.update({"running": False, "last_error": "AI 接口不可用，无法识别地点"})
        return progress
    buckets = [
        b for b in await bucket_mgr.list_all(include_archive=True)
        if "places" not in (b.get("metadata") or {})
        and should_extract(b.get("metadata") or {}, b.get("content", ""))
    ]
    progress["total"] = len(buckets)
    for b in buckets:
        try:
            n = await attach_places(bucket_mgr, dehydrator, b["id"], b.get("content", ""))
            progress["found"] += n
            progress["with_places"] += int(n > 0)
        except Exception as exc:  # noqa: BLE001
            progress["errors"] += 1
            progress["last_error"] = f"{b['id']}: {exc}"[:300]
        progress["processed"] += 1
        if pause_s:
            await asyncio.sleep(pause_s)
    progress["running"] = False
    return progress


def aggregate(buckets: list) -> list[dict]:
    """按城市(没有城市就按地点名)聚合成地图上的点; 坐标取该城市下各地点的平均。"""
    from utils import parse_iso_datetime, is_knowledge
    groups: dict = {}
    for b in buckets:
        meta = b.get("metadata") or {}
        places = normalize_places(meta.get("places"))
        if not places:
            continue
        day = ""
        for key in ("event_time", "created"):
            if meta.get(key):
                try:
                    day = parse_iso_datetime(meta[key]).date().isoformat()
                    break
                except (TypeError, ValueError):
                    continue
        for p in places:
            key = (p["city"] or p["name"], p["country"])
            g = groups.setdefault(key, {"city": key[0], "country": p["country"], "lats": [], "lons": [],
                                        "spots": set(), "memories": {}})
            g["lats"].append(p["lat"])
            g["lons"].append(p["lon"])
            if p["name"] != key[0]:
                g["spots"].add(p["name"])
            # 同一条记忆在同一个城市只算一次(一段记忆里提到同城的两个景点很常见)
            g["memories"].setdefault(b["id"], {"id": b["id"], "name": meta.get("name") or b["id"], "day": day,
                                               "spot": p["name"] if p["name"] != key[0] else "",
                                               "note": is_knowledge(meta)})
    out = []
    for g in groups.values():
        mems = sorted(g["memories"].values(), key=lambda m: m["day"], reverse=True)
        out.append({
            "city": g["city"], "country": g["country"],
            "lat": round(sum(g["lats"]) / len(g["lats"]), 5), "lon": round(sum(g["lons"]) / len(g["lons"]), 5),
            "spots": sorted(g["spots"]), "count": len(mems), "memories": mems,
            "notes": sum(1 for m in mems if m["note"]),
            "first": mems[-1]["day"] if mems else "", "last": mems[0]["day"] if mems else "",
        })
    out.sort(key=lambda g: -g["count"])
    return out
