# 更新日志 / Changelog

本 fork 以快照方式发布（无版本号），条目按日期记录。上游对齐条目会标注对应的上游版本。

## 2026-09-26 · 桶间关系（对齐上游 3.2.0 / 3.3.0）

- **自动建立关系**：hold / grow / capture-hook 新建记忆后，后台用向量相似度 + 时间差推断关系并双向写入 `relation_links`（规则原样移植自上游 `relation_store.py`：same_event ≥0.85 且 ≤6 小时、continuation_of ≥0.75 且 ≤72 小时、related_to ≥0.72；每桶最多 8 条自动关系；因果与 custom 永不自动建）。不调 LLM；fire-and-forget，失败只记日志，不影响写入。feel / plan / letter / I 不参与。只对之后新建的记忆生效，存量记忆不补建。
- **关系提示**：breath 浮现（核心准则 / 永久参考 / 浮现记忆）、检索结果和 dream 里，记忆下方显示 `↳ 相关 → <id>`（最多 2 条，多的折叠）。只显示目标在当前视野内（活跃、未删除、分级可见）的关系。
- **trace 修正关系**：`unlink="目标id"` 双向物理移除；`relink="目标id", relation_type=...` 改已有关系类型，对侧自动写反向类型，改过的降为手动关系、不再被自动推断改写。不能凭空建立；与其他修改互斥；参数不合法抛工具错误而不是返回像成功的短句。
- **`BucketManager.mutate_relation_pair()`**：按 id 排序持有两把跨进程桶锁，第二个写失败时恢复第一个。
- **核心准则不可被消化**（对齐上游 3.2.0 修复）：钉选 / 高亮 / 保护的桶不再因带 internalized（digested）标记从 breath 与 breath-hook 的核心准则 / 永久参考区消失。线上数据中目前没有这种桶，不会有记忆突然冒出。
- 测试：新增 `tests/test_relations.py`（6 例）；已验证去掉可见性过滤、去掉新建后连关系、恢复"已内化即隐藏"时对应用例失败。全量 143 passed, 7 skipped。

## 2026-09-26 · 检索与浮现（对齐上游 3.6.0 / 3.0.0）

- **检索只读**：`breath_search` / `breath_advanced` 的命中不再后台 `touch()`。此前每条命中都刷新 `last_active`、`activation_count += 1` 并触发时间涟漪，形成"查得勤 == 更重要"：为核对事实反复读的旧记忆权重越爬越高，新桶挤不进浮现区。recall-hook / breath-hook 本来就只记命中统计、不改权重，行为不变。
- **新增 `trace(bucket_id, reinforce=True)`**：唯一的显式强化入口。按桶强化，带时间涟漪；与其他字段更新互斥；不改内容，所以用户手写的桶也能强化；回收站 / 归档区的桶需先恢复。
- **日期过滤**：`breath_search` / `breath_advanced` 新增 `date_from` / `date_to`（YYYY-MM-DD 或 ISO；纯日期的 date_to 含当天全天），对检索、浮现、feel、catalog 四条分支统一生效。本 fork 优先按 `event_time`（事情发生的时间）过滤，没有才用 `created`；给了范围时读不出时间的桶排除。核心准则 / 永久参考不受时间过滤。
- **浮现区近期名额**：新增 `surfacing.recent_slots`（默认 3）/ `surfacing.recent_days`（默认 7）。按缺口补，补进来的排在冷启动与 top1 之后，不先被 token 预算砍掉。
- 上游"24 小时内新桶不进『久未浮现』区"不适用：本 fork 没有该区，冷启动通道本就为新建重要桶设计。
- 修正 `trace` 对 `resolved=1` 的描述：沉底、仍可检索、之后由衰减归档；只有 resolved + importance=1（噪声）直接进归档区。
- **新增 `feel(query)` 工具**（对齐上游 3.0.0）：按关键词找 feel，候选限定在当前视野可见的 feel 内；向量相似度 ≥ 0.65 才命中，先按相似度再按时间倒序，逐字返回；向量不可用时退回字面匹配并在首行说明。与上游不同，`breath_advanced(domain="feel")` 仍按时间列出全部 feel（开场流程在用），不强制 query。
- **`breath_advanced(domain="plan")` 通道**（对齐上游 3.0.0 修复）：此前会落进普通浮现、返回核心准则；现在逐字列出进行中的 plan，不调 LLM。
- 测试：新增 `tests/test_upstream_36_retrieval.py`（9 例）；已验证恢复检索 touch、关闭名额时对应用例失败。全量 137 passed, 7 skipped。

## 2026-09-25 · 合入 folio 开发分支（codex/upstream-alignment-safe-fixes @ 6619e5d, 2026-07-14 ~ 07-18）

