"""「关于你」偏好档案（本 fork 新增，取上游 3.5.0 `You` 的核心、做轻）。

为什么要有它：记忆库里存的是 600 多条「发生过的事」，她的偏好散在里面，没人把它提炼成
一句「她喜欢什么」。每次推荐都得从事件里现翻，翻不到就只能泛泛而谈——这正是「推荐的
罗马景点不对胃口」的原因。ChatGPT 的记忆是一份常驻的短档案，每次对话都在眼前。

所以这里是一份按方面分的短档案：
- 每条一句话，写的是**稳定的**偏好、习惯、雷区、低落时需要什么，不是一次性的事件（那是 hold）。
- 他刚写下的是「观察中」；在**不同的日子**里又看到同一件事、确认一次，攒够天数才定下来——
  一次随口的话不该被当成定论（上游 You 用 3 个不同自然日，这里默认 2 天，她聊得没那么密）。
  她在页面上点「对」，当场定下。
- 每次开场只把**已确定**的部分整份带上（有长度上限）；观察中的只报个数，不占 token。
- 存成 `<buckets_dir>/profile/about.md`：不混进普通记忆列表，但跟着 buckets 目录进每日备份。
"""
from __future__ import annotations

import os
import secrets
from datetime import datetime

import frontmatter

from utils import atomic_write_text, filesystem_turn

ASPECTS = [
    ("travel", "旅行节奏"),
    ("sights", "爱看什么"),
    ("food", "吃的口味"),
    ("avoid", "不喜欢 · 雷区"),
    ("comfort", "低落时需要什么"),
    ("talk", "说话习惯"),
    ("daily", "生活习惯"),
]
ASPECT_KEYS = [k for k, _ in ASPECTS]
ASPECT_LABELS = dict(ASPECTS)

CONFIRM_DAYS = 2          # 在几个不同的日子里看到, 才从「观察中」定下来
MAX_TEXT = 80             # 每条一句话
MAX_PER_ASPECT = 8        # 每个方面最多几条(含观察中)
INJECT_MAX_CHARS = 1200   # 开场带上的已确定部分, 总长上限


def _today() -> str:
    return datetime.utcnow().date().isoformat()


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


class AboutError(ValueError):
    pass


