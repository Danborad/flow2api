# Flow2API Captcha Worker 更新记录

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

- 增加远程 Flow2API 域名 `ai2.960916.xyz` 的连接权限。
- 支持通过域名连接远程 IPv4/IPv6 服务。

## 1.1.1

- 手动导入与自动导入同时进行时，显示“同步正在进行中”，不再误报新增 0/更新 0。

## 1.1.0

- 增加扩展版本号显示，设置页显示当前版本。
- Service Worker 启动日志输出版本号。
- WebSocket 注册信息携带扩展版本，后端日志可确认实际连接版本。

## 1.0.0

- 初始版本。