folio 作者未发正式快照的四批对齐（详见下方 07-14 / 07-15 / 07-16 / 07-17 条目）整体合入：Plan / Letter / `I` / Anchor、持久媒体附件、`why_remembered` / `meaning`、GitHub 备份校验与合并式恢复、OAuth 2.1（默认关闭）、Cloudflare Tunnel / 自更新 / 重启 / 多实例（均默认关闭）、同桶并发锁、向量批处理、语义降级提示、catalog 目录模式等。

本 fork 的调整：

- **私密分级**：folio 重写了 `list_all()`（文件指纹缓存，多条返回路径）。原实现改名为 `_list_all_unfiltered()`，`list_all()` 包一层 `_filter_by_access_level()`，所有返回路径统一过滤。`find_exact_content()`（导入去重）与 `list_trash()` 也按级别过滤。新工具 plan / letter_read / anchor / I 读桶都经 `list_all` / `get`，天然受限。
- **OAuth 通道**：通过 OAuth 进入 `/mcp` 的请求显式设为受限视野（level 1），与 URL-key 同级。
- **breath**：沿用本 fork 的兼容适配（0 参数 schema + 旧参数照常生效 + 未知参数报错），因此不带 folio 的 `breath_legacy`；`breath_advanced` 增加 `catalog`。
- **自更新默认源**改为本仓库，避免误拉 folio 覆盖私密分级与 hook（功能本身仍默认关闭）。
- `hold` / `grow` / `_merge_or_create` / dashboard 新建同时保留 `level` 与 folio 新增的 media / why_remembered / meaning 等参数；recall-hook / capture-hook 未改。
- CLAUDE_PROMPT.md 补上 plan / letter / I / anchor 工具说明。

测试：全量 128 passed, 7 skipped（main 上 3 个 TestDecayScoreSpecial 旧失败随 folio 修正断言一并消失）。新增 3 例分级回归（信件、anchor、查重与回收站），已验证去掉过滤时失败。另用线上备份副本（613 个桶、5 个私密桶）本地试跑：页面、链接图、信件 / plan / 回收站接口、recall-hook 正常；模拟 claude.ai URL-key 通道用 breath_search / source / letter_read 检索全部私密桶，泄露 0。

## 2026-09-25 · breath 拆分（对齐上游 2.6.x / folio 2026-07-15）

- `breath` 对外公布为 0 参数（claude.ai 按需加载工具时能稳定选中），新增 `breath_search(query, domain, max_results)` 与 `breath_advanced(query, max_tokens, domain, valence, arousal, max_results)`。
- 兼容：`breath` 函数保留旧签名，缓存了旧 schema 的客户端继续传 `query`/`domain` 照常生效；拼错或未知参数直接报错，不再被 FastMCP 静默丢弃降级为默认浮现（做法同上游 3.6.x，未采用 folio 的 `breath_legacy` 别名）。
- 当时未移植 folio 的 `catalog` 目录模式；已随下方 2026-09-25 folio 开发版合并加入 `breath_advanced(catalog=True)`。
- 私密分级不变：三个入口共用同一实现，读桶仍经 `list_all`/`get` 分级过滤；`/breath-hook`、`/recall-hook`、`/capture-hook` 不经过这些工具，不受影响。
- CLAUDE_PROMPT.md 采用 folio 新版工具说明（去掉 catalog），保留 `source`；README / USAGE 同步。
- 新增 `tests/test_breath_split.py`（6 例，走真实 MCP 调用路径）；已验证去掉兼容适配时其中 2 例失败。

## 2026-07-17 · 上游 v2.7.6 产品能力完整对齐

- 新增独立 Plan / Letter / `I` 记忆类型、计划自动完成判断、Anchor（24 条上限）、`dont_surface`、`first_of_kind`、`triggered_by` 与桌面/手机响应式管理入口。
- 新增 OAuth 2.1 PKCE MCP 鉴权、Cloudflare Quick Tunnel 控制、部署自检、安全暂存式自更新/重启与受来源约束的测试数据硬删除；危险动作均默认关闭。
- 新增多人多实例启动器和 Docker 模板：每位 owner 使用独立进程、端口、数据目录和 Secret，子进程不继承其他 owner 的 Secret。
- 新增钉选与普通高重要度的跨进程并发配额；对齐 Embedding 单调补账、本地 Ollama 安全切换和脱水视角 v4，避免旧快照覆盖新任务及人物主语翻转。
- 反代头只接受 `OMBRE_TRUSTED_PROXY_CIDRS` 白名单最后一跳；OAuth 公网 origin 必须显式固定，访问/刷新令牌仅以哈希落盘。
- 新增傻子版扩展能力说明与 7 组专项回归；全仓结果为 `115 passed, 7 skipped, 5 subtests passed`。

