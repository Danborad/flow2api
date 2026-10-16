# Google Flow 视频生成接口抓包结论（Omni 1.1 Flash）

抓包时间：2026-10-08　目标项目：`Flow2API`（`4bcb3ed9-f4d6-4e32-b668-38c9b3156cab`）
抓包方式：chrome-devtools-mcp 接管真实登录态 Chrome，读 `batchexecute` 的 `f.req`

---

## 0. 一句话结论

| 问题 | 答案 |
| --- | --- |
| 接口是 `batchexecute` 还是 `batchAsyncGenerateVideo`？ | **`batchexecute`**，不是 `batchAsyncGenerateVideo` |
| 文生视频（T2V）的 rpcid | **`YhhmEf`** |
| 图生视频（I2V）的 rpcid | **`eb1hJf`**（和 T2V 是两个不同的 RPC） |
| 模型代号叫什么 | **`abra`**（Omni 系列代号）。完整"模式串"= `模型_任务_时长`，如 **`abra_t2v_6s`** / **`abra_i2v_6s`** / **`abra_t2v_4s`** |
| 时长（4/6/8/10 秒）落在哪个字段 | **只写在模式串后缀**：`_4s` / `_6s` / `_8s` / `_10s`。请求体里没有独立的数字时长字段 |
| 比例（16:9 / 9:16）落在哪个字段 | 模式串**后面紧邻的一个整数**：`9:16 → 1`，`16:9 → 2` |
| I2V 怎么引用图片 | **项目内媒体 UUID（mediaId）**，路径 `head[4][1]`。不传 URL、不传 base64 |

---

## 1. 接口地址

两个场景的外壳完全一样，只有 `rpcids` 不同：

```
POST https://flow.google.com/_/AiSandboxAngularFrontend/data/batchexecute
     ?rpcids=<RPCID>
     &source-path=%2Fproject%2F4bcb3ed9-f4d6-4e32-b668-38c9b3156cab
     &bl=boq_labs-ai-sandbox-frontend_20261006.11_p0
     &f.sid=-2961122983081282129
     &hl=zh-CN
     &_reqid=<递增整数>
     &rt=c

Content-Type : application/x-www-form-urlencoded;charset=UTF-8
X-Same-Domain: 1
Origin       : https://flow.google.com
Referer      : https://flow.google.com/
Cookie       : 登录态（SID/HSID/SSID…）
body         : f.req=<urlencode 的 JSON>&at=<服务端下发的时间戳令牌>
```

**rpcid 对照表**（本次抓到的全部相关 RPC）：

| 用途 | rpcid | 备注 |
| --- | --- | --- |
| 文生视频 T2V | `YhhmEf` | 点"开始生成"时发出 |
| 图生视频 I2V | `eb1hJf` | 挂了起始帧后发出 |
| 图片生成 | `ogiZ0b` | 上一轮图片抓包已确认 |
| 任务状态轮询 | `jwpduf` | 生成后约每 3 秒一次 |
| 项目列表 | `UpteDb` | `["projects/*",21,…]` |

---

## 2. 文生视频（T2V）请求体

`f.req` 解码后是**两层嵌套 JSON 字符串**。外层第 3 段才是真正 payload：

```json
[
  [
    [
      [null, null, [[["<提示词>"]]]],          // ← 提示词
      "abra_t2v_6s",                            // ← 【模型/模式串】
      1,                                        // ← 【宽高比】9:16=1, 16:9=2
      null,
      [null, null, null, null,
       "<UUID-A>", "<UUID-B>"]                  // ← 客户端生成的两个 UUID
    ]
  ],
  [null, 22, null, null, null,
   "4bcb3ed9-f4d6-4e32-b668-38c9b3156cab",      // ← 项目 id
   null, null, null, null,
   ["<约 2.7KB 的不透明令牌>", 1]],              // ← 会话/完整性令牌（base64，非明文）
  ["<批次UUID>", 2]                             // ← 批次 id，服务端响应会原样回带
]
```

实测样本：

```json
[[[[null,null,[[["A golden retriever running along a beach at sunset, cinematic slow motion"]]]],
   "abra_t2v_6s", 1, null,
   [null,null,null,null,"65EEFDBA-9B96-483B-A949-FB818B529CEA","B40EB6CB-7206-4176-9FAD-240855C6984F"]]],
 [[null,22,null,null,null,"4bcb3ed9-f4d6-4e32-b668-38c9b3156cab",null,null,null,null,["0cAFcWeA…(2788B)",1]]],
 ["C7555E3F-AA9A-404F-81E6-884D06B0CA01",2]]
```

