# Flow2API 短模型名调用规范

这份文档给调用 Flow2API 的应用使用。调用方不需要记 `gemini-3.1-flash-image-landscape-4k`、`veo_3_1_i2v_s_fast_fl`、`veo_3_1_r2v_fast` 这种内部长模型 ID，只需要传 Flow 风格展示名。视频模型会根据输入图片数量自动切换到文生视频、图生视频、首尾帧或多图视频链路。

旧的长模型 ID 和上一版短名仍然可用，只是不再出现在默认模型列表里。

## 通用参数

### 图片画幅

放在 `generationConfig.imageConfig.aspectRatio`，也兼容 `generationConfig.aspectRatio`。

| 画幅 | 参数值 | 说明 |
| --- | --- | --- |
| 横屏 | `16:9` 或 `landscape` | 默认值 |
| 竖屏 | `9:16` 或 `portrait` | 竖版图 |
| 方图 | `1:1` 或 `square` | 方图 |
| 横屏 4:3 | `4:3` 或 `four-three` | 4:3 |
| 竖屏 3:4 | `3:4` 或 `three-four` | 3:4 |

### 图片分辨率

放在 `generationConfig.imageConfig.imageSize`，也兼容 `generationConfig.imageSize`。

| 分辨率 | 参数值 | 说明 |
| --- | --- | --- |
| 默认 | `1k` 或不传 | 不走放大模型 |
| 2K | `2k` | 仅 `Nano Banana Pro`、`Nano Banana 2` 支持 |
| 4K | `4k` | 仅 `Nano Banana Pro`、`Nano Banana 2` 支持 |

### 视频画幅

放在 `generationConfig.aspectRatio` 或 `generationConfig.imageConfig.aspectRatio`。

| 画幅 | 参数值 | 说明 |
| --- | --- | --- |
| 横屏 | `16:9` 或 `landscape` | 默认值 |
| 竖屏 | `9:16` 或 `portrait` | 竖版视频 |

### 视频时长

只有 `Omni 1.1 Flash` 读取 `generationConfig.durationSeconds`，也兼容 `duration`、`duration_seconds`。`Veo 3.1 - Lite`、`Veo 3.1 - Fast`、`Veo 3.1 - Quality` 会忽略时长参数，按 Flow 网页默认档走。

| 时长 | 参数值 | 说明 |
| --- | --- | --- |
| 默认 | 不传 | Omni 1.1 Flash 默认时长 |
| 4 秒 | `4`、`4s` | 仅 Omni 1.1 Flash |
| 6 秒 | `6`、`6s` | 仅 Omni 1.1 Flash |
| 8 秒 | `8`、`8s` | 仅 Omni 1.1 Flash |
| 10 秒 | `10`、`10s` | 仅 Omni 1.1 Flash |

### 视频输出分辨率

放在 `generationConfig.imageSize`，也兼容 `generationConfig.imageConfig.imageSize`。公开模型名中只有 `Veo 3.1 - Quality` 会读取此参数；`Omni 1.1 Flash`、`Veo 3.1 - Lite`、`Veo 3.1 - Fast` 会忽略。

| 输出 | 参数值 | 说明 |
| --- | --- | --- |
| 默认 | 不传 | 不走视频放大 |
| 1080P | `1080p` | 先生成再放大 |
| 4K | `4k` | 先生成再放大，耗时更长 |

## 图片模型名

| 模型名 | 对应产品 | 支持画幅 | 支持分辨率 |
| --- | --- | --- | --- |
| `Nano Banana Pro` | Nano Banana Pro | `16:9`、`9:16`、`1:1`、`4:3`、`3:4` | 默认、`2k`、`4k` |
| `Nano Banana 2` | Nano Banana 2 | `16:9`、`9:16`、`1:1`、`4:3`、`3:4` | 默认、`2k`、`4k` |
| `Imagen 4` | Imagen 4 | `16:9`、`9:16` | 默认 |

兼容别名：`Nano Banana 2`、`nano-banana-pro`、`nanobanana-pro`、`nano-banana-2`、`nano-banana2`、`nanobanana2`、`imagen`。

## 视频模型名

