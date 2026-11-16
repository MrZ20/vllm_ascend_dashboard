# 镜像构建监控与通知（Image Build Monitor）设计

## 1. 背景与目标

团队每天定时触发 vllm-ascend 镜像构建流程，目前依赖人工查看 Action 结果，存在：

- 构建失败发现滞后；
- 定时任务未触发/延迟无人感知；
- 构建长时间卡住无人处理；
- 通知方式单一，无法定向到责任人。

本模块目标：在看板内提供镜像构建监控与主动通知能力，覆盖 `failure / missing / stuck` 三类异常，支持邮件通知、告警确认/恢复、冷却与去重。

## 2. 数据来源

复用现有发布件管理采集链路：

- `release_workflows`：已注册的 GitHub Action workflow（含 repo、workflow_id、name、path）。
- `release_build_runs`：每轮构建 run（status/conclusion、event、branch、created_at_remote、github_run_id）。
- `release_build_groups`：Job 维度明细，用于告警详情下钻。

监控模块不重复调用 GitHub，只基于已采集数据做规则判定，避免 API 限流。

## 3. 数据模型

### 3.1 image_build_monitor_targets（监控目标）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | BIGINT PK | |
| release_workflow_id | BIGINT FK unique | 关联 release_workflows |
| enabled | BOOL | 是否启用 |
| schedule_cron | VARCHAR(100) | 期望调度 cron，5 段；为空则不做 missing 检测 |
| timezone | VARCHAR(64) | 默认 Asia/Shanghai |
| grace_minutes | INT | 调度容忍窗口，默认 30 |
| lookback_days | INT | 失败/卡住回溯天数，默认 2 |
| notify_emails | JSON | 收件人列表 |
| notify_on_failure | BOOL | 失败通知 |
| notify_on_missing | BOOL | 未触发通知 |
| notify_on_stuck | BOOL | 卡住通知 |
| stuck_threshold_minutes | INT | 运行超过该时长视为卡住，默认 180 |
| cooldown_minutes | INT | 同一告警重复提醒间隔，默认 60 |
| last_checked_at | DATETIME | 上次检测时间 |
| created_at / updated_at | DATETIME | |

### 3.2 image_build_alerts（告警事件）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | BIGINT PK | |
| target_id | BIGINT FK | |
| release_workflow_id | BIGINT | 冗余便于查询 |
| github_run_id | BIGINT nullable | missing 类型为空 |
| alert_type | VARCHAR(20) | failure/missing/stuck |
| severity | VARCHAR(20) | critical/warning |
| status | VARCHAR(20) | open/acknowledged/resolved |
| title / message | VARCHAR | 展示内容 |
| run_url | VARCHAR(500) | GitHub run 链接 |
| branch / event | VARCHAR | |
| dedupe_key | VARCHAR(255) unique | 去重键 |
| first_detected_at / last_detected_at | DATETIME | |
| resolved_at / resolved_by | | |
| notified_at / last_notified_at | DATETIME | |
| notification_success | BOOL | |
| notification_error | TEXT | |
| details | JSON | 原始判定上下文 |

去重键：

- failure: `failure:{workflow_id}:{github_run_id}`
- missing: `missing:{workflow_id}:{expected_fire_iso}`
- stuck: `stuck:{workflow_id}:{github_run_id}`

## 4. 判定规则

检测周期默认每 10 分钟，可在 `.env` 通过 `IMAGE_BUILD_MONITOR_INTERVAL_MINUTES` 调整。

1. **failure**
   - 最近 `lookback_days` 内、状态属于 `failure/timed_out/startup_failure/cancelled` 的 run。
   - 每个 run 生成唯一告警；若后续出现更晚的成功 run，则自动置为 resolved。

2. **missing**
   - 使用 APScheduler `CronTrigger.from_crontab(schedule_cron, timezone)` 计算上一次应触发时间 `prev_fire`。
   - 当 `now >= prev_fire + grace_minutes`，且不存在 `event=schedule` 且 `created_at_remote >= prev_fire - 5min` 的 run 时告警。
   - 后续补跑出现（created_at_remote ≥ prev_fire）则自动 resolved。

3. **stuck**
   - `status` 属于 `queued/in_progress/waiting/requested/pending` 且 `created_at_remote` 早于 `now - stuck_threshold_minutes`。
   - run 结束后自动 resolved。

## 5. 通知策略