---

## 3. 图生视频（I2V）请求体

结构与 T2V **只差一个槽位**——挂上参考图后，`head` 里多出一个"参考图对象"，原本的两个客户端 UUID 往后顺移一位：

```json
[
  [
    [
      [null, null, [[["<提示词>"]]]],
      "abra_i2v_6s",                            // ← 【模型/模式串】
      1,                                        // ← 【宽高比】
      null,
      [ null,
        "a8ce1200-26be-4bb8-be26-6b2628b19340", // ← 【参考图 mediaId】就这里
        null, null, null,
        [null, null, 1, 1] ],
      [null, null, null, null,
       "<UUID-A>", "<UUID-B>"]
    ]
  ],
  [null, 22, null, null, null, "<项目id>", null, null, null, null, ["<令牌>", 1]],
  ["<批次UUID>", 2]
]
```

### 图片是怎么被引用的（重点）

**是 mediaId（UUID），不是 URL，也不是 base64。**

- 位置：`f.req` 内层 payload 的 `head[4][1]`
- 取值：`"a8ce1200-26be-4bb8-be26-6b2628b19340"`（本次挂的是项目里那张"Orange cat sitting on windowsill"）
- 结构：`[null, "<mediaId>", null, null, null, [null, null, 1, 1]]`
  - 第 0 位：占位 `null`（起始帧）
  - 第 1 位：**mediaId** ← 唯一标识图片的字段
  - 末位 `[null,null,1,1]`：疑似帧角色/顺序标记（起始帧恒为 `1,1`）
- **整个请求体里搜不到任何图片 URL**，也没有 base64 图像数据 → 图片是"项目内已有的媒体资源"，服务端按 mediaId 取
- 因为图片本来就是项目里的生成产物，**I2V 没有再触发额外的上传 RPC**；如果换成用户本地新图，预期会先有一个上传接口把图变成 mediaId（本次未验证）

---

## 4. 参数 → 字段映射（含对照实验证据）

我把同一个模型跑了 4 组，逐次只改一个参数，用抓到的请求体做差分：

| # | 模型 | 时长 | 比例 | 抓到的模式串 | 比例字段值 | 方式 |
| --- | --- | --- | --- | --- | --- | --- |
| A | Omni 1.1 Flash | 6 秒 | 9:16 | `abra_t2v_6s` | `1` | 真实生成 |
| B | Omni 1.1 Flash | 6 秒 | **16:9** | `abra_t2v_6s` | **`2`** | 拦截比价（未真发） |
| C | Omni 1.1 Flash | **4 秒** | 16:9 | **`abra_t2v_4s`** | `2` | 拦截比价（未真发） |
| D | Omni 1.1 Flash | 6 秒 | 9:16 | `abra_i2v_6s` | `1` | 真实生成（带 1 张图） |

推理链：

- A vs B：**只有比例变了**，模式串一模一样，唯一变化的是那个整数 `1 → 2` ⇒ **该整数 = 宽高比**
- B vs C：**只有时长变了**，那个整数没动，变的是模式串后缀 `_6s → _4s` ⇒ **时长编码在模式串里**
- A vs D：**只有任务类型变了**（加了参考图），模式串中段 `t2v → i2v` ⇒ **模式串中段 = 任务类型**

### 模式串命名规律（推断）

```
abra        _  t2v   _ 6s
└ 模型家族     └ 任务类型  └ 时长
（Omni 系列）  （t2v/i2v）
```

> ⚠️ 只验证了 Omni 1.1 Flash = `abra`。**Veo 3.1 系列（Lite/Fast/Quality）的代号本次没抓**，不要直接套用。

### 你文档里可以这样写

```python
# 模式串拼装
VIDEO_MODEL_CODE = {
    "omni-1.1-flash": "abra",
    # "veo-3.1-fast":  "???",   # 未验证，待抓
}
def build_mode(model, task, duration_s):
    return f"{VIDEO_MODEL_CODE[model]}_{task}_{duration_s}s"   # task: "t2v" / "i2v"

ASPECT_CODE = {"9:16": 1, "16:9": 2}
```

---

## 5. 响应

标准 `batchexecute` 格式：`)]}'` 前缀 + `wrb.fr` 包裹，payload 是 JSON 字符串。

