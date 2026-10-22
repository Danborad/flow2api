# Flow2API 新版 Cookie-first 认证与 Flow 前端 RPC 迁移指南

> 用途：将仍依赖旧版 Google Labs NextAuth Session Token、Labs tRPC 或 AI Sandbox REST 接口的 Flow2API 部署，迁移到当前 `flow.google.com` 使用的 Cookie/XSRF 与同源 `batchexecute` RPC。
>
> 目标读者：负责在另一台服务器上实施修复的智能体或开发者。
>
> 安全要求：不要把真实 Cookie、Connection Token、代理凭据、验证码 API Key、邮箱或项目 UUID 写入代码、测试、日志、提交记录或迁移文档。

## 1. 迁移结论

旧认证链路已经失效，不应继续修补：

- 不再调用 `labs.google/fx/api/auth/session`。
- 不再把 `__Secure-next-auth.session-token` 当作主要认证凭据。
- 不再依赖旧 Labs OAuth、NextAuth ST、Labs tRPC 或旧 AI Sandbox REST 上传/视频接口。
- 不通过浏览器自动点击生成来降级，因为那会丢失参考图、图生图、视频、媒体轮询等完整能力。

新版链路的核心是：

1. 用户在持久化 Chromium Profile 中手动登录 `flow.google.com`。
2. Host Agent 通过 CDP/Playwright 导出结构化 Google/Flow Cookie 快照。
3. Flow2API 后端访问 `https://flow.google.com/projects` 验证 Cookie、识别邮箱并接收 Cookie 轮换。
4. 每次业务调用先加载对应 Flow 页面，从 bootstrap HTML 动态提取：
   - 前端版本 `bl`，来自 `boq_labs-ai-sandbox-frontend_*`
   - 前端会话 `f.sid`，来自 `FdrFJe`
   - XSRF `at`，来自 `WIZ_global_data.SNlM0e`
5. 后端携带 Cookie、动态 XSRF 和同源请求头调用 `flow.google.com/_/AiSandboxAngularFrontend/data/batchexecute`。
6. 响应中的 Cookie 轮换必须回写数据库；回写时使用 CAS，禁止较旧请求覆盖较新的浏览器 Cookie 快照。

认证主凭据是 `google_cookies`。数据库中的 `st` 和 `at` 只用于兼容现有 Flow2API 内部接口：

```text
st = frontend-<sha256(cookie_snapshot) 前 32 位>
at = flow-frontend-cookie
```

这两个值不是 Google 下发的真实 ST/AT，不能直接放进 `Authorization: Bearer ...` 调用旧 REST 接口。

## 2. 不可破坏的实现约束

实施智能体必须遵守以下约束：

- 保留原有 OpenAI/Gemini API 路由、模型别名、任务系统、项目池、代理、验证码、管理界面和媒体处理能力。
- 只替换认证与上游 Flow 传输层，不整体重写业务 API。
- Cookie 必须保留 `domain`、`hostOnly`、`path`、`secure`、`httpOnly`、`sameSite`、过期时间和重复同名 Cookie，不能只存成普通字典。
- 不得把所有 Cookie 粗暴拼成一个全局 Header；必须按目标 URL 的域、路径、Secure 和过期规则选择 Cookie。
- 每次请求必须从当前 bootstrap 动态获取 `bl`、`f.sid` 和 XSRF，不能硬编码。
- 验证码 token、User-Agent 和代理出口必须保持一致，否则可能触发 `UNUSUAL_ACTIVITY` 或 reCAPTCHA evaluation failed。
- Cookie 轮换、删除和重定向过程中下发的 `Set-Cookie` 必须被完整保存。
- Cookie 快照并发回写必须使用比较并交换，旧请求不得覆盖新快照。
- 扩展继续只负责验证码。账号导入保持关闭，不新增扩展 `cookies` 权限；Cookie 同步由 Host Agent 的 CDP 完成。
- 不记录原始 Cookie。日志和状态文件只能保存 Cookie 数量、短指纹、Token ID、邮箱和项目 ID 等非凭据数据。

## 3. 推荐模块边界

参考实现将功能拆为以下模块。其他代码库可以改名，但职责不要混在一起。

