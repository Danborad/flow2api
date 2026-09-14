# Flow2API Captcha Worker 更新记录

## 1.1.20

- 修复 Cookie 隔离与同名分片误拼问题：按 domain 独立隔离分组提取 Session Token，防止不同域名的同名 Cookie 被错误拼接导致损坏。
- 完善失效重试机制：当后端返回无效或缺少 access_token 时，自动触发静默刷新授权并重试。

## 1.1.19

- 智能失效检测与全自动续期：导入前主动检验 Session Token 状态，检测到失效自动打开背景标签页完成 Google Labs 授权续期（续期 24 小时）；彻底解决“导入的 Labs Session Token 已过期”问题。

## 1.1.18

- 导入时全自动静默完成 Google Labs 会话激活与握手，无需用户手动打开任何页面。

## 1.1.17

- 精准会话接口换取：后台自动请求 `labs.google/fx/api/auth/session` 激活并拉取会话 Token，避免重定向中断；报错提供明确指引。

## 1.1.16

- 自动静默轮询换取 Session Token：针对仅打开过 `flow.google.com` 的新环境，后台自动在授权落地前持续轮询检测写入，无需手动介入。

## 1.1.15

- 修复 Session Token 获取：按 domain 深度扫描 labs.google / flow.google.com 的认证 Cookie。
- 修复打码页面选择：智能复用已有项目页；若在首页自动跳转进入已有项目页获取验证码。

## 1.1.14

- 兼容新版 Flow 会话 Cookie 名称、作用域和分片 Cookie。
- 导入失败时只记录会话 Cookie 名称，不记录 Cookie 值。

## 1.1.13

- 将 `flow.google.com` 设为账号同步和验证码页面的主入口。
- `labs.google/fx/...` 仅保留为旧版本兼容回退。

## 1.1.12

- 验证码请求优先打开当前项目页，不再复用没有验证码运行环境的 Flow 首页。
- 记录验证码请求对应的项目 ID。

## 1.1.11

- 服务地址改为由用户输入并在保存时申请运行时权限，不再把具体服务器地址写入扩展清单。
- 保留并明确支持新版 Flow 页面 `flow.google.com`。

## 1.1.10

- 远程 Flow2API 地址改为由用户输入并在保存时申请运行时权限，不再把具体服务器地址写入扩展清单。

## 1.1.9

- 增加 `executeScript` 外层超时，避免浏览器脚本卡住后一直无回包。
- reCAPTCHA 页面未加载时 15 秒内返回明确的 `captcha_load` 错误。
- 记录空脚本结果和脚本调用超时。

## 1.1.7

- 不再向 `flow.google.com` 注入外部脚本，避免触发 Trusted Types 错误。
- 等待 Flow 页面自身加载 `grecaptcha.enterprise` 后再执行验证码。
- 同步更新旧 content script，避免旧路径再次注入外部脚本。

## 1.1.8

- 将 content script 同步到 `flow.google.com`，避免旧页面注入逻辑继续触发 Trusted Types 错误。

## 1.1.6

- 支持 Google Flow 重定向后的 `flow.google.com` 页面。
- 修复页面白名单导致的 `page_check: unexpected page`。

## 1.1.5

- reCAPTCHA 页面执行始终返回结构化结果，显示具体失败阶段。
- 日志记录实际执行的 Flow 标签页 URL、是否复用页面和脚本异常。

## 1.1.4

- 设置页增加可持久化的最近插件日志。
- 记录服务端验证码请求、执行成功/失败、账号同步和临时 Flow 页面关闭事件。
- 日志自动过滤 API Key、Cookie、ST/AT 和验证码内容。

## 1.1.3

- 设置页显示 WebSocket 实时连接状态和最近错误。
- 增加“立即重连”按钮。
- 连接鉴权失败时明确提示检查 API Key。

## 1.1.2

- 增加远程 Flow2API 域名连接支持。
- 支持通过域名连接远程 IPv4/IPv6 服务。

## 1.1.1

- 手动导入与自动导入同时进行时，显示“同步正在进行中”，不再误报新增 0/更新 0。

## 1.1.0

- 增加扩展版本号显示，设置页显示当前版本。
- Service Worker 启动日志输出版本号。
- WebSocket 注册信息携带扩展版本，后端日志可确认实际连接版本。

## 1.0.0

- 初始版本。
