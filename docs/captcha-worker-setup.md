# Flow2API Captcha Worker 配置

这个扩展负责在真实 Chrome 登录态里生成 reCAPTCHA token，用来替代 Docker 内置浏览器打码，降低 `PUBLIC_ERROR_UNUSUAL_ACTIVITY` 的概率。

## 扩展目录

在 Chrome 的 `chrome://extensions/` 里开启开发者模式，然后选择“加载已解压的扩展程序”，选择这个目录：

```text
/vol1/1000/share1/AI/flow2api/extension
```

## 插件配置

加载后打开扩展的“详情”或“选项”，填写：

```text
Route Key: flow-main
Client Label: chrome-flow-main
WebSocket URL: ws://127.0.0.1:8000/captcha_ws
Flow2API API Key: 填写你本地后台配置里的 API Key
```

如果这个扩展运行在另一台 Windows 电脑的 Chrome 里，WebSocket URL 改成：

```text
ws://192.168.31.60:8000/captcha_ws
```

## 后端配置

当前后端已经切到：

```text
captcha_method = extension
```

当前 Token 已绑定：

```text
extension_route_key = flow-main
```

## 使用方式

1. 同一个 Chrome 保持登录 `https://labs.google/fx/tools/flow` 或 `https://labs.google/fx/vi/tools/flow`。
2. 保持 `Flow2API Captcha Worker` 扩展启用。
3. 保持 `Flow2API Token Updater` 扩展启用。
4. 再调用 Flow2API 生成。

## 失败时检查

如果仍然出现 `reCAPTCHA evaluation failed`：

1. 打开 `chrome://extensions/`，确认 `Flow2API Captcha Worker` 没被禁用。
2. 打开扩展的 service worker 控制台，查看是否 WebSocket connected。
3. 确认 Route Key 是 `flow-main`。
4. 确认 Flow 页面能正常打开且账号已登录。
5. 等几分钟再试，Google 风控有时会短时间拒绝同一环境。