| 模块 | 参考文件 | 职责 |
| --- | --- | --- |
| Cookie 解析与校验 | `src/services/browser_cookie_utils.py` | 解析结构化快照、URL 作用域筛选、序列化 Header、登录 Cookie 组校验 |
| Cookie Jar | `src/services/flow_cookies.py` | 保留作用域、重复 Cookie、轮换和删除，导出新快照 |
| 登录验证 | `src/services/protocol_login.py` | 用 Cookie 请求 Flow 页面、识别邮箱、接收轮换 Cookie |
| Flow RPC transport | `src/services/flow_frontend.py` | bootstrap、动态 XSRF、`batchexecute`、帧解析、错误转换 |
| 当前 Flow 功能 | `src/services/flow_current.py` | 项目、余额、图片、视频、媒体 RPC 参数与响应适配 |
| 对外 Client | `src/services/flow_client.py` | 让当前 RPC mixin 覆盖旧传输，同时保留原业务辅助逻辑 |
| Token 生命周期 | `src/services/token_manager.py` | Cookie-first 添加、刷新、校验、代理上下文和数据库回写 |
| 数据库 | `src/core/database.py`、`models.py` | Cookie 字段、刷新状态和 CAS 更新 |
| 浏览器协调 API | `src/api/admin.py` | `/api/plugin/reconcile-browser-account` |
| Host Agent | 独立项目 `scripts/core.py` | CDP 导出 Cookie、项目和邮箱，调用协调 API |

推荐组合方式：

```python
class FlowClient(CurrentFlowClientMixin, LegacyFlowClientBase):
    """当前 Flow RPC 覆盖旧传输，原业务辅助能力继续复用。"""
```

不要把整个旧 `flow_client.py` 替换掉。通过 mixin 覆盖认证、上传、生成、轮询和媒体方法，可以最大限度保留现有路由、模型映射、代理与验证码实现。

## 4. Cookie 快照格式

数据库字段 `google_cookies` 存储 JSON 字符串，内容是 Cookie 对象数组：

```json
[
  {
    "name": "SID",
    "value": "<redacted>",
    "domain": ".google.com",
    "hostOnly": false,
    "path": "/",
    "secure": true,
    "httpOnly": true,
    "sameSite": "None",
    "expirationDate": 1893456000
  },
  {
    "name": "OSID",
    "value": "<redacted>",
    "domain": "flow.google.com",
    "hostOnly": true,
    "path": "/",
    "secure": true,
    "httpOnly": true
  }
]
```

不能只保存：

```json
{"SID":"...","OSID":"..."}
```

普通字典会丢失：

- 同名 Cookie 的多个 domain/path 版本
- host-only 与 domain Cookie 的区别
- Path 优先级
- Secure 和过期状态
- 服务端 Cookie 删除与轮换语义

### 4.1 必需 Cookie 组

对 `https://flow.google.com/projects` 进行 URL 作用域筛选后，至少需要：

Flow 会话组，满足一个即可：

```text
OSID
__Secure-OSID
```

Google 账号组，满足一个即可：

```text
SID
HSID
SSID
APISID
SAPISID
__Secure-1PSID
__Secure-3PSID
```

注意：同名 Cookie 如果只存在于 `.youtube.com`，不能视为 Flow 的账号 Cookie；它不会发送给 `flow.google.com`。

### 4.2 URL 作用域规则

生成 Cookie Header 前必须检查：

- partitioned Cookie：普通服务端 Cookie Jar 无法准确复刻时应跳过。
- domain：host-only 必须精确匹配；domain Cookie 允许子域。
- path：请求路径必须匹配 Cookie Path，长 Path 优先。
- secure：只对 HTTPS 发送。
- expires：过期 Cookie 不发送。
- 重复项：保留顺序和重复 name/value，不要合并成字典。

核心接口建议：

```python
parse_browser_cookie_payload(raw) -> list[dict]
cookie_pairs_for_url(raw, target_url) -> list[tuple[str, str]]
serialize_cookie_header_for_url(raw, target_url) -> str
validate_flow_cookie_storage(raw) -> str
```

## 5. Host Agent：从浏览器同步账号

### 5.1 浏览器要求

