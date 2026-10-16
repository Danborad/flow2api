# Flow「高清重塑」抓包结论（图片 2K / 视频 1080p）

> 抓包方式：chrome-devtools-mcp 接管真实登录态 Chrome，实测点击触发、差分对照。
> 时间：2026-10-08 20:10–20:23 ｜ 项目 `4bcb3ed9-f4d6-4e32-b668-38c9b3156cab`

---

## 0. 一句话结论

**图片 2K 和视频 1080p 走的是两条完全不同的路：**

| | 图片 → 2K | 视频 → 1080p |
| --- | --- | --- |
| 接口 | `POST rpcids=SPrCad` | 首次 `POST rpcids=p0UkFb`，之后 **纯 CDN GET** |
| 服务名 | `/FlowService.UpsampleImage` | `/VideoFxService.BatchAsyncGenerateVideoUpsampleVideo` |
| 形态 | **同步**，一次请求返回成品 | **异步任务**，需轮询 `jwpduf` |
| 交付 | 响应里**内联 base64 图片** | CDN 直链，mediaId 加 `_upsampled` 后缀 |
| 产物 | 全新 UUID 资产 | 原 id + `_upsampled` |
| 放大倍率 | **2×**（768×1376 → 1536×2752） | **1.5×**（720×1280 → 1080×1920） |
| 模型串 | 枚举 26 / 27（`GEM_PIX_2_UPSAMPLE_2K` / `_4K`） | **`veo_3_1_upsampler_1080p`** |

**「已高清重塑」这四个字是字面意思**——视频的 `_upsampled` 资产一旦生成就会留在项目里，之后再点是直接拿现成的。

---

## 1. 图片：1K 原始尺寸 vs 2K 已高清重塑

### 1K 原始尺寸 —— 没有服务端处理，就是一次 CDN 直取

```
POST /_/AiSandboxAngularFrontend/data/batchexecute?rpcids=as29s     ← /FlowService.GetMedia，仅 170 字节
GET  https://flow-content.google/image/c3259594-f335-4ddf-a39c-9ca723cb63cc
     ?Expires=…&KeyName=labs-flow-prod-cdn-key&Signature=…            ← 200，原图本体
```

下载落地：**768 × 1376，741,643 字节**。

### 2K 已高清重塑 —— `SPrCad` 同步重塑

请求体（`f.req` 解码后）：

```json
["c3259594-f335-4ddf-a39c-9ca723cb63cc",        ← [0] 源图片 mediaId
 1,
 [null, 22, null, null, null,
  "4bcb3ed9-f4d6-4e32-b668-38c9b3156cab",       ← 项目 id
  null, null, null, null,
  ["<2745 字符的签名/上下文串>", 1]]]
```

响应体（`)]}'` 前缀的 Google 格式）：

```json
["b3554089-7d03-4400-980d-090a7f1e70fa",        ← 新的 2K 资产 mediaId
 "4bcb3ed9-f4d6-4e32-b668-38c9b3156cab",
 "543a574e-9d5c-4e3c-b6ba-57086f771817",        ← 媒体记录 id
 null, null,
 [[1791461603,570764000], …, 
   [null,null,null,[[[null,null,"c3259594-…"]]],1],   ← 回指源图片
   …, 2904433],                                       ← 2K 文件字节数
 [[…, "A ginger cat sitting on the windowsill.", 36, …]],   ← 原始提示词
 "/9j/4AAQSkZJRgAB…"                            ← 2K 图本体（base64 JPEG，内联）
]
```

- 下载落地：**1536 × 2752，2,904,433 字节** —— 与响应里的 `2904433` 完全对上
- **正好 2 倍边长（4 倍像素）**
- 响应内嵌的 C2PA 清单里明确写着：
  - `c2pa.created` — "Created by Google Generative AI."
  - `c2pa.edited` — **"Applied imperceptible SynthID watermark."** ← 重塑这一步会加不可见水印

### 4K

菜单里 4K 带一个「升级」按钮（未点击，避免触发付费）。前端枚举里已经能看到对应型号：
`GEM_PIX_2_UPSAMPLE_2K = 26`、`GEM_PIX_2_UPSAMPLE_4K = 27` —— 说明 **2K/4K 是同一个 `SPrCad` 接口、同一个上下文结构，只是档位参数不同**。

