# Google Flow 模型代号抓包结论

- **日期**：2026-10-08
- **页面**：`https://flow.google.com/project/4bcb3ed9-f4d6-4e32-b668-38c9b3156cab`
- **方法**：chrome-devtools-mcp 接管已登录的真实 Chrome，在 UI 上逐个切换模型 → 填提示词 → 点"开始生成"，抓取生成请求体并解码
- **触发接口**：`POST https://flow.google.com/_/AiSandboxAngularFrontend/data/batchexecute?rpcids=ogiZ0b`
- **消耗**：三次生成均显示"生成将消耗 0 个点数"

---

## 一、结论

| Flow 界面显示 | 请求体里的 `modelName` | 上游 jobId | 抓包 reqid |
| --- | --- | --- | --- |
| 🍌 Nano Banana Pro | **`GEM_PIX_2`** | 1493176058 | reqid=29 |
| 🍌 Nano Banana 2 Lite | **`HARBOR_SEAL`** | 840074683 | reqid=40 |
| 🍌 Nano Banana 2.1 | **`BELUGA`** | 1018428553 | reqid=8 |

### ⚠️ 关键发现：没有 `NARWHAL`

当前 Flow 的「图片」模型菜单只有上面三项，**三者的代号都不是 `NARWHAL`**。

所以文档里那条映射：

> ~~Nano Banana 2 / 2.1 对应底层：NARWHAL~~

**对当前线上版本不成立**。`Nano Banana 2.1` 实际下发的是 **`BELUGA`**。

如果代码里已经有 `NARWHAL`，那它可能来自更早的版本，或者来自其它入口（如"创建工具" / NextChat 路径），需要单独验证——本次抓的"图片"直出通路里完全没有出现。

---

## 二、请求结构

请求头 `Content-Type: application/x-www-form-urlencoded;charset=UTF-8`，body 只有一个 `f.req` 字段，值是 URL 编码的 JSON 字符串。解码后形如：

```
[[["ogiZ0b","[null,[[null,null,null,<jobId>,2,\"<MODEL>\",null,
   [null,<enum>,null,null,null,\"<projectId>\",null,null,null,null,[\"<长token>\",1]],
   [[[\"<提示词>\"]]],null,null,null,\"<UUID>\",\"<UUID>\"]],1,[...]]",null,"generic"]]]
```

字段含义（基于三次抓包比对）：

| 位置 | 含义 | 三次观测值 |
| --- | --- | --- |
| RPC id | `ogiZ0b` = 图片生成 | 固定 |
| `[3]` | 上游 jobId，每次调用不同 | 1018428553 / 1493176058 / 840074683 |
| `[4]` | 类型枚举，三次都是 `2` | 2 |
| `[5]` | **模型代号**（本次要抓的目标） | BELUGA / GEM_PIX_2 / HARBOR_SEAL |
| `[7][1]` | 枚举，三次都是 `22` | 22 |
| `[7][5]` | 项目 id | `4bcb3ed9-f4d6-4e32-b668-38c9b3156cab` |
| `[7][10][0]` | 会话/鉴权长 token | 每次都变 |
| `[[[ "..." ]]]` | 提示词原文 | 一只橘猫坐在窗台上 |

响应体是 Google 的 `)]}'` 前缀 JSON，里面带了最终图片地址 `https://flow-content.google/image/<uuid>?...`。

---

## 三、对 Flow2API 的适配建议

`model_resolver.py` 里的别名映射应改成：

```python
CLIENT_ALIAS_TO_MODEL = {
    "nano-banana-pro":     "GEM_PIX_2",
    "nano-banana-2-lite":  "HARBOR_SEAL",
    "nano-banana-2.1":     "BELUGA",
    "nano-banana-2.1-2k":  "BELUGA",   # 2K/4K 是否走同一代号需再验
    "nano-banana-2.1-4k":  "BELUGA",
}
```

注意：本次只验证了 **x1 输出、9:16 比例**。2K/4K 是否改变 `modelName`、还是只改另外一个分辨率字段，**尚未验证**——如果要确认，只需在 Flow 里把输出改成 x2/x4 或换比例后再抓一次，对比请求体差异即可。

---

## 四、复现步骤

1. Chrome 打开 `chrome://inspect/#remote-debugging`，启用远程调试
2. 在 Flow 项目页选中目标模型，写好提示词，点"开始生成"
3. 用 `chrome-devtools-mcp` 的 `list_network_requests` 找 `rpcids=ogiZ0b` 那条 POST
4. 用 `get_network_request(reqid=..., requestFilePath="xxx.network-request")` 把请求体存盘
5. 运行解析脚本：

```bash
python scripts/parse_flow_request.py captures/flow-req-08.network-request
```

输出示例：

```
模型代号  : BELUGA
上游 jobId: 1018428553
类型枚举  : 2
项目 id   : 4bcb3ed9-f4d6-4e32-b668-38c9b3156cab
提示词    : ['一只橘猫坐在窗台上']
```

---

## 五、本次操作的副作用

- 在项目里真实生成了 **3 张图片**（提示词均为"一只橘猫坐在窗台上"），如需清理请到项目里删除
- Flow 页面的模型选择器**当前停在「Nano Banana 2 Lite」**，不是原来的状态，需要手动切回
- 三项均显示消耗 0 个点数
