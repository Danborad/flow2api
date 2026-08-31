# Flow2API Fork

将 Google Flow 的图片和视频生成能力封装为 OpenAI/Gemini 兼容 API，并通过仓库自带的 Chrome 扩展同步当前浏览器账号、刷新 ST/AT 和处理 reCAPTCHA。

本仓库是 [TheSmallHanCat/flow2api](https://github.com/TheSmallHanCat/flow2api) 的个人维护分支，保留上游核心能力，并针对原生运行、浏览器插件同步、公开模型名和管理后台做了调整。

## 主要变化

- 内置 Chrome 扩展同时负责账号导入、定时同步和 reCAPTCHA，不需要另外安装 Token Updater。
- 推荐原生 Python 运行，方便直接使用真实 Chrome 登录态。
- 对外只展示简洁模型名，旧长模型 ID 仍兼容。
- 支持 OpenAI `/v1/chat/completions` 和 Gemini `generateContent` / `streamGenerateContent`。
- 管理后台显示账号积分；图片不扣点，视频按 Flow 规则统计点数。
- 移除了上游 README 中与本 fork 使用方式无关的推广内容。

## 支持能力

- 文生图、图生图、连续图片编辑
- 文生视频、图生视频、首尾帧视频、多参考图视频
- Nano Banana Pro / Nano Banana 2 的 2K、4K 输出
- Omni 1.1 Flash 的 4、6、8、10 秒路由，支持首帧和首尾帧
- 多账号 Token 管理、并发控制和负载均衡
- 浏览器账号自动导入与定时刷新
- Chrome 扩展 route key 绑定，多浏览器 Profile 对应多账号
- Web 管理后台、请求日志、余额和生成统计

## 快速开始

### 1. 原生运行

要求 Python 3.10+，推荐使用虚拟环境：

```bash
git clone https://github.com/Danborad/flow2api.git
cd flow2api

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

Windows 激活虚拟环境：

```powershell
.venv\Scripts\activate
```

服务默认监听：

```text
http://127.0.0.1:8000
```

首次登录管理后台：

```text
用户名：admin
密码：admin
```

首次登录后请立即修改密码和 API Key。

### 2. 加载浏览器扩展

1. 在 Chrome 打开 `chrome://extensions/`。
2. 开启“开发者模式”。
3. 点击“加载已解压的扩展程序”。
4. 选择仓库里的 `extension` 目录。
5. 打开扩展设置页并填写：

```text
WebSocket URL: ws://127.0.0.1:8000/captcha_ws
Flow2API API Key: 管理后台中的 API Key
```

6. 在同一 Chrome Profile 登录 `https://labs.google/fx/tools/flow`。
7. 点击“导入当前 Google 账号”。
8. 开启“定时自动导入当前 Google 账号”。

扩展会读取当前 Profile 的 Labs Session Token 和 Google 登录 Cookie，导入后台并定时更新。后台验证码方式应设置为 `extension`。

扩展版本显示在插件设置页标题和 Chrome 扩展详情页中。每次插件代码更新都会递增 `extension/manifest.json` 的版本号，并同步记录在 `extension/CHANGELOG.md`。

详细说明见 [浏览器插件配置](docs/captcha-worker-setup.md)。

### 3. 多账号

不同账号必须使用不同 Chrome Profile，不能只开同一 Profile 的多个窗口。

每个 Profile 单独加载扩展。插件会自动生成不同的内部实例标识：

```text
账号 A: 自动生成
账号 B: 自动生成
账号 C: 自动生成
```

导入后，后台 Token 会自动绑定当前浏览器 Profile，生成时验证码请求只会发给匹配的 Profile。API Key 是后台唯一的全局鉴权 Key，不需要为每个插件生成新的 Key。

## API 接入

### OpenAI 兼容

```text
Base URL: http://127.0.0.1:8000/v1
API Key: 管理后台中的 API Key
Endpoint: /chat/completions
```

Nano Banana 2 方图 2K：

```bash
curl -X POST "http://127.0.0.1:8000/v1/chat/completions" \
  -H "Authorization: Bearer $FLOW2API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Nano Banana 2",
    "messages": [
      {"role": "user", "content": "一个透明玻璃苹果，白底产品摄影"}
    ],
    "generationConfig": {
      "imageConfig": {
        "aspectRatio": "1:1",
        "imageSize": "2k"
      }
    },
    "stream": false
  }'
```

### Gemini 兼容

```bash
curl -X POST "http://127.0.0.1:8000/models/Nano%20Banana%202:generateContent?key=$FLOW2API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [
      {
        "role": "user",
        "parts": [{"text": "一个透明玻璃苹果，白底产品摄影"}]
      }
    ],
    "generationConfig": {
      "imageConfig": {
        "aspectRatio": "1:1",
        "imageSize": "2k"
      }
    }
  }'
```

支持的认证方式：

```text
Authorization: Bearer <api_key>
x-goog-api-key: <api_key>
?key=<api_key>
```

## 公开模型名

| 模型 | 类型 | 主要参数 |
| --- | --- | --- |
| `Nano Banana Pro` | 图片 | 5 种比例，默认/2K/4K |
| `Nano Banana 2` | 图片 | 5 种比例，默认/2K/4K |
| `Imagen 4` | 图片 | 16:9、9:16 |
| `Omni 1.1 Flash` | 视频 | 4/6/8/10 秒；1 张首帧、2 张首尾帧、3 张以上参考图 |
| `Veo 3.1 - Lite` | 视频 | 按图片数量自动选择 T2V/I2V/首尾帧 |
| `Veo 3.1 - Fast` | 视频 | 按图片数量自动选择 T2V/I2V/R2V |
| `Veo 3.1 - Quality` | 视频 | T2V/I2V，支持 1080p/4K 放大 |

旧名称 `Nano Banana2` 和内部长模型 ID 仍可调用，但不会出现在默认模型列表。

完整参数、路由结果和 OpenAI/Gemini 示例见 [模型路由规则](docs/model-aliases.md)。

## 视频积分

图片生成不消耗账号点数。当前管理后台按以下规则统计成功视频请求：

| 模型 | 时长 | 点数 |
| --- | --- | --- |
| `Veo 3.1 - Lite` | 默认 | 10 |
| `Omni 1.1 Flash` | 4 秒 | 7 |
| `Omni 1.1 Flash` | 6 秒 | 10 |
| `Omni 1.1 Flash` | 8 秒 | 12 |
| `Omni 1.1 Flash` | 10 秒 | 15 |

Token 列表里的余额来自上游账号 Credits；“今日视频点数/账号余额”是统计值，不会在本地重复扣款。

## 常用地址

```text
管理后台: http://127.0.0.1:8000/manage
模型测试: http://127.0.0.1:8000/test
健康检查: http://127.0.0.1:8000/health
模型列表: http://127.0.0.1:8000/v1/models
Prometheus: http://127.0.0.1:8000/metrics
```

## Docker

仓库仍保留上游 Docker 配置，但浏览器扩展需要连接真实 Chrome 登录态，本 fork 更推荐原生运行。使用 Docker 时，需要确保扩展所在设备可以访问容器的 `8000` 端口，并把 WebSocket URL 改成宿主机可访问地址。

## 排障

### Token 显示过期

- 确认扩展已重新加载且开启定时自动导入。
- 确认同一 Chrome Profile 能正常打开 Google Flow。
- 打开扩展设置查看最近自动导入状态。
- 手动点击“导入当前 Google 账号”，再刷新后台 Token 列表。

### `Failed to obtain reCAPTCHA token`

- 确认扩展 WebSocket 已连接。
- 确认账号是在当前 Chrome Profile 中导入的，不要混用其他 Profile 的账号配置。
- 保持 Google Flow 页面可正常打开。
- 并发请求会排队等待插件生成验证码，先用单请求验证。

### 外部设备调用

外部设备不能使用 `127.0.0.1`，应改成运行 Flow2API 设备的局域网 IP，并确认防火墙已开放 `8000` 端口。

## 文档

- [浏览器插件配置](docs/captcha-worker-setup.md)
- [模型路由规则](docs/model-aliases.md)
- [原作者仓库](https://github.com/TheSmallHanCat/flow2api)

## 许可证

本项目沿用上游 MIT License。请遵守 Google 服务条款，并自行承担账号、网络和 API 使用风险。