---

## 2. 视频：720p 原始尺寸 vs 1080p 已高清重塑

### 720p 原始尺寸 —— 纯 CDN 直取

```
GET https://flow-content.google/video/c2be96a9-b93c-439b-9eb8-5106ef3b6f6b?Expires=…&Signature=…
```

下载落地：**720 × 1280，3,571,051 字节**。（除埋点 `WuwhI` 外**没有任何 RPC**）

### 1080p 已高清重塑 —— 第一次会真的跑一个模型

**关键实验**：新生成一支视频「Neon city street after rain」→ 生成过程只有 `YhhmEf` + 轮询，**没有**任何 upscale 请求 →
紧接着点「1080p 已高清重塑」，立刻触发：

```
POST …/batchexecute?rpcids=p0UkFb      ← /VideoFxService.BatchAsyncGenerateVideoUpsampleVideo
POST …/batchexecute?rpcids=jwpduf      ← 轮询进度（和生成用同一个）
POST …/batchexecute?rpcids=as29s       ← 完成后取新资产元数据
GET  https://flow-content.google/video/802dc4ed-…_upsampled?Expires=…&Signature=…
```

`p0UkFb` 请求体（结构精简）：

```json
[[[null, "802dc4ed-8f1e-4149-b535-c2f52729e4a3"],   ← 源视频 mediaId
   null, 1, null,
   [null, "41924249-10f5-4568-9abe-98d3bcd8809a", null, null, "F5612E89-…"],
   null, 2,
   …(28 个 null)…,
   "veo_3_1_upsampler_1080p"],                       ← ★ 放大模型在这里
 [null, 22, null, null, null, "4bcb3ed9-…", …, ["<2766 字符签名串>", 1]],
 ["802FE9CF-809A-4AD2-AB82-17313C476B03"]]
```

`p0UkFb` 响应：新 mediaId = **`802dc4ed-…_upsampled`**，其 modelName 字段同样是 `veo_3_1_upsampler_1080p`。

下载落地：**1080 × 1920，6,317,102 字节** → **1.5 倍边长**。

### 第二次点 1080p 就只是下载了

对已经重塑过的视频（Golden retriever）再点 1080p：

```
GET https://flow-content.google/video/c2be96a9-…_upsampled?Expires=…&Signature=…    ← 200，直接就是成品
POST …batchexecute?rpcids=WuwhI                                                     ← 仅埋点
```

**没有任何 upscale RPC** —— 这就是为什么菜单上写「已高清重塑」。

---

## 3. rpcid → 服务名（从 3.4MB 前端 bundle 里挖出的权威映射）

| rpcid | 服务 | 用途 |
| --- | --- | --- |
| `SPrCad` | `/FlowService.UpsampleImage` | **图片高清重塑（2K/4K）** |
| `p0UkFb` | `/VideoFxService.BatchAsyncGenerateVideoUpsampleVideo` | **视频高清重塑（1080p/4K）** |
| `as29s` | `/FlowService.GetMedia` | 读媒体元数据 / 换带签名的直链 |
| `YhhmEf` | `/VideoFxService.BatchAsyncGenerateVideoText` | 文生视频 T2V |
| `eb1hJf` | `/VideoFxService.BatchAsyncGenerateVideoStartImage` | 图生视频 I2V |
| `MZZa6b` | `/VideoFxService.BatchAsyncGenerateVideoReferenceImages` | 素材生视频 R2V |
| `jwpduf` | （轮询） | 异步任务进度 |
| `WuwhI` | `/FlowService.BatchLogFrontendEvents` | **纯前端埋点，跟下载无关**（上一轮误当作下载接口） |

---

## 4. 前端模型枚举（图片侧，`switch` 表）

```js
IMAGEN_3_5_FAST = 20      IMAGEN_3_5 = 21         GEM_PIX = 23
R2I = 24                  GEM_PIX_2 = 25
GEM_PIX_2_UPSAMPLE_2K = 26          ← 2K 重塑
GEM_PIX_2_UPSAMPLE_4K = 27          ← 4K 重塑
GEM_PIX_2_BOTTLE = 28     NARWHAL = 29            GEM_PIX_2_GEOGENIE = 30
IMAGEN_3 = 11             IMAGEN_3_FAST = 12      IMAGEN_3_1 = 19
IMAGEN_2 = 8              IMAGEN_2_PORTRAIT = 9   IMAGEN_2_LANDSCAPE = 10
```