- 使用独立、持久化 Chromium Profile。
- 用户手动登录 Google 和 Flow。
- Chromium 开放仅本机可访问的 CDP 端口，例如 `127.0.0.1:9223`。
- 打开 `https://flow.google.com/projects` 并进入一个项目页。
- 项目页地址应类似：

```text
https://flow.google.com/project/<UUID>
```

### 5.2 CDP 快照算法

伪代码：

```python
browser = playwright.chromium.connect_over_cdp("http://127.0.0.1:9223")
snapshot = []

for context in browser.contexts:
    for cookie in context.cookies():
        if cookie_is_partitioned(cookie):
            continue
        if not cookie.name or not cookie.value or not cookie.domain:
            continue
        snapshot.append({
            "name": cookie.name,
            "value": cookie.value,
            "domain": cookie.domain,
            "hostOnly": cookie.hostOnly or not cookie.domain.startswith("."),
            "path": cookie.path or "/",
            "secure": cookie.secure,
            "httpOnly": cookie.httpOnly,
            "sameSite": cookie.sameSite,
            "expirationDate": cookie.expires if cookie.expires != -1 else omitted,
        })
```

参考实现从专用 Profile 导出全部非分区 Cookie，再由后端按 URL 严格筛选。更小权限的实现可以只导出 `google.com` 及其子域，但必须包含 `.google.com` 和 `flow.google.com`。

### 5.3 提取账号与项目

从 Flow 页面 HTML 中提取已登录邮箱：

```regex
"oPEP7c"\s*:\s*"([^"\\]*@[^"\\]+)"
```

从已打开页面提取项目：

```regex
^https://flow\.google\.com/projects?/([0-9a-fA-F-]{36})(?:[/?#]|$)
```

项目名称可以使用页面 title。新账号第一次导入必须提供项目 ID；已有账号可以沿用数据库项目。

### 5.4 浏览器协调 API

Host Agent 调用：

```http
POST /api/plugin/reconcile-browser-account
Authorization: Bearer <connection-token>
Content-Type: application/json
```

请求体：

```json
{
  "google_cookies": "<结构化 Cookie JSON 字符串>",
  "email": "user@example.invalid",
  "project_id": "00000000-0000-0000-0000-000000000000",
  "project_name": "Current Flow project",
  "extension_route_key": "flow-<random-route-key>",
  "proxy_url": "<可选，账号固定代理>"
}
```

成功响应：

```json
{
  "success": true,
  "action": "updated",
  "token_id": 1,
  "email": "user@example.invalid",
  "project_id": "00000000-0000-0000-0000-000000000000",
  "credits": 100,
  "cookie_snapshot_fingerprint": "0123456789abcdef"
}
```

Host Agent 状态文件只保存：

- Cookie 数量
- SHA-256 短指纹
- API 状态
- Token ID、邮箱、项目 ID
- 写后校验结果

不要保存 Cookie 原文或请求 Authorization。

### 5.5 浏览器重启后保持登录

Google 账号 Cookie 中包含会话 Cookie。Chromium 不启用会话恢复时，服务重启可能导致用户被登出。

推荐 Chromium policy：

```json
{
  "AutofillAddressEnabled": true,
  "BlockThirdPartyCookies": false,
  "BrowserSignin": 1,
  "RestoreOnStartup": 1,
  "CookiesAllowedForUrls": [
    "https://[*.]google.com",
    "https://flow.google.com"
  ],
  "PasswordManagerEnabled": true
}
```

启动参数增加：

```text
--restore-last-session
```

服务停止时应通过 CDP `Browser.close` 优雅退出，避免直接 kill Chromium。

## 6. 后端登录验证

`detect_account_identity()` 应执行真实在线验证，而不是仅检查 Cookie 名称。

验证流程：

1. 解析和校验结构化 Cookie。
2. 构建支持作用域和轮换的 Cookie Jar。
3. 使用与生成请求一致的代理出口请求：

```text
GET https://flow.google.com/projects
Referer: https://flow.google.com/
浏览器 impersonation: chrome
```

4. 只接受最终域名为 `flow.google.com` 的响应。
5. 状态必须为 HTTP 200。
6. HTML 必须包含当前 Flow 前端 marker：

```text
boq_labs-ai-sandbox-frontend_
```