class AboutStore:
    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self.dir = os.path.join(base_dir, "profile")
        self.path = os.path.join(self.dir, "about.md")

    # ---------------- 读写 ----------------
    def load(self) -> dict:
        data = {k: [] for k in ASPECT_KEYS}
        if not os.path.exists(self.path):
            return data
        try:
            post = frontmatter.load(self.path)
        except Exception:
            return data
        raw = post.get("aspects") or {}
        for key in ASPECT_KEYS:
            rows = raw.get(key) if isinstance(raw, dict) else None
            for e in rows or []:
                if isinstance(e, dict) and str(e.get("text") or "").strip() and e.get("id"):
                    data[key].append({
                        "id": str(e["id"]),
                        "text": str(e["text"]).strip()[:MAX_TEXT],
                        "status": "confirmed" if e.get("status") == "confirmed" else "watching",
                        "source": str(e.get("source") or "him"),
                        "seen": sorted({str(d)[:10] for d in (e.get("seen") or []) if d}),
                        "updated": str(e.get("updated") or ""),
                    })
        return data

    def _save(self, data: dict) -> None:
        os.makedirs(self.dir, exist_ok=True)
        post = frontmatter.Post(
            "她的偏好档案。由 about.py 维护；可以在 dashboard 的「关于你」页面查看和修改。",
            aspects={k: data.get(k, []) for k in ASPECT_KEYS},
            updated=_now(),
        )
        atomic_write_text(self.path, frontmatter.dumps(post))

    def _turn(self):
        return filesystem_turn(self.base_dir, "about-profile")

    @staticmethod
    def _find(data: dict, entry_id: str):
        for key in ASPECT_KEYS:
            for e in data[key]:
                if e["id"] == entry_id:
                    return key, e
        return None, None

    # ---------------- 操作 ----------------
    async def add(self, aspect: str, text: str, source: str = "him", confirmed: bool = False) -> dict:
        aspect = str(aspect or "").strip().lower()
        text = str(text or "").strip()
        if aspect not in ASPECT_KEYS:
            raise AboutError(f"aspect 只能是 {' / '.join(ASPECT_KEYS)}（{'、'.join(ASPECT_LABELS.values())}）")
        if not text:
            raise AboutError("text 不能为空")
        if len(text) > MAX_TEXT:
            raise AboutError(f"每条一句话，最多 {MAX_TEXT} 字（这条 {len(text)} 字）。长的经历用 hold 存成记忆。")
        async with self._turn():
            data = self.load()
            for e in data[aspect]:
                if e["text"] == text:
                    return await self._seen_locked(data, e, source)
            if len(data[aspect]) >= MAX_PER_ASPECT:
                raise AboutError(f"「{ASPECT_LABELS[aspect]}」已经有 {MAX_PER_ASPECT} 条了。先合并或删掉旧的，再加新的。")
            entry = {
                "id": secrets.token_hex(4),
                "text": text,
                "status": "confirmed" if confirmed else "watching",
                "source": source,
                "seen": [_today()],
                "updated": _now(),
            }
            data[aspect].append(entry)
            self._save(data)
            return {**entry, "aspect": aspect}

    async def _seen_locked(self, data: dict, entry: dict, source: str) -> dict:
        today = _today()
        if source == "memory":
            # 从记忆里提炼出来的不算"又看到了一次": 重跑提炼不能替她把条目定下来
            return {**entry, "aspect": self._find(data, entry["id"])[0]}
        if today not in entry["seen"]:
            entry["seen"] = sorted(set(entry["seen"]) | {today})
        if source == "her" or len(entry["seen"]) >= CONFIRM_DAYS:
            entry["status"] = "confirmed"
        entry["updated"] = _now()
        self._save(data)
        key, _ = self._find(data, entry["id"])
        return {**entry, "aspect": key}

    async def confirm(self, entry_id: str, by: str = "him") -> dict:
        """他: 在新的一天里又看到了同一件事(攒天数); 她: 当场定下。"""
        async with self._turn():
            data = self.load()
            key, e = self._find(data, entry_id)
            if not e:
                raise AboutError(f"找不到 {entry_id}")
            return await self._seen_locked(data, e, by)

    async def revise(self, entry_id: str, text: str, by: str = "him") -> dict:
        text = str(text or "").strip()
        if not text or len(text) > MAX_TEXT:
            raise AboutError(f"新内容要是一句话，1–{MAX_TEXT} 字")
        async with self._turn():
            data = self.load()
            key, e = self._find(data, entry_id)
            if not e:
                raise AboutError(f"找不到 {entry_id}")
            e["text"] = text
            e["updated"] = _now()
            if by == "her":
                e["status"] = "confirmed"
                e["source"] = "her"
            self._save(data)
            return {**e, "aspect": key}

    async def remove(self, entry_id: str) -> bool:
        async with self._turn():
            data = self.load()
            key, e = self._find(data, entry_id)
            if not e:
                return False
            data[key] = [x for x in data[key] if x["id"] != entry_id]
            self._save(data)
            return True

    # ---------------- 展示 ----------------
    def render_for_model(self, include_watching: bool = False) -> str:
        """开场注入: 只带已确定的(总长有上限); include_watching=True 时带上观察中的和 id(给 you(read=True))。"""
        data = self.load()
        lines, used, watching = [], 0, 0
        for key in ASPECT_KEYS:
            confirmed = [e for e in data[key] if e["status"] == "confirmed"]
            pending = [e for e in data[key] if e["status"] != "confirmed"]
            watching += len(pending)
            rows = confirmed + (pending if include_watching else [])
            if not rows:
                continue
            if include_watching:
                parts = [f"  · {e['text']}" + ("" if e["status"] == "confirmed" else f"（观察中 {len(e['seen'])}/{CONFIRM_DAYS} 天）")
                         + f" [id:{e['id']}]" for e in rows]
                lines.append(f"【{ASPECT_LABELS[key]}】 aspect={key}\n" + "\n".join(parts))
            else:
                line = f"【{ASPECT_LABELS[key]}】" + "；".join(e["text"] for e in confirmed)
                if used + len(line) > INJECT_MAX_CHARS:
                    break
                used += len(line)
                lines.append(line)
        if not lines:
            if include_watching:
                return "档案还是空的。聊天里发现她稳定的偏好、习惯、雷区、低落时需要什么，就用 you(aspect=..., text=...) 记下来。"
            return ""
        head = "=== 关于她（我对她的了解）===" if not include_watching else "=== 关于她 · 全部 ==="
        tail = ""
        if not include_watching and watching:
            tail = f"\n（另有 {watching} 条还在观察中，没定下来。）"
        return head + "\n" + "\n".join(lines) + tail

    def as_json(self) -> dict:
        data = self.load()
        return {
            "aspects": [{"key": k, "label": ASPECT_LABELS[k], "entries": data[k]} for k in ASPECT_KEYS],
            "confirm_days": CONFIRM_DAYS,
            "max_per_aspect": MAX_PER_ASPECT,
            "max_text": MAX_TEXT,
        }


