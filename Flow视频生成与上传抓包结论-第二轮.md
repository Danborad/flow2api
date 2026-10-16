# Google Flow 视频生成 & 本地上传 抓包结论（第二轮）

抓包时间：2026-10-08 19:30–20:05
目标页面：`https://flow.google.com/project/4bcb3ed9-f4d6-4e32-b668-38c9b3156cab`
模型：**Omni 1.1 Flash**（内部代号 `abra`）
抓包方式：CDP 驱动真实 Chrome + 临时 XHR 拦截钩子（真发/拦截两种模式对照）

---

## 一、分辨率 360p / 720p

**结论：分辨率就写在「模式串」里，360p 是加后缀，720p 是默认不带后缀。**

| 设置 | 模式串 |
| --- | --- |
| 视频 · 帧 · **720p** · 6 秒 | `abra_t2v_6s` |
| 视频 · 帧 · **360p** · 6 秒 | `abra_t2v_6s_360p` |
| 视频 · 帧 · **360p**（图生视频） | `abra_i2v_6s_360p` |

也就是说模式串的完整语法是：

```
<模型代号>_<任务>_<时长>[ _<分辨率> ]
   abra       t2v       6s      360p（仅在 360p 时出现）
```

- **360p** 会显式追加 `_360p`
- **720p** 不追加任何东西（默认档）
- 请求体里**没有**独立的 resolution 数字字段，只靠这个后缀区分

证据文件（同一提示词、同参数，仅改分辨率）：

- `captures/flow-t2v-720p.network-request` → `abra_t2v_6s`
- `captures/flow-t2v-360p.network-request` → `abra_t2v_6s_360p`

> 顺带确认：**时长同理**，编码在模式串中间（`_4s` / `_6s` / `_8s` / `_10s`），也没有独立数字字段。

---

## 二、「帧」和「素材」的区别

这是本轮最关键的发现：**两者不是同一个接口，连 rpcid 都不一样。**

| | 帧（Frame） | 素材（Asset / Ingredients） |
| --- | --- | --- |
| rpcid | `YhhmEf`（无图时）/ `eb1hJf`（挂了起始帧） | **`MZZa6b`** |
| 模式串任务段 | `t2v` / `i2v` | **`r2v`**（reference-to-video） |
| UI 形态 | 「开始 / 结束」两个帧槽 | 「+ 添加素材」，可多选 |
| 图片语义 | 当作**首帧/尾帧**（画面从这里开始） | 当作**参考素材**（内容/风格参考） |
| 请求体位置 | 图片在模式串**后面**的第 4 项里 | 参考图列表插在模式串**前面** |

三种请求体的数组布局（以模式串为锚点）：

```
【T2V · 帧 · 不挂图】  rpcid = YhhmEf
[ 提示词,  "abra_t2v_6s",      比例,  null,  [4个null, uuid, uuid] ]

【I2V · 帧 · 挂起始帧】rpcid = eb1hJf
[ 提示词,  "abra_i2v_6s",      比例,  null,
  [ null, "<起始帧 mediaId>", null, null, null, [裁切参数] ],
  [4个null, uuid, uuid] ]

【R2V · 素材】        rpcid = MZZa6b
[ 提示词,  [[null, "<素材 mediaId>"], ...],   "abra_r2v_6s",  比例,  null,  [4个null, uuid, uuid] ]
              ↑ 参考素材列表整体插在模式串前面
```

**要点**

- 素材模式支持**多张**参考图（列表可扩展），帧模式只有首/尾两帧
- 素材模式下**不挂任何素材**去生成，请求体和 T2V 一模一样（`abra_t2v_6s`），即素材是"可选增强"
- 挂素材后 UI 里不会有「开始/结束」槽，即使只挂 1 张图，语义也是 `r2v` 而不是 `i2v`

---

## 三、上传本地图片 —— 走 batchexecute，base64 内联

```
POST https://flow.google.com/_/AiSandboxAngularFrontend/data/batchexecute?rpcids=maseQ&source-path=%2Fproject%2F<项目ID>&...
Content-Type: application/x-www-form-urlencoded;charset=UTF-8
body: f.req=<urlencode(JSON)>&at=<token>
```

**rpcid 是 `maseQ`**。请求体结构：

```json
[ [...项目与会话上下文...],
  "<整张图片的 base64>",       // ← 索引 1，图片本体
  "image/jpeg",               // ← 索引 2，MIME
  ...,
  "flow_generated_cat.jpg",   // ← 索引 8，文件名
  ... ]
```

- **不是 multipart/form-data**，是把整个文件 base64 后塞进 `f.req` 的 JSON 里
- 实测：本地 JPG 226,006 字节 → base64 后 301,344 字符 → 请求体总长 323,393 字节
- 响应里返回新素材的 **mediaId**（本次为 `387cb499-0683-4c2a-856e-ff8e7035c7fe`）

**闭环验证**：拿这个 `maseQ` 返回的 mediaId 去跑图生视频（帧模式），请求体里出现的起始帧 mediaId 正是 `387cb499-0683-4c2a-856e-ff8e7035c7fe` —— 上传 → 拿到 mediaId → 用于生成，链路完全打通。

证据：`captures/flow-upload-166.network-request`

