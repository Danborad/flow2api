# 浏览器插件配置

仓库自带的 Chrome 扩展位于 `extension/`，同时负责：

- 使用当前 Chrome Profile 的真实 Google Flow 登录态生成 reCAPTCHA token。
- 读取并导入 Labs Session Token 和 Google 登录 Cookie。
- 定时把账号信息同步到 Flow2API 后台。
- 自动为当前 Chrome Profile 生成内部实例标识，并把后台 Token 绑定到对应浏览器。

不需要另外安装 Flow2API Token Updater。

## 加载扩展

1. 打开 `chrome://extensions/`。
2. 开启“开发者模式”。
3. 点击“加载已解压的扩展程序”。
4. 选择当前仓库的 `extension` 目录。

扩展目录应包含 `manifest.json`、`background.js`、`content.js`、`options.html`。

当前扩展版本会显示在插件设置页标题中，例如 `v1.1.9`。Chrome 扩展管理页的“详情”中也会显示同一版本；后台 WebSocket 日志会记录连接插件的版本号。设置页还会显示 WebSocket 当前连接状态、最近错误和插件事件日志。

## 插件设置

在扩展详情中打开“扩展程序选项”，填写：

```text
WebSocket URL: ws://127.0.0.1:8000/captcha_ws
Flow2API API Key: 管理后台中的 API Key
后台自动刷新间隔: 120 分钟
插件自动导入间隔: 30 分钟
```

勾选“定时自动导入当前 Google 账号”，然后点击“保存”。

如果浏览器扩展和 Flow2API 不在同一台设备，WebSocket URL 要改成服务端局域网地址，例如：

```text
ws://192.168.1.20:8000/captcha_ws
```

也可以填写已配置 DNS 的远程域名，例如：

```text
ws://ai2.960916.xyz:8000/captcha_ws
```

域名解析到 IPv4 或 IPv6 都可以，浏览器会按 DNS 解析结果连接。远程服务必须监听对应地址，并允许外部访问 `8000` 端口；如果使用 HTTPS 反向代理，应改用 `wss://ai2.960916.xyz/captcha_ws`。

服务端防火墙需要允许浏览器设备访问 `8000` 端口。

## 导入账号

1. 在当前 Chrome Profile 登录 Google 账号。
2. 打开 `https://labs.google/fx/tools/flow` 并确认页面可用。
3. 回到扩展设置页点击“导入当前 Google 账号”。
4. 管理后台刷新 Token 列表，确认邮箱、余额和过期时间正常。

插件导入前会打开一个隐藏的 Flow 页面刷新 Labs Session Cookie。导入成功后，后台会更新现有邮箱对应的 Token，或为新邮箱创建 Token。

扩展设置页不会保存后台管理员密码；账号导入接口使用 Flow2API API Key 鉴权。

## 自动刷新机制

自动刷新分成两层：

1. 插件定时读取当前 Chrome Profile 的最新 Labs ST 和 Google Cookie，并导入后台。
2. 后台使用保存的 ST/Google Cookie 更新 AT、余额和过期时间。

浏览器必须保持运行。关闭 Chrome、禁用扩展或退出 Google Flow 后，插件无法继续同步。

## 多账号

不同 Google 账号必须使用不同 Chrome Profile。每个 Profile：

1. 单独登录一个 Google Flow 账号。
2. 单独加载本扩展。
3. 点击一次“导入当前 Google 账号”。

示例：

```text
Profile A: 插件自动生成实例标识
Profile B: 插件自动生成实例标识
Profile C: 插件自动生成实例标识
```

同一 Chrome Profile 的多个窗口共享 Cookie，不能当作多个独立账号。

## reCAPTCHA

生成时，后端通过 WebSocket 把 reCAPTCHA 请求发送给导入该账号的浏览器实例。扩展优先复用已打开的 Google Flow 标签页；没有可用页面时会创建临时隐藏页。

验证码请求在插件中按队列处理。大量并发请求可能需要等待，建议先用单请求确认链路正常。

## 常见问题

### WebSocket 未连接

- 确认 Flow2API 服务正在运行。
- 确认 WebSocket URL 和 API Key 正确。
- 打开扩展的 Service Worker 控制台查看连接日志。
- 修改配置后点击保存，扩展会自动重连。

### 导入提示 Cookie 不完整

- 确认当前 Profile 已登录 `accounts.google.com`。
- 确认同一 Profile 可以正常进入 Google Flow。
- 重新加载扩展后再导入。
- 插件兼容传统 `SID/SAPISID` 和新版 `__Secure-*PSID/__Secure-*PAPISID` Cookie。

### 后台 Token 又过期

- 查看扩展设置页的最近自动导入状态。
- 手动刷新 Google Flow 页面后重新导入。
- 确认 Chrome 没有被系统休眠或关闭后台运行。
- 确认后台 Token 处于启用状态，并在当前 Profile 重新点击一次“导入当前 Google 账号”。

### 图片或视频提示 `Failed to obtain reCAPTCHA token`

- 确认账号是在当前 Chrome Profile 中导入的，不要手动复制其他 Profile 的配置。
- 确认 Flow 页面没有跳回登录页。
- 暂时降低并发，先发送一个请求。
- 在扩展 Service Worker 控制台查看 `Extension script failed` 或 timeout 信息。

## 安全

- 不要把真实 API Key、ST、AT 或 Google Cookie 写进 Git 仓库。
- 只在自己信任的设备加载扩展。
- Flow2API 不应直接暴露到公网；如需远程访问，请使用反向代理、访问控制和 HTTPS/WSS。