7. HTML 必须能提取 `oPEP7c` 邮箱。
8. 使用 libcurl 的完整 Cookie 列表重新生成快照，必须包含重定向期间的 Cookie 更新和删除。
9. 生成本地兼容身份：

```python
st = "frontend-" + sha256(refreshed_cookie_json).hexdigest()[:32]
```

邮箱以 Flow 页面返回值为准，不要盲信 Host Agent 传入值。

## 7. 数据库与 Token 模型

Token 表至少增加或确认以下字段：

```text
protocol_mode             TEXT DEFAULT 'session'
google_cookies            TEXT DEFAULT ''
login_account             TEXT DEFAULT ''
login_password            TEXT DEFAULT ''
proxy_url                 TEXT DEFAULT ''
auto_refresh_enabled      BOOLEAN DEFAULT true
refresh_interval_minutes  INTEGER DEFAULT 120
last_st_refresh_at        TIMESTAMP NULL
last_st_refresh_result    TEXT DEFAULT ''
extension_route_key       TEXT DEFAULT ''
```

Cookie 模式账号推荐值：

```text
protocol_mode = protocol
st = frontend-<cookie sha256 prefix>
at = flow-frontend-cookie
google_cookies = <JSON snapshot>
last_st_refresh_result = success
```

### 7.1 协调账号逻辑

`reconcile-browser-account` 应按以下顺序匹配已有账号：

1. 已验证邮箱匹配。
2. 请求显式携带的 `token_id`。
3. 兼容迁移：数据库只有一个尚未填充 `google_cookies` 的旧 Token 时复用它。
4. 否则创建新 Token；创建新 Token 时必须有 Flow 项目 ID。

更新字段至少包括：

```text
st
at
at_expires
google_cookies
protocol_mode
email
login_account
last_st_refresh_at
last_st_refresh_result
current_project_id/current_project_name（有值时）
extension_route_key（有值时）
```

然后使用 Cookie 模式读取余额并写回 `credits`、`user_paygate_tier`。

### 7.2 `st_to_at()` 兼容适配

Cookie 模式中的 `st_to_at()` 不再访问旧认证接口，而是：

1. 校验或加载 `google_cookies`。
2. 调用当前 Flow RPC `nzlxg` 验证登录态并读取余额。
3. 返回兼容结构：

```python
{
    "access_token": "flow-frontend-cookie",
    "expires": now_plus_55_minutes_iso,
    "user": {},
    "credits": credits,
    "userPaygateTier": tier,
    "google_cookies": rotated_cookie_snapshot,
}
```

`expires` 是本地刷新调度兼容值，不是 Google AT 的真实到期时间。

### 7.3 管理界面的过期显示

Cookie 模式不能继续用旧 AT 的 `at_expires` 显示“已过期”。

推荐规则：

- `at == flow-frontend-cookie` 且存在 `google_cookies` 时，认证类型视为 Cookie。
- `last_st_refresh_result == success`：显示 `Cookie 有效`。
- 最近验证失败：显示 `需同步`。
- 旧 ST/AT 账号继续按 `at_expires` 显示剩余时间。

每次 Cookie 协调成功时仍应更新 `at_expires`，避免旧字段残留。

## 8. Flow Cookie Jar 与并发安全

### 8.1 为什么不能使用普通字典

Flow/Google 可能同时存在同名 Cookie 的多个 domain/path 版本。普通字典会丢失重复值并改变浏览器发送顺序。

Cookie Jar 必须：

- 导入结构化 Cookie。
- 对目标 URL 只发送匹配项。
- 保留重复 `(domain, path, name)` 值。
- 处理响应 `Set-Cookie` 新增、替换和删除。
- 从 libcurl `COOKIELIST` 导出新的结构化快照。
- 保留原快照中的 SameSite 等 libcurl 不公开的属性。

### 8.2 Cookie 轮换回写

一次 RPC 完成后：

```python
rotated = flow_cookie_jar.export_storage()
```

如果快照发生变化，应更新数据库中的：

```text
google_cookies
st = frontend-<new hash>
at = flow-frontend-cookie
at_expires
last_st_refresh_at
last_st_refresh_result = success
```

### 8.3 CAS 防覆盖

存在以下竞态：