| 模型名 | 0 张图 | 1 张图 | 2 张图 | 3 张及以上 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `Omni 1.1 Flash` | `abra_t2v_*s` | `abra_i2v_*s` | `abra_i2v_*s` | `abra_r2v_*s` | 1 张首帧，2 张首尾帧，3 张以上多参考图；支持 4/6/8/10 秒 |
| `Veo 3.1 - Lite` | `t2v_lite` | `i2v_lite` | `interpolation_lite` | `interpolation_lite` | Flow 里的 Lite 档；忽略时长参数 |
| `Veo 3.1 - Fast` | `t2v_fast` | `i2v_fast` | `i2v_fast` | `r2v_fast` | Flow 里的 Fast 档；忽略时长参数 |
| `Veo 3.1 - Quality` | `t2v_quality` | `i2v_quality` | `i2v_quality` | `i2v_quality` | Flow 里的 Quality 档；忽略时长参数 |

### 自动路由规则

- 0 张图：走文生视频 `T2V`
- 1 张图：走图生视频 `I2V`
- 2 张图：优先走首尾帧或双帧 `I2V`
- 3 张及以上：如果模型支持，走多参考图 `R2V`

对于 `Omni 1.1 Flash`，1 张图使用首帧接口，2 张图使用首尾帧接口，3 张及以上使用多参考图接口。视频输出分辨率当前使用 Flow 默认档；`360p/720p` 选择不改变公开 API 的模型路由。

这意味着画布/工作流应用不需要显式切换到 `veo-i2v-*` 或 `veo-r2v-*` 这类内部模型，只要把图片按 OpenAI/Gemini 标准方式传给上述公开模型名即可。

兼容别名：`veo`、`veo-fast`、`veo-lite`、`veo-i2v`、`veo-i2v-fast`、`veo-r2v` 等上一版短名仍然可调用，但默认模型列表不展示。

## OpenAI 兼容接口示例

### Nano Banana Pro 竖屏 4K 图

```bash
curl -X POST "http://localhost:8000/v1/chat/completions" \
  -H "Authorization: Bearer $FLOW2API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Nano Banana Pro",
    "messages": [
      {"role": "user", "content": "一只白猫坐在雨后的霓虹街头，电影感"}
    ],
    "generationConfig": {
      "imageConfig": {
        "aspectRatio": "9:16",
        "imageSize": "4k"
      }
    },
    "stream": true
  }'
```

### Nano Banana 2 方图 2K

```bash
curl -X POST "http://localhost:8000/v1/chat/completions" \
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
    "stream": true
  }'
```

### Veo 竖屏 1080P 文生视频

```bash
curl -X POST "http://localhost:8000/v1/chat/completions" \
  -H "Authorization: Bearer $FLOW2API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Veo 3.1 - Quality",
    "messages": [
      {"role": "user", "content": "一只小猫穿过晨雾中的花园，镜头缓慢推进"}
    ],
    "generationConfig": {
      "aspectRatio": "9:16",
      "imageSize": "1080p"
    },
    "stream": true
  }'
```

### Omni 1.1 Flash 横屏 6 秒文生视频

```bash
curl -X POST "http://localhost:8000/v1/chat/completions" \
  -H "Authorization: Bearer $FLOW2API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Omni 1.1 Flash",
    "messages": [
      {"role": "user", "content": "一辆未来感跑车穿过夜晚城市，速度感强"}
    ],
    "generationConfig": {
      "aspectRatio": "16:9",
      "durationSeconds": 6
    },
    "stream": true
  }'
```

### Veo Fast 竖屏文生视频

```bash
curl -X POST "http://localhost:8000/v1/chat/completions" \
  -H "Authorization: Bearer $FLOW2API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Veo 3.1 - Fast",
    "messages": [
      {"role": "user", "content": "雨夜赛博朋克城市街道，镜头向前推进"}
    ],
    "generationConfig": {
      "aspectRatio": "9:16"
    },
    "stream": true
  }'
```

### Veo Fast 单图生成视频

```bash
curl -X POST "http://localhost:8000/v1/chat/completions" \
  -H "Authorization: Bearer $FLOW2API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Veo 3.1 - Fast",
    "messages": [
      {
        "role": "user",
        "content": [
          {"type": "text", "text": "让画面中的人物自然转身并向镜头走来"},
          {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,<首帧base64>"}}
        ]
      }
    ],
    "generationConfig": {
      "aspectRatio": "9:16"
    },
    "stream": true
  }'
```