- 渠道：复用 `infrastructure/core/email.py` 与 `smtp_config`，首期仅邮件；后续可扩展企微/钉钉/飞书。
- 收件人：监控目标配置的 `notify_emails`，支持多人。
- 触发：新告警立即发送；同一条 open 告警超过 `cooldown_minutes` 后再次提醒。
- 邮件内容：工作流、仓库、分支、run 链接、异常类型、首次/最近发现时间、错误摘要、处理入口。
- 发送结果写回告警 `notification_success/notification_error`，失败不阻塞检测。

## 6. API 设计（前缀 `/api/v1/image-build-monitor`）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/overview?days_back=7` | 汇总卡片 + 目标健康度 + 最近告警 |
| GET | `/targets` | 监控目标列表（含 workflow 信息） |
| GET | `/candidates` | 可添加的 ReleaseWorkflow 列表 |
| POST | `/targets` | 新建/按 workflow 更新目标 |
| PUT | `/targets/{id}` | 更新目标 |
| DELETE | `/targets/{id}` | 删除目标 |
| GET | `/targets/{id}/runs?limit=20` | 目标最近 run 时间线 |
| GET | `/alerts?status=&alert_type=&page=` | 告警分页列表 |
| POST | `/alerts/{id}/ack` | 确认告警 |
| POST | `/alerts/{id}/resolve` | 手动恢复 |
| POST | `/alerts/{id}/renotify` | 重新发送通知 |
| POST | `/check` | 立即执行一次检测 |

权限：登录用户可查看；新增/修改/删除/检测等写操作要求 admin。

## 7. 前端设计

新增路由 `/image-build-monitor`，导航位于「研发交付」分组，名称「镜像构建监控」。

页面结构：

1. 顶部操作：时间范围、立即检测、新增监控。
2. 汇总卡片：监控目标数、今日运行、失败、未触发、卡住、未处理告警。
3. 监控目标表：workflow、仓库、cron、容忍窗口、收件人、最近状态、操作（编辑/删除/查看 run）。
4. 告警列表：类型、级别、状态、workflow、标题、首次/最近发现、通知结果、操作（确认/恢复/重发）。
5. 配置弹窗：workflow 选择、cron、时区、容忍窗口、卡住阈值、收件人、通知开关、冷却时间。
6. 最近 run 抽屉：时间线展示状态、分支、耗时、GitHub 链接。

## 8. 调度与运维

- 在独立 scheduler 进程注册 `image_build_monitor_check` 定时任务。
- `.env`：
  - `IMAGE_BUILD_MONITOR_ENABLED=true`
  - `IMAGE_BUILD_MONITOR_INTERVAL_MINUTES=10`
- 迁移脚本：`database/migrations/image_build_monitor.py`，由 `database/migrations/migrate.py` 统一执行，不触碰用户数据。

## 9. 实施阶段

- 第一阶段（本次）：failure/missing/stuck 检测、邮件通知、目标配置、告警闭环、看板页面。
- 第二阶段：IM Webhook、责任人自动路由（按 branch/hardware/owner）、告警静默窗口。
- 第三阶段：AI 失败摘要接入、告警趋势与 SLA 统计。

## 10. 本地开发与验证

### 真实 GitHub 数据

“立即检测”会先调用 GitHub Actions API 同步每个启用监控目标最近 2 天的运行记录，再执行规则判定。同步失败不会中断判断，仍会基于本地已采集数据生成告警，并在返回结果的 `errors` 中提示原因。

本地 `.env.local` 中的 `GITHUB_TOKEN` 必须有效。Token 失效时仅开发环境（`DEBUG=true`）会降级为匿名访问公开仓库；匿名访问共享出口 IP 很容易触发 60 次/小时限流。因此本地验证请通过「管理 → 系统配置 → 编辑 GitHub」配置有效 Token。

### Cron 可视化配置

新增/编辑监控目标时，Cron 输入框右侧提供「可视化」按钮，支持：

- 每小时第 N 分钟
- 每天指定时间
- 每周指定星期与时间
- 每月指定日期与时间
- 自定义 5 段表达式

生成结果会写回 Cron 输入框。

### 真实邮件测试

监控页面顶部「邮件测试」可向指定邮箱发送一封真实告警模板邮件：

- 使用「邮件服务」中已配置的 SMTP 参数；
- 内容与正式告警模板一致；
- 可选择用于渲染邮件的监控目标；
- 返回发送成功/失败原因，便于校验 SMTP 与邮件格式。

本地 SMTP 未配置时，测试接口会明确返回“SMTP 未配置”。