1. 请求 A 读取 Cookie 版本 1。
2. 浏览器同步写入版本 2。
3. 请求 A 完成并拿到版本 1 的轮换结果。
4. 如果直接 UPDATE，请求 A 会覆盖版本 2。

必须使用：

```sql
UPDATE tokens
SET google_cookies = ?, ...
WHERE id = ? AND google_cookies = ?
```

只有数据库当前值仍等于请求开始时的 `expected_cookies` 才允许回写。参考方法：

```python
update_token_if_cookie_matches(token_id, expected_cookies, **fields) -> bool
```

CAS 失败表示已有更新快照，当前请求不能覆盖它。

## 9. Flow 前端 RPC Transport

### 9.1 Bootstrap

每次 RPC 根据业务上下文加载页面：

```text
无项目：https://flow.google.com/projects
有项目：https://flow.google.com/project/<project_id>
媒体编辑：https://flow.google.com/project/<project_id>/edit/<media_id>
```

请求必须使用：

- Cookie Jar
- 与账号一致的代理
- 浏览器 User-Agent/impersonation
- `Referer: https://flow.google.com/`

只允许站内重定向。跳转到 `accounts.google.com` 或其他域表示登录失效。

从 HTML 提取：

```regex
bl: boq_labs-ai-sandbox-frontend_[A-Za-z0-9_.-]+
f.sid: "FdrFJe":"(-?[0-9]+)"
XSRF: "SNlM0e":"..."
```

### 9.2 `batchexecute` URL

```text
https://flow.google.com/_/AiSandboxAngularFrontend/data/batchexecute
  ?rpcids=<rpc_id>
  &source-path=<source_path>
  &bl=<bootloader_id>
  &f.sid=<frontend_session_id>
  &hl=en
  &_reqid=<random integer>
  &rt=c
```

### 9.3 请求头

```http
Accept: */*
Accept-Language: en-US,en;q=0.9
Content-Type: application/x-www-form-urlencoded;charset=UTF-8
Cookie: <按 batchexecute URL 作用域筛选后的 Cookie>
Origin: https://flow.google.com
Referer: https://flow.google.com/
sec-fetch-dest: empty
sec-fetch-mode: cors
sec-fetch-site: same-origin
x-same-domain: 1
```

### 9.4 请求体

`argument` 先序列化为紧凑 JSON 字符串，再包装成：

```python
f_req = json.dumps(
    [[[rpc_id, json.dumps(argument, separators=(",", ":")), None, "generic"]]],
    separators=(",", ":"),
)

body = urlencode({
    "f.req": f_req,
    "at": xsrf_token,
})
```

不要对数组结构做“语义化改写”；这些位置数组是前端 protobuf 的 JSON 映射，字段位置必须与真实 UI 请求一致。

### 9.5 XSRF 更新

服务端可能在响应中返回：

```text
["xsrf", "<new token>"]
```

检测到后使用新 token 原样重试一次当前 RPC。

### 9.6 响应帧解析

响应是多行 framed JSON。处理方式：

1. 逐行查找以 `[` 开头的 JSON。
2. 解析每个 frame。
3. 查找：

```text
["wrb.fr", "<rpc_id>", "<inner json>", ...]
```

4. 对第三项再次 `json.loads()` 得到业务 payload。
5. 查找 `er` 帧并转换为明确异常。
6. 对 RPC 401 输出 Cookie 已失效/账号不一致提示。
7. 识别 `PUBLIC_ERROR_*` 和 `ERROR_*` 业务错误码。

## 10. 当前 RPC 映射

以下 RPC ID 来自当前 Flow Web 前端。迁移时应通过真实浏览器抓包确认，Google 更新前端后可能变化。