# ============================================================
# 从现有记忆提炼第一版（她审过才算数）
# ------------------------------------------------------------
# 分批把记忆的名字+开头喂给 AI，只要「稳定的」偏好（在多条记忆里反复出现的），
# 最后再合并去重一次，每个方面最多 6 条。结果一律是「观察中」、来源 memory，
# 她在页面上点「对」才定下。不会覆盖已有条目（相同文字的只记一次）。
# ============================================================
DRAFT_BATCH = 40
DRAFT_PER_ASPECT = 6

_DRAFT_PROMPT = (
    "下面是一个人的一批生活记忆（第三人称「她」）。从中提炼她【稳定的】偏好与习惯——\n"
    "只要在多条记忆里反复出现、或者她明确说过的；一次性的事件不要。\n"
    "按这些方面归类（没有就留空）：\n"
    + "\n".join(f"- {k}: {v}" for k, v in ASPECTS) +
    "\n每条一句话，≤40 字，用「她……」开头或直接写偏好，具体，不要空话（不要「她热爱生活」这种）。\n"
    '只输出 JSON：{"travel":["…"],"sights":[],"food":[],"avoid":[],"comfort":[],"talk":[],"daily":[]}'
)
_MERGE_PROMPT = (
    "下面是从一个人的记忆里分批提炼出的偏好条目，有重复和近义。请按方面合并去重，"
    f"每个方面保留最有代表性、最具体的最多 {DRAFT_PER_ASPECT} 条，每条 ≤40 字。互相矛盾的都去掉。\n"
    '只输出 JSON，键同输入：{"travel":[…],"sights":[…],"food":[…],"avoid":[…],"comfort":[…],"talk":[…],"daily":[…]}'
)


async def _ask_json(dehydrator, system: str, user: str) -> dict:
    import json
    from utils import clean_llm_json
    resp = await dehydrator.client.chat.completions.create(
        model=dehydrator.model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user[:12000]}],
        max_tokens=1500, temperature=0.2,
    )
    raw = (resp.choices[0].message.content or "") if resp.choices else ""
    data = json.loads(clean_llm_json(raw)) if raw.strip() else {}
    return {k: [str(x).strip()[:MAX_TEXT] for x in (data.get(k) or []) if str(x).strip()]
            for k in ASPECT_KEYS} if isinstance(data, dict) else {k: [] for k in ASPECT_KEYS}


async def draft_from_memories(bucket_mgr, dehydrator, store: AboutStore, progress: dict, pause_s: float = 6.5) -> dict:
    import asyncio
    from utils import is_knowledge, strip_wikilinks
    progress.update({"running": True, "batches": 0, "done_batches": 0, "added": 0, "errors": 0, "last_error": ""})
    if not getattr(dehydrator, "api_available", False):
        progress.update({"running": False, "last_error": "AI 接口不可用，没法提炼"})
        return progress
    buckets = [b for b in await bucket_mgr.list_all(include_archive=False)
               if (b.get("metadata") or {}).get("type", "dynamic") in ("dynamic", "permanent")
               and not is_knowledge(b.get("metadata") or {})]
    buckets.sort(key=lambda b: str((b.get("metadata") or {}).get("created") or ""))
    lines = [f"- {(b.get('metadata') or {}).get('name') or ''}：{strip_wikilinks(str(b.get('content') or ''))[:150]}"
             for b in buckets]
    batches = [lines[i:i + DRAFT_BATCH] for i in range(0, len(lines), DRAFT_BATCH)]
    progress["batches"] = len(batches) + 1
    pooled = {k: [] for k in ASPECT_KEYS}
    for chunk in batches:
        try:
            got = await _ask_json(dehydrator, _DRAFT_PROMPT, "\n".join(chunk))
            for k in ASPECT_KEYS:
                pooled[k].extend(got[k])
        except Exception as exc:  # noqa: BLE001
            progress["errors"] += 1
            progress["last_error"] = f"{type(exc).__name__}: {exc}"[:300]
        progress["done_batches"] += 1
        if pause_s:
            await asyncio.sleep(pause_s)
    import json
    try:
        merged = await _ask_json(dehydrator, _MERGE_PROMPT, json.dumps(pooled, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001
        progress["errors"] += 1
        progress["last_error"] = f"合并失败 {type(exc).__name__}: {exc}"[:300]
        merged = {k: v[:DRAFT_PER_ASPECT] for k, v in pooled.items()}
    progress["done_batches"] += 1
    for k in ASPECT_KEYS:
        for text in merged[k][:DRAFT_PER_ASPECT]:
            try:
                await store.add(k, text, source="memory")
                progress["added"] += 1
            except AboutError:
                continue
    progress["running"] = False
    return progress