> **⚠️ 修正上一轮的结论**：`NARWHAL` **存在，是枚举 29**。上一轮说「线上没有 NARWHAL」不准确——它在模型枚举表里已定义，只是当前「图片直出」菜单没有挂它。所以它更可能属于别的入口或尚未放量的档位。

视频任务类型枚举：`TEXT / REFERENCES / EDIT_VIDEO / EXTEND_VIDEO / UPSAMPLE_VIDEO / EXPERIMENTAL`

客户端判定「已高清重塑」的逻辑（从 bundle 反推）：
`media.getName().includes("_upsampled") || 某个 upscale 状态字段 !== 0`，命中的资产会被标记 `isHidden`。

---

## 5. 给你 model_resolver.py 的补充

```python
# 图片放大
"image-upscale-2k": "GEM_PIX_2_UPSAMPLE_2K",   # 同步，SPrCad
"image-upscale-4k": "GEM_PIX_2_UPSAMPLE_4K",

# 视频放大
"video-upscale-1080p": "veo_3_1_upsampler_1080p",   # 异步任务，p0UkFb

# 判断某资产是否已放大
def is_upsampled(media_id: str) -> bool:
    return media_id.endswith("_upsampled")
```

**取 1080p 视频的两条分支**：
1. `media_id` 已以 `_upsampled` 结尾 → 直接 `GET flow-content.google/video/<id>`
2. 否则 → 先调 `p0UkFb`（body 里放 `veo_3_1_upsampler_1080p`），轮询 `jwpduf`，完成后用 `as29s` 拿新直链

---

## 6. 本次副作用（已尽量控制）

- **实际生成 1 支新视频**「Neon city street after rain」（6s / 720p → 已重塑到 1080p），留在项目里
- 图片 2K 重塑产生了 1 个新资产（在 Ginger cat 那张上）
- Downloads 新增文件：
  - `Neon_city_street_after_rain_20261008202215.mp4`（1080×1920）
  - `Golden_retriever_running_on_beach_20261008201708.mp4`（720×1280，第二次点 720p 的产物）
  - `Ginger_cat_sitting_on_windowsill_20261008201615.jpg`（768×1376，1K）
- 页面里注入的抓包钩子**已移除并用原生实现校验恢复**
- 浏览器标签与面板状态未改动

---

> **后续补充（20:46）**：已实测 **Nano Banana Pro / 2 Lite / 2.1 三个模型**各自的 2K 重塑，结论是**完全一样**（同一 `SPrCad`、同一请求结构、同为 2×，请求体不含源模型字段）。详见 `Flow图片放大-三模型对比.md`。

---

## 7. 还没验证的

1. **4K 具体走什么** —— 需要点「升级」（可能触发付费/订阅），没有冒险点。前端枚举显示图片侧是 `GEM_PIX_2_UPSAMPLE_4K`；视频侧没在 bundle 里搜到 4K upsampler 型号串（只有请求里冒出来的 `veo_3_1_upsampler_1080p`，推测 4K 是 `veo_3_1_upsampler_4k` 之类，未证实）。
2. **高清重塑是否消耗点数** —— 页面上没找到可读的余额浮层，未做前后对比。
3. **270p GIF 动画** —— 同一下载菜单里的第三项，本次没点。
4. **图片 2K 是否也会落一个 `_upsampled` 资产** —— 响应里返回的是全新 UUID（无后缀），与视频侧形态不同，未能确认是否另有隐藏资产。

---

## 附：证据文件

| 文件 | 内容 |
| --- | --- |
| `captures/flow-img-2k-SPrCad.network-request` | 图片 2K 重塑请求体 |
| `captures/flow-video-upscale-p0UkFb.network-request` | 视频 1080p 重塑任务请求体 |
| `captures/flow-t2v-neon.network-request` | 对照：新视频生成（`abra_t2v_6s`） |
| `captures/flow-t2v-720p.network-request` | 对照：720p 生成 |
| `scripts/parse_flow_upscale.py` | 一键解析上面这些抓包文件 |

```bash
python scripts/parse_flow_upscale.py --auto captures/
```
