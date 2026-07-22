# FinAlly 审查修复计划

状态：本轮实施完成（仅 DATA-05、OPS-06 需独立架构/产品决策）  
创建：2026-07-10  
依据：全仓代码、测试、部署与文档审查

## 目标与边界

本轮将 FinAlly 明确加固为可安全运行的 `local-demo` / 单副本
`classroom-server` 模块化单体：修复可被利用的信任边界、双市场财务
正确性、行情/SSE 一致性、关键数据完整性、CI 与运行文档。

不会把现有单体强行拆成微服务。Postgres、Redis、队列 worker 与多副本
部署属于后续生产化阶段；在实现前，server 模式必须显式拒绝不受支持的
多 worker / 无持久盘运行方式。

## 状态定义

- `pending`：尚未开始。
- `in-progress`：正在修改并补测试。
- `done`：代码和针对性测试完成，待全套验证。
- `deferred`：需要外部基础设施或产品决策；会以运行时限制和文档说明
  防止被误当作已支持能力。

## A. 信任边界与交易安全

| ID | 状态 | 修复项 | 验收标准 |
| --- | --- | --- | --- |
| SEC-01 | done | 引入运行模式；默认仅 localhost，server 模式要求显式安全配置。 | 默认启动不对外暴露；不安全 server 配置失败。 |
| SEC-02 | done | `/api/season/reset` 采用审计、幂等确认与事务内 current season 读取；管理员令牌在 classroom-server 强制（local-demo 可选但会校验）。 | server 模式匿名/普通用户 401/403；并发 reset 不产生双 current season。 |
| SEC-03 | done | 将 name-only 身份限制为 local demo；server 模式使用带密钥验证的登录。 | 既有用户名无法被无凭证接管。 |
| SEC-04 | done | classroom-server 下 Guest 不得创建/使用 API Key（local-demo 保留 P3 单用户契约）；受限 key 对 Chat 一律 403，对 Rule/Strategy 创建按 ticker 白名单与数量上限校验载荷（不可解析/缺字段则拒绝）。 | 交易入口按 key 约束执行 guardrails；已知限制：延迟成交不计日上限（见 SECURITY.md）。 |
| SEC-05 | done | 限制 Bearer body、使用真正的审计摘要，并拒绝非有限数值。 | 超大 body/NaN/Infinity 被 4xx 拒绝，审计无原始业务正文。 |

## B. 市场、赛季与策略一致性

| ID | 状态 | 修复项 | 验收标准 |
| --- | --- | --- | --- |
| DATA-01 | done | 新用户从 MarketProfile 取得 seed cash 和默认 watchlist。 | CN 新用户获 100,000 与 A 股标的。 |
| DATA-02 | done | 行情订阅覆盖持仓、挂单、规则和 live strategy，估值提供 stale 回退。 | 删除自选后关键 ticker 仍有行情需求。 |
| DATA-03 | done | 行情 freshness 状态进入 health/readiness，并阻止陈旧报价成交。 | provider 异常时系统降级且拒绝新成交。 |
| DATA-04 | done | reset 原子暂停策略并归档/隔离赛季数据。 | 新赛季不继承 live strategy 的虚假持仓。 |
| DATA-05 | deferred | 策略/人工交易的 lot allocation 与完整按赛季归因。 | 需产品确认策略共享持仓语义后独立迁移。 |

## C. 前端正确性与可用性

| ID | 状态 | 修复项 | 验收标准 |
| --- | --- | --- | --- |
| FE-01 | done | SSE 定义 heartbeat，客户端以 heartbeat 保活。 | 午休/闭市不反复重连。 |
| FE-02 | done | 所有金额、数量、图表基线从 MarketProfile 获取。 | CN 不出现 $/10,000 误基线。 |
| FE-03 | done | ticker 切换重置 EventArchive 分页状态。 | 不混入上一 ticker 的事件。 |
| FE-04 | done | 统一 API error state 与 SSE payload runtime 校验。 | 网络/契约错误可见、无效 frame 不污染 store。 |
| FE-05 | done | 修复关键键盘语义、CN `lang`、reduced motion、图表归属。 | 核心导航/表格可键盘操作，许可归属可见。 |
| FE-06 | done | 响应式布局与按 ticker structural sharing。 | 窄屏/768/1280/1600 组件契约与 selector 渲染测试通过。 |

## D. 可运维性、CI 与文档

| ID | 状态 | 修复项 | 验收标准 |
| --- | --- | --- | --- |
| OPS-01 | done | 集中 Settings、完整 `.env.example`、startup validation 与脱敏 effective config。 | 文档覆盖所有公开配置，非法配置启动失败。 |
| OPS-02 | done | readiness、迁移版本、SQLite 备份/恢复说明与保留策略。 | 探针能区分活着与可服务，升级有恢复步骤。 |
| OPS-03 | done | 启动脚本重建/健康失败非零退出；隔离双市场 volume。 | 不复用旧镜像，不会健康失败却成功返回。 |
| OPS-04 | done | PR 静态门禁与 US/CN smoke；失败归档报告。 | lint/typecheck/test/smoke 能阻断 PR。 |
| OPS-05 | done | 建立 `CURRENT.md`，同步 README/agent 指引/前端 README。 | 新贡献者只读当前真相源即可正确启动与开发。 |
| OPS-06 | deferred | Postgres、Redis、leader/worker、横向扩展。 | server 文档明确仅支持单副本持久盘。 |

## 执行顺序

1. A：先消除公网共享时的直接接管与全局破坏风险。
2. B/C：保证 US/CN 资金、报价、SSE 和页面信息一致。
3. D：使修复能持续被 CI、部署和文档验证。
4. 全套后端、前端、US/CN E2E 回归；复审所有 `done` 项。

## 交付物

- 代码与回归测试。
- 当前架构、运行模式、安全/运维和 API 契约文档。
- 本文件中的最终状态与验证记录。

## 执行与验证记录（2026-07-10）

- 后端：`1351 passed`；Ruff 通过；`pytest --cov=app --cov-fail-under=90`
  为 91.22%。新增 security/runtime、CN profile、stale quote、readiness、
  schema ledger 与 SSE heartbeat 回归。
- 前端：`62 suites / 486 tests` 通过；TypeScript、生产 `src` ESLint 和静态
  export 构建通过。新增 CN 金额、EventArchive 切换、SSE payload/heartbeat、
  API 错误态、响应式和 ticker structural-sharing 回归。
- 部署：US、CN、两份 E2E Compose 解析通过；shell/JSON/diff/密钥模式扫描
  通过。Docker US smoke 覆盖 fresh start、SSE 恢复、交易 `3/3`；CN smoke
  覆盖 profile、中文渲染和整手拒绝 `3/3`，测试网络使用非 `app` 的内部别名
  规避新版 Chromium 的 HTTPS 自动升级，并已清理临时容器/卷。
- CI：PR 运行后端 Ruff/覆盖率、前端 lint/typecheck/build/覆盖率及 US/CN
  smoke；完整 E2E 保留 nightly/manual。依赖审计会在 GitHub runner 中联网执行。

仍需独立立项的事项：

- `DATA-05`：需要先确定人工仓位与多个策略共享仓位时的归因语义，再实施
  lot allocation 和跨赛季历史迁移。
- `OPS-06`：Postgres、Redis、leader/worker 和多副本部署需要外部基础设施
  方案；当前 server 模式显式限制为单副本持久盘。
- 完整 nightly E2E（含真实时间 11 分钟级场景）未在本机执行；PR smoke 已
  通过。测试 mock 的全量 ESLint 历史 `any` 债务也仍按生产源码门禁分阶段
  治理。