## 2026-07-16 · 数据恢复、持久附件与体验元数据

- 新增 GitHub 备份只读校验与合并恢复：正式恢复前自动生成本地 ZIP，不删除本地独有记忆，并支持 Windows 深路径。
- 对齐持久媒体：`hold` / `trace` / 网页端可上传附件，Markdown 只存稳定引用；桌面、蜂窝和手机端可查看，备份恢复会一并校验附件。
- 对齐 `why_remembered`、可追加 `meaning`、`source_tool` 和 `grow_batch_id`；这些体验元数据可搜索但不改变衰减分数。
- 增加 ngrok 兼容响应头；保留本 fork 更严格的管理员令牌、同源 Cookie 与 CORS 安全边界。

## 2026-07-15 · 上游 2.7.0 体验与可靠性对齐

### 修复 / Fixed

- 同一记忆桶的更新、删除、恢复、归档、取消归档、永久删除和激活写入增加跨线程、跨事件循环、跨进程锁，避免并发读改写互相覆盖。
- embedding 补账熔断区分不同桶；同一条被供应商过滤的“毒内容”反复失败不再拖住所有新记忆。
- `config.yaml` 持久化改为跨进程锁定的原子读改写，并在写后回读验证。
- 导入断点身份绑定格式与分块策略；逐字导入恢复时精确去重；提取、落库和原文附加失败进入前端可见错误列表；移除旧的 12000 字符静默截断，超出 token 安全上限时明确告警。
- Windows Git 备份复制、遍历、哈希和 manifest 写入支持长路径及原子落盘。

### 优化 / Improved

- `breath()` 固定为零参数浮现，并拆出 `breath_search` 与 `breath_advanced`；`breath_legacy` 保留旧参数入口。
- Dashboard 搜索响应增加 `vector_status`、`vector_notice` 与 `X-Semantic-Search`，语义服务降级不再伪装为“零结果”。
- 向量余弦检索改为 NumPy 批量矩阵计算，并隔离坏向量及维度不匹配数据。

### 测试 / Tests

- 新增同桶并发、配置并发、导入截断与恢复去重、毒条目熔断、向量批处理、语义降级状态和 Windows 深路径备份回归；全量结果为 `92 passed, 7 skipped, 5 subtests passed`。

## 2026-07-14 · 安全对齐批次二（正确性 / 数据安全）

### 修复 / Fixed

- 搜索可见性过滤移到 `limit` 切片之前，feel / noise / pinned / trash 不再挤占有效结果窗口；命中统计也只记录调用方实际可见的结果。
- `hold()` 显式传入的 valence / arousal 优先于自动分析，合法的 `0.0` 不再被中和值覆盖；Feel 模式保留调用方 tags；grow 与合并路径同步修复 `0.0 or default`。
- 完整 bucket ID 查询直接返回原文，不再调用脱水模型。
- 脱水缓存绑定 base URL + model；embedding 新写入绑定 endpoint + model，并兼容 `bge-m3` / `bge-m3:latest` 别名。
- 软删除统一移出活跃向量索引，恢复时自动补建；向量检索额外防御性排除 trash。
- 活跃桶缓存改为每秒检查 path / mtime / size，Obsidian、Git 或手工改盘最多约 1 秒可见；可通过 `OMBRE_EXTERNAL_CHANGE_POLL_SECONDS` 调整。
- Windows 原子写入增加长路径前缀，同时保留 PermissionError 短重试。
- Git 备份只复制 Markdown 源数据，`runtime_config.json` 深度脱敏，排除 SQLite/缓存/日志，并生成逐文件 SHA256 manifest。
- 配置 API 的字符串 `"false"` 不再被 `bool()` 误判为开启；搜索 limit 加硬上限。

### 测试 / Tests

- 新增 `pytest.ini`，异步测试统一使用 auto 模式；修正 protected score 的过期断言。
- 新增离线回归：过滤前切片、外部编辑缓存、模型/endpoint 缓存隔离、备份脱敏与 manifest、显式零坐标、Feel tags、原文 ID 读取、软删除向量一致性。
- 全仓结果：`82 passed, 7 skipped, 5 subtests passed`。

## 2026-07-10 · 上游对齐批次一（v2.3.19 → v2.5.3 修复类）

从上游两条版本线（v2.4.x / v2.5.x）移植的修复与健壮性改动。功能类（OAuth、multi-owner、目录模式等）不在本批，另行评估。

### 修复 / Fixed