**T2V 响应（`YhhmEf`）** —— 立刻返回任务记录，同时把请求参数原样回带：

```
[null,1030,
 [[ "<videoId>", null, null,
    ["Golden retriever running on beach",[<ts>],null,null,
     "<jobId>", "<批次UUID>",[<ts>]],
    "<项目id>" ]],
 [[ "<jobId>", "<项目id>", "<videoId>", null, null,
    [[<ts>],"<提示词>",null,null,null,null,
     [null,[["abra_t2v_6s",1,null,null,1,1]],[[null,null,[[["<提示词>"]]]]],null,1],
     null,[6],1],
    null,[[null,549707,null,null,null,null,null,"<提示词>",null,null,null,null,"abra_t2v_6s","",null,false,1],
          [null,null,[6]],["<jobId>"]]]]]
```

实测 id：

| 项 | T2V | I2V |
| --- | --- | --- |
| jobId | `c2be96a9-b93c-439b-9eb8-5106ef3b6f6b` | `48821156-f540-404a-b5c3-2a91622a86a1` |
| videoId | `ec8ab5a9-5e1f-456e-bad3-27e1cfeb2866` | `0865de74-b46e-4844-b092-bb954d4f5047` |

**成品地址**（视频也用图片那个 CDN 端点，靠签名鉴权）：

```
https://flow-content.google/image/<jobId>?Expires=<unix>&KeyName=labs-flow-prod-cdn-key&Signature=<sig>
```

**轮询（`jwpduf`）**：

```
请求 f.req: [[["jwpduf","[null,null,[\"<jobId>\"]]",null,"generic"]]]
响应      : [null,null,[[ "<jobId>","<项目id>","<videoId>", … ]]]
```
生成开始后约每 3 秒一次。随任务推进，记录尾部会追加数值（最后一帧出现 `1779242`，疑似成品文件字节数）。

---

## 6. 本次副作用与还原情况

| 项 | 状态 |
| --- | --- |
| 实际生成的视频 | 2 支：①「Golden retriever running on beach」(T2V, 6s, 9:16)　②「Cat looking at camera」(I2V, 6s, 9:16) |
| 询问代价 | 视频每次约 **10 个点数**，本次真跑 2 次 |
| 拦截产生的失败占位 | 2 个，**已删除** |
| 注入的 fetch/XHR 钩子 | **已移除并校验**（`window.fetch` / `XHR.prototype.send` 均恢复原生） |
| 面板状态 | **已还原**：Omni 1.1 Flash · 视频/帧 · 9:16 · 720p · 6 秒 · x1，起始帧已清空 |

> 抓参数时用了一个"拦截请求体后丢弃"的临时钩子：能拿到请求体但请求不发出去，因此 B、C 两组对照**没有消耗点数**。

---

## 7. 还没验证 / 建议下一步

1. **Veo 3.1 三个档位的代号**（Lite / Fast / Quality）——同一套路再抓 3 次即可，但注意 Veo 的分辨率/时长组合和 Omni 不同。
2. **360p / 720p 落在哪**——请求体里**没有**看到分辨率字段，怀疑在会话令牌里或走服务端默认。改分辨率再抓一次就能确认。
3. **x1 / x2 / x3 / x4 输出数量落在哪**——请求体里也**没有**看到。两种可能：客户端展开成多次调用，或写在令牌里。改 x2 抓一次即可分辨（看是 1 条请求还是 2 条）。
4. **上传本地图**：用户自己传一张图走 I2V 时，mediaId 是怎么拿到的（应该有一个上传 RPC）。本次用的是项目里已有的图，没触发上传。
5. **完成态判定**：`jwpduf` 响应里没看到显式的 `status=done` 字段，建议以"记录尾部出现成品大小 / 出现可下载签名 URL"为准。

---

## 附：文件清单

| 文件 | 说明 |
| --- | --- |
| `captures/flow-t2v-req7.network-request` | T2V 真实请求原始体（rpcid=YhhmEf） |
| `captures/flow-i2v-62.network-request` | I2V 真实请求原始体（rpcid=eb1hJf） |
| `scripts/parse_flow_video_request.py` | 一条命令解析上述文件，打印模式串/比例/提示词/参考图 mediaId |
| `Flow模型代号抓包结论.md` | 上一轮图片模型代号结论（BELUGA / GEM_PIX_2 / HARBOR_SEAL） |

用法：

```bash
python scripts/parse_flow_video_request.py captures/flow-t2v-req7.network-request
```