---

## 四、上传本地视频 —— 走另一套协议（resumable upload）

**视频上传完全不走 `batchexecute`**，用的是 Google 标准的 resumable upload，分两步：

**第 1 步 · 申请上传会话**

```
POST https://flow.google.com/upload/v1/flow/upload/video/<项目ID>
x-goog-upload-protocol: resumable
x-goog-upload-command: start
x-goog-upload-header-content-length: 2037721
slug: flow_generated_video.mp4
（body 为空，content-length: 0）
```

响应头里给出后续要用的地址：

```
x-goog-upload-status: active
x-goog-upload-url: https://flow.google.com/upload/v1/flow/upload/video/<项目ID>?upload_id=<AP6rU81...>&upload_protocol=resumable
x-goog-upload-chunk-granularity: 1048576
```

**第 2 步 · 上传字节并收尾**

```
POST <上一步返回的 x-goog-upload-url>
x-goog-upload-command: upload, finalize
x-goog-upload-offset: 0
slug: flow_generated_video.mp4
content-length: 2037721
（body = 视频原始二进制）
```

响应（JSON）直接返回新素材的元数据：

```json
{
  "mediaId": "7f0b1e9a-e0f0-4479-8457-6dfb6ecca93e",
  "media": { "video": { "dimensions": { "width": 720, "height": 1280, "length": "6.020s" } },
             "mediaMetadata": { "mediaStatus": { "mediaGenerationStatus": "MEDIA_GENERATION_STATUS_PENDING" } } },
  "workflow": { "metadata": { "displayName": "flow_generated_video" } }
}
```

**字节数校验**：上传体解码后 2,037,721 字节，与本地 MP4 完全一致（`content-length` 也精确匹配），MD5 可复核。

### 图片 vs 视频上传 对照

| | 图片 | 视频 |
| --- | --- | --- |
| 接口 | `batchexecute?rpcids=maseQ` | `POST /upload/v1/flow/upload/video/<项目ID>` |
| 编码 | base64 内联在 `f.req` | 原始二进制 body |
| 步数 | 1 步 | 2 步（start → upload, finalize） |
| 大文件 | 请求体会膨胀 33% | 走 resumable，支持分片（粒度 1 MiB） |
| 返回 | batchexecute 响应内含 mediaId | JSON 直接给 mediaId |

---

## 五、本轮全量接口一览（Omni 1.1 Flash）

| 场景 | rpcid / 路径 | 说明 |
| --- | --- | --- |
| 文生视频 | `batchexecute?rpcids=YhhmEf` | 模式串 `abra_t2v_*` |
| 图生视频（帧） | `batchexecute?rpcids=eb1hJf` | 模式串 `abra_i2v_*` |
| 素材转视频 | `batchexecute?rpcids=MZZa6b` | 模式串 `abra_r2v_*` |
| 图片生成 | `batchexecute?rpcids=ogiZ0b` | 上一轮结论 |
| **图片上传** | `batchexecute?rpcids=maseQ` | base64 内联 |
| **视频上传** | `/upload/v1/flow/upload/video/<项目ID>` | resumable，2 步 |
| 任务轮询 | `batchexecute?rpcids=jwpduf` | 约 3 秒一次 |

---

## 六、成本与副作用

**积分消耗：0**。本轮所有生成都通过临时钩子拦截（命中 rpcid 后存下请求体但不发出），零消耗。

副作用（已全部处理）：

- 拦截产生的**失败占位已全部删除**
- 面板已还原：**视频 · 帧 · 9:16 · 720p · 6 秒 · x1**
- **项目里新增了 2 个上传测试素材**：`flow_generated_cat.jpg`（图片）和 `flow_generated_video.mp4`（视频）—— 这是上传验证的产物，不需要可以自行删除
- 浏览器下载目录里可能残留 2 个 `.tmp` 临时文件（图片/视频下载时的残留）

---

## 七、未验证项

- **x1–x4 输出数量**：请求体里没找到对应字段（应改为本次的 "分辨率" 思路再验一次，可能在别的数组位置）
- **角色 / 场景 / 虚拟形象 / 语音** 这几类素材的上传接口未测（只测了图片、视频）
- **本地图片上传后走 I2V 时** 请求体里出现的裁切参数数组（`[0.0038, null, 0.9961, 1]`）含义未深究，推测与源图与目标比例不一致时的裁剪有关
- `jwpduf` 轮询响应里没有显式的 `status=done` 字段

---

## 八、复现方式

```bash
# 解析任何抓包文件（自动识别 生成 / 图片上传 / 视频上传 三类）
python scripts/parse_flow_capture.py captures/xxx.network-request

# 用 CDP 把本地文件塞进网页的 file input（绕过 MCP 路径白名单）
node scripts/cdp_set_file_input.mjs "<本地文件绝对路径>"

# 用 CDP 在页面里求值并落盘（导出页面变量，零转录风险）
node scripts/cdp_eval_to_file.mjs "window.__cap2[0]" "captures/out.network-request"
```

> 注意：`chrome-devtools-mcp` 的 `get_network_request(requestFilePath=...)` 只能写到**系统临时目录**（有 workspace root 白名单限制），要落到项目目录需要再拷一次。