### Veo Fast 多图生成视频

```bash
curl -X POST "http://localhost:8000/v1/chat/completions" \
  -H "Authorization: Bearer $FLOW2API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Veo 3.1 - Fast",
    "messages": [
      {
        "role": "user",
        "content": [
          {"type": "text", "text": "参考三张图的人物和场景，生成一段镜头平滑推进的视频"},
          {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,<参考图1>"}},
          {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,<参考图2>"}},
          {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,<参考图3>"}}
        ]
      }
    ],
    "generationConfig": {
      "aspectRatio": "16:9"
    },
    "stream": true
  }'
```

## Gemini 官方格式示例

### Nano Banana 2 生成图片

```bash
curl -X POST "http://localhost:8000/models/Nano%20Banana2:generateContent" \
  -H "x-goog-api-key: $FLOW2API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [
      {
        "role": "user",
        "parts": [{"text": "一颗放在木桌上的红苹果，棚拍光线，极简背景"}]
      }
    ],
    "generationConfig": {
      "responseModalities": ["IMAGE"],
      "imageConfig": {
        "aspectRatio": "1:1",
        "imageSize": "2k"
      }
    }
  }'
```

## 解析示例

| 请求模型 | 参数 | 内部实际模型 |
| --- | --- | --- |
| `Nano Banana Pro` | `aspectRatio=3:4`、`imageSize=4k` | `gemini-3.0-pro-image-three-four-4k` |
| `Nano Banana 2` | `aspectRatio=1:1`、`imageSize=2k` | `gemini-3.1-flash-image-square-2k` |
| `Omni 1.1 Flash` | `aspectRatio=16:9`、`durationSeconds=6`、0 张图 | `omni_6s` (`abra_t2v_6s`) |
| `Omni 1.1 Flash` | `aspectRatio=16:9`、`durationSeconds=6`、1 张图 | `omni_6s` (`abra_i2v_6s`, 首帧) |
| `Omni 1.1 Flash` | `aspectRatio=16:9`、`durationSeconds=6`、2 张图 | `omni_6s` (`abra_i2v_6s`, 首尾帧) |
| `Omni 1.1 Flash` | `aspectRatio=16:9`、`durationSeconds=6`、3 张图 | `omni_6s` (`abra_r2v_6s`) |
| `Veo 3.1 - Quality` | `aspectRatio=9:16`、`imageSize=1080p`、0 张图 | `veo_3_1_t2v_portrait_1080p` |
| `Veo 3.1 - Fast` | `aspectRatio=16:9`、1 张图 | `veo_3_1_i2v_s_fast_fl` |
| `Veo 3.1 - Fast` | `aspectRatio=16:9`、3 张图 | `veo_3_1_r2v_fast` |
| `Veo 3.1 - Lite` | `aspectRatio=9:16`、1 张图 | `veo_3_1_i2v_lite_portrait` |
| `Veo 3.1 - Lite` | `aspectRatio=9:16`、2 张图 | `veo_3_1_interpolation_lite_portrait` |

## 视频 URL 返回说明

新版 Flow 上游在视频任务 `SUCCESSFUL` 后，不一定会直接在状态接口里返回 `fifeUrl`。Flow2API 会自动再调用：

```text
labs.google/fx/api/trpc/media.getMediaUrlRedirect?name=<media_id>
```

读取 302 `Location`，再把真实 CDN 视频地址返回给调用方。

## 模型列表接口

默认模型列表现在只返回短模型名，适合外部应用直接展示：

```bash
curl "http://localhost:8000/v1/models" \
  -H "Authorization: Bearer $FLOW2API_KEY"
```

Gemini 格式模型列表同样只返回短模型名：

```bash
curl "http://localhost:8000/models" \
  -H "x-goog-api-key: $FLOW2API_KEY"
```

如果需要排查内部长模型 ID，可以用调试接口：

```bash
curl "http://localhost:8000/v1/models/internal" \
  -H "Authorization: Bearer $FLOW2API_KEY"
```