| 功能 | RPC ID | 备注 |
| --- | --- | --- |
| 创建项目 | `jHPbke` | 返回项目 UUID |
| 删除项目 | `QI2zvc` | 参数包含项目 ID |
| 余额/账号等级 | `nzlxg` | Cookie 登录验证也使用它 |
| 获取媒体 | `as29s` | 图片/视频 URL |
| 删除媒体 | `cz8Z4b` | 媒体 ID 列表 |
| 上传图片 | `maseQ` | Base64、MIME、项目上下文、reCAPTCHA |
| 图片生成 | `ogiZ0b` | 文生图、参考图/图生图 |
| 图片放大 | `SPrCad` | 2K/4K enum |
| 文生视频 | `YhhmEf` | text-to-video |
| 参考图视频/Omni | `MZZa6b` | reference-to-video |
| 首尾帧视频 | `nprQif` | start/end interpolation |
| 首帧视频 | `eb1hJf` | image-to-video |
| 视频续写 | `fZytfe` | extend |
| 视频放大 | `p0UkFb` | upsample |
| 视频状态轮询 | `jwpduf` | operation ID 列表 |
| Agent session | `mrlkwd` | 获取创建 Agent session |
| Agent session detail | `GN0Bre` | session 明细 |

当前 Flow 前端未提供已验证的媒体拼接 RPC。旧“续写后服务端拼接”链路不能假装成功，应明确返回：

```text
Current Flow frontend does not expose a media concatenation RPC
```

## 11. 图片与视频调用要求

### 11.1 图片上传

流程：

1. 必须有 `project_id`。
2. 检测 JPEG/PNG MIME。
3. 为 `UPLOAD_IMAGE` 获取验证码。
4. 调用 `maseQ`，上传 Base64 图片及文件名。
5. 从响应 URL 或嵌套字段中提取媒体 UUID。

不再回退旧 `:uploadUserImage` REST 接口。

### 11.2 图片生成

当前图片模型示例：

```text
BELUGA
HARBOR_SEAL
GEM_PIX_2
```

旧 `NARWHAL` 可以在内部映射到 `BELUGA`，但不能在失败后回退到已经下线的旧上游接口。

图片生成使用 `ogiZ0b`，参考图通过已上传媒体 ID 写入参数。必须保留现有 API 对文生图、参考图和图生图的路由语义。

### 11.3 视频生成模式

| 模式 | RPC |
| --- | --- |
| `text` | `YhhmEf` |
| `references` | `MZZa6b` |
| `start_end` | `nprQif` |
| `start` | `eb1hJf` |
| `extend` | `fZytfe` |
| `upsample` | `p0UkFb` |

提交后统一标准化为 operation 列表，至少保留：

```json
{
  "operation": {"name": "<operation-id>"},
  "name": "<operation-id>",
  "mediaName": "<media-id>",
  "projectId": "<project-id>"
}
```

轮询使用 `jwpduf`。当轮询响应缺少最终视频 URL 时，可用 operation/media ID 调用 `as29s` 补取媒体信息。

## 12. reCAPTCHA、User-Agent 与代理

上传、图片生成、图片放大和视频提交仍需要验证码。

验证码请求的 `websiteURL` 必须是：

```text
https://flow.google.com/project/<project_id>
```

常用 action：

```text
UPLOAD_IMAGE
IMAGE_GENERATION
VIDEO_GENERATION
```

外部打码服务如果返回 `solution.userAgent`，提交 Flow RPC 时必须沿用该 User-Agent。账号验证、验证码和生成请求应尽量使用相同固定代理出口。

推荐在每个 Token 上保留 `proxy_url`，调用期间临时写入请求 fingerprint/context，调用结束后恢复原上下文，避免并发账号串代理。

扩展模式下用 `extension_route_key` 将验证码任务路由到对应 Chromium；扩展只处理验证码，不导出账号 Cookie。

## 13. Token 刷新生命周期

### 13.1 判断是否需要刷新

Cookie 模式：

```python
if google_cookies:
    if not auto_refresh_enabled:
        return False
    if not last_st_refresh_at:
        return True
    return now - last_st_refresh_at >= refresh_interval_minutes
```

旧 AT 模式可以继续按 `at_expires < 1 hour` 判断。

### 13.2 刷新 Cookie 模式

1. 用当前 Cookie 调用 `st_to_at()` 兼容适配。
2. 适配内部调用 `nzlxg`，同时接受 Cookie 轮换。
3. 解析返回的兼容 `expires`。
4. 使用 CAS 更新 Cookie、合成 ST、占位 AT、过期字段和刷新状态。
5. 再调用 `get_credits()` 验证。

刷新失败时不要因为一次网络/验证码异常立刻耗尽整个账号池。记录失败并请求 Host Agent 重新同步；只有明确认证失败或连续策略达到阈值时再禁用账号。