- **记忆桶原子写**（上游 2.5.0）：所有桶 `.md` 写入改为 临时文件 + fsync + `os.replace`，进程被杀/断电/磁盘写满不再产生半截文件；`runtime_config.json`、导入进度、命中统计等原有的手搓原子写一并收敛到同一 helper（补上 fsync）。Windows 下目标文件被同步盘/杀软短暂占用导致 `os.replace` 报 PermissionError 时短重试 3 次，仍失败则报错（不回退成截断式写入）。归档/回收站/恢复等移动操作补防撞名：目标已有同名文件时旁置为 `.stale-<时间戳>`（不带 `.md` 后缀，桶扫描自动忽略），不覆盖、不报错。
- **时间戳时区统一**（上游 2.5.3）：`created`/`last_active` 带 `Z` 后缀（本仓写入格式）或 UTC offset（导入数据）时，衰减引擎与检索时间新鲜度曾因 naive/aware 相减 `TypeError` 一律走 30 天兜底——**衰减打分对几乎所有桶失真，自动归档事实上从未生效**。统一经 `parse_iso_datetime`（naive UTC 口径）解析后恢复真实天数。注意：修复后第一次衰减周期，长期未激活的低重要度桶会按设计归档（归档可恢复、关键词仍可检索）。
- **桶元数据时间字段序列化归一**（上游 2.4.4）：YAML 把不带引号的时间戳解析成 `datetime` 对象，曾导致 dashboard 列表/详情、导入页、`dream()` 排序在遇到上游迁移桶时报错。读取层统一归一为 ISO 字符串。
- **LLM 回复 JSON 宽松解析**（上游 2.4.6，提取策略有意比上游严）：新增 `clean_llm_json()`，容忍 DeepSeek 等模型在 JSON 前后附带说明文字。整体可解析时原样返回；否则取**最后一个**平衡 JSON 值——上游取第一个，会把说明文字里的格式示例（如「请按 `{"k": 0.5}` 的格式」）当成结果吞进去。接入打标、日记拆分、正文重写、批量导入抽取五个解析点。
- **配置布尔安全归一**（上游 2.5.3）：YAML/JSON 里写成带引号的 `"false"`/`"0"` 不再被当作开启。涉及 embedding 开关、检索模式开关、auto_merge。

- **家族自动重建的时区偏移**：`built_at`（本地时间）与桶 `created`（UTC）曾直接字符串比较，JST 环境下"有没有新桶"的判断被压住最多 9 小时。families 状态时间戳改 UTC+Z 口径、比较改解析后进行；旧格式状态视为需要重建，一次收敛。

### 优化 / Improved

- **检索响应性能**（上游 2.5.0）：`list_all()` 活跃桶集内存缓存（写操作失效、touch/时间涟漪就地更新、命中返回逐桶拷贝防检索打分字段污染缓存、60 秒 TTL 兜底外部直接改盘的场景）；breath 浮现结果分波并发脱水（每波 4 条、波间检查 token 预算，不为被裁剪的结果整批调用 LLM）；touch 及时间涟漪移出 breath 响应路径改为后台补账。语义保留：last_active / activation_count / 涟漪照旧；取舍：进程在响应后、后台补账前被杀（重启/部署瞬间）会丢那一次激活计数，属可自愈的启发式数据。
- **embedding 进程内 LRU 查询缓存**（上游 2.4.13）：同一模型同一文本短时间内的重复向量请求只打一次 API。
- **API 超时可配**（上游 2.4.5）：新增 `dehydration.timeout_seconds` / `embedding.timeout_seconds`，环境变量 `OMBRE_COMPRESS_TIMEOUT_SECONDS` / `OMBRE_EMBED_TIMEOUT_SECONDS`。默认值不变（60 / 30 秒）。

### 已核对、无需移植 / Verified not applicable

- 上游 2.4.13 的"写入路径双重 embedding"：本 fork 的 `BucketManager` 不在内部生成向量，显式调用是唯一路径，无此问题。
- 上游 2.5.2 的"hold 降级保存"：本 fork 写入链路已是失败安全（打标失败用默认元数据照存正文；embedding 失败桶照写、事后 backfill；合并失败回落新建桶）。上游"合并只追加原文不走 LLM"的行为变更未采纳，本 fork 保留 LLM 智能合并。

### 测试 / Tests

- 新增 `tests/test_upstream_align_tier1.py`（21 例）：原子写、撞名旁置、时区解析各输入形态、clean_llm_json、时间字段归一、布尔/数值归一、活跃集缓存全生命周期、embedding LRU。
- 存量测试与基线逐项一致（3 failed / 20 errors 为预先存在的 pytest 9 环境兼容问题与已知的 permanent 打分期望值噪音，非本批引入）。