## 14. 必须删除或禁用的旧路径

迁移完成后，全仓库搜索并处理以下模式：

```text
labs.google/fx/api/auth/session
/api/auth/session
__Secure-next-auth.session-token 作为主凭据
旧 Labs OAuth 自动打开/自动登录
Labs tRPC GET/POST
旧 AI Sandbox REST uploadImage
:uploadUserImage fallback
旧 REST 视频生成和轮询
浏览器自动点击 Flow UI 作为主生成链路
```

如果为了兼容保留旧方法名，应让它们明确抛错，而不是静默调用退役接口：

```python
raise RuntimeError("The retired Labs tRPC transport is disabled")
```

## 15. 推荐实施顺序

交给其他智能体时，按以下顺序执行，避免半迁移状态：

1. 备份数据库和配置，但不要把备份提交到 Git。
2. 检查工作区已有改动，禁止覆盖用户未提交内容。
3. 增加 Token 数据库字段和迁移。
4. 实现结构化 Cookie 解析、URL 作用域筛选和校验。
5. 实现支持重复 Cookie、轮换和删除的 Cookie Jar。
6. 实现 `ProtocolLogin`，在线验证 Cookie 并识别邮箱。
7. 实现 Flow bootstrap 与 `batchexecute` transport。
8. 实现当前项目、余额、图片、视频、轮询和媒体 RPC。
9. 用 mixin 或适配层覆盖旧 FlowClient 传输方法。
10. 修改 TokenManager 为 Cookie-first，并加入 CAS 回写。
11. 增加 `/api/plugin/reconcile-browser-account`。
12. 修改 Host Agent 通过 CDP 同步 Cookie、项目和扩展 route key。
13. 禁用扩展账号自动导入，保留验证码功能。
14. 修改管理端 Cookie 账号的过期状态显示。
15. 删除或禁用旧认证、旧 REST/tRPC 路径及相应过时测试。
16. 运行全套单元测试。
17. 重启服务并执行端到端验证。

## 16. 必需测试

### 16.1 Cookie 单元测试

- 结构化数组、`{"cookies": [...]}` 和旧 Header 输入。
- host-only 与 domain Cookie。
- Path 作用域和长 Path 优先。
- Secure 和过期 Cookie。
- partitioned Cookie 跳过。
- 重复同名 Cookie 保留。
- `Set-Cookie` 新增、替换和删除。
- 缺少 Flow 会话组时拒绝。
- 缺少 Google 账号组时拒绝。

### 16.2 登录验证测试

- 有效 Flow bootstrap 能提取邮箱。
- 跳转 Google 登录时失败。
- HTTP 401/403 时失败。
- 缺少当前前端 marker 时失败。
- Host Agent 提示邮箱与真实邮箱不一致时以 Flow 页面为准。

### 16.3 RPC transport 测试

- 正确提取 `bl`、`FdrFJe`、`SNlM0e`。
- `f.req` 双层 JSON 编码正确。
- `xsrf` 响应触发一次重试。
- `wrb.fr` payload 解包。
- `er`、401、`PUBLIC_ERROR_*` 转换。
- 请求结束后能导出轮换 Cookie。

### 16.4 业务 RPC 测试

- `nzlxg` 余额。
- `maseQ` 上传。
- `ogiZ0b` 文生图和参考图。
- `SPrCad` 图片放大。
- 六种视频提交模式。
- `jwpduf` 轮询。
- `as29s` 媒体补取。

### 16.5 数据库和并发测试

- 新账号创建。
- 同邮箱账号更新而不是重复创建。
- reconcile 更新 `at_expires`。
- Cookie 账号不被旧 AT 时间显示为“已过期”。
- CAS 成功时更新。
- 浏览器已写入新快照后，旧请求 CAS 失败且不覆盖。

参考命令：

```bash
.venv/bin/python -m unittest discover -s tests
```

参考部署完成时的测试基线：

```text
Flow2API: 123 tests passed
Host Agent: 27 tests passed
```

测试数量不是验收条件；全部通过且覆盖上述行为才是。

## 17. 端到端验收

必须用真实账号完成以下验证：

1. Chromium 手动登录后进入 Flow 项目页。
2. Host Agent 同步 Cookie，API 返回 `success: true`。
3. 数据库 `google_cookies` 已写入，写后指纹一致。
4. 管理端账号显示启用，认证状态显示 `Cookie 有效`，不显示旧 AT“已过期”。
5. 余额读取成功。
6. 文生图成功并能下载结果。
7. 上传参考图成功。
8. 图生图/参考图生成成功。
9. 文生视频或 Omni 视频提交成功。
10. `jwpduf` 轮询到成功并通过 `as29s` 得到视频 URL。
11. 正常重启 Flow2API 后仍可生成。
12. 正常重启 Chromium 后登录态仍保留；如果 Google 主动使会话失效，界面应明确要求手动重新登录。

## 18. 常见故障

### 管理端显示“已过期”

原因：Cookie 模式仍读取旧 `at_expires`。

修复：

- reconcile 成功时更新 `at_expires`。
- Cookie 模式按 `last_st_refresh_result` 显示 `Cookie 有效/需同步`。
- 不把 `flow-frontend-cookie` 当作真实 Google AT。

### `Google Cookies 不完整`

检查对 `flow.google.com/projects` 作用域筛选后的 Cookie，而不是整个浏览器中是否存在同名 Cookie。常见情况是 `SID` 只存在于 `.youtube.com`。

### Flow 页面跳到 `/about` 或账号选择页

Google 登录已失效。要求用户通过 noVNC 手动登录并进入项目页，然后重新同步。不要恢复旧自动密码登录。

### RPC 401

Cookie 已失效、不完整、来自不同账号，或 Cookie Header 作用域构造错误。重新在线验证 Cookie，不要尝试旧 NextAuth session 接口。

### RPC 返回无 payload

检查：

- RPC ID 是否仍有效。
- `bl` 和 `f.sid` 是否来自当前 bootstrap。
- `SNlM0e` 是否正确。
- `f.req` 是否双层紧凑 JSON。
- `source-path` 是否与项目/媒体页面一致。

### reCAPTCHA evaluation failed

检查验证码 token、User-Agent、代理出口、websiteURL 和 action 是否一致。

### 浏览器重启后退出登录

启用 `RestoreOnStartup: 1`、`--restore-last-session`，并确保 Chromium 使用持久化 Profile 和优雅退出。

### Cookie 同步成功但生成后又失效

检查是否忽略了 RPC 响应中的 Cookie 轮换，或者旧请求覆盖了新快照。实现完整 Cookie Jar 和 CAS 回写。

## 19. 安全检查清单

- `google_cookies` 等同账号会话凭据，数据库备份和文件权限按密钥处理。
- CDP 端口只能监听 `127.0.0.1`，不得暴露公网。
- noVNC 应增加访问控制，不得裸露到公网。
- Connection Token 使用随机高熵值。
- 日志默认脱敏，禁止输出 Cookie、XSRF、验证码 token 和 Authorization。
- Host Agent 状态文件只记录短指纹和计数。
- 浏览器 Profile 使用专用系统用户和最小权限。
- 代理凭据不要写入文档、测试和提交。
- 扩展账号自动导入关闭，不新增 `cookies` 权限。

## 20. 给执行智能体的最终指令

在目标服务器上实施时：

1. 先阅读目标代码结构和当前脏工作区，不要假设文件与本参考仓库完全一致。
2. 以最小改动替换认证和上游 transport，保留已有业务 API。
3. 不实现旧认证兼容回退；发现 persisted data 或外部消费者确实需要时，再做明确的数据迁移。
4. 不要求用户提供真实 Cookie 到对话或文件；Cookie 只通过目标服务器浏览器和本机 API 流转。
5. 先写/更新测试，再实现功能。
6. 完成代码、数据库迁移、服务部署、全套测试和真实端到端验证后再结束。
7. 最终报告中列出：修改文件、数据库变化、测试结果、服务状态、端到端结果、已知限制和是否仍需人工登录。

本文档描述的是完整 Cookie-first + Flow frontend RPC 迁移，不是仅修复管理界面或伪造 Token 有效期。只有账号同步、图片、参考图、视频提交和轮询都通过，才算迁移完成。
