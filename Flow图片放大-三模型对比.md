# Flow 图片放大：Nano Banana Pro / 2 Lite / 2.1 三个模型一样吗？

> 结论时间：2026-10-08 20:40–20:46 ｜ 项目 `4bcb3ed9-f4d6-4e32-b668-38c9b3156cab`
> 方法：chrome-devtools-mcp 接管真实登录态 Chrome，三模型各生成一张同提示词同比例图 → 各点 2K 高清重塑 → 抓 `SPrCad` 请求体比对 → 前端 bundle 反查调用链

---

## 0. 一句话结论

**放大这一步，三个模型完全一样 —— 同一个接口、同一份请求结构、同一个 2 倍倍率、同一套菜单文案。**

放大**不看你用哪个模型生成**，只看源图的 mediaId。请求体里**根本没有"源模型"这个字段**。

| 源模型 | 源 mediaId | 1K 基准 | 2K 产物 mediaId | 2K 尺寸 | 2K 字节数 |
| --- | --- | --- | --- | --- | --- |
| 🍌 Nano Banana Pro | `c3259594-f335-…`（上轮存档） | 768×1376 | `b3554089-7d03-…` | **1536×2752** | 2,904,433 |
| 🍌 Nano Banana 2 Lite | `60d10e4f-4ffe-4…` | 768×1376 | `f2031f47-0d07-…` | **1536×2752** | 2,746,499 |
| 🍌 Nano Banana 2.1 | `52676eac-7658-…` | 768×1376 | `686469a8-1aa8-…` | **1536×2752** | 2,521,711 |

字节数不同只是因为画面内容不同（苹果 vs 猫），**尺寸分毫不差**。

---

## 1. 抓到的三条 `SPrCad`，结构逐字段一致

三条请求解码后都是同一形状：

```json
["<源图片 mediaId>",
 1,                                    ← field 2，三个模型都是 1
 [null, 22, null, null, null,
  "4bcb3ed9-f4d6-4e32-b668-38c9b3156cab",   ← 项目 id
  null, null, null, null,
  ["0cAFcWeA…", 1]]]                  ← 约 2700 字的上下文签名串
```

外面包一层 batchexecute 信封：

```
f.req=[[["SPrCad","<上面那段>",null,"generic"]]]&at=<token>&
```

三条的差异**只有 f.req 里的 mediaId 和签名串**——签名串跟着图片走，跟模型无关。

### 响应（三个模型同构）

```json
["<新的 2K 资产 mediaId>", "<项目 id>", "<媒体记录 id>", null, null,
 [[时间戳], …, [null,null,null,[[[null,null,"<回指源图 mediaId>"]]],1], …, <字节数>],
 [[…,"<原始提示词>", 字数, …]],
 "/9j/4AAQSkZJRgAB…"      ← 2K 图本体（base64 JPEG，内联在响应里，没有二次 CDN 请求）
]
```

响应里的 C2PA 清单三条都写着同样的两句：

- `c2pa.created` — "Created by Google Generative AI."
- `c2pa.edited` — **"Applied imperceptible SynthID watermark."** ← 重塑这一步统一加不可见水印

---

## 2. 前端代码依据：图片侧**没有**"按源模型挑放大模型"的逻辑

对比视频侧就很清楚了。

**视频放大**（`E8a` → `g_a`）会**按源模型的支持表**去挑放大模型：

```js
g_a=function({ux:a, Ms:b, aspectRatio:c, Kc:d}){     // ux = 源模型的 usages 列表
  …
  d=(e=TN({Jf:a, aspectRatio:c, Qt:f_a(b), …}))?.key;
  a=a.flatMap(f=>f.usages).find(f=>
      f.mx?.includes(b) &&                 // 该模型支持这个放大档位
      f.ui.some(h=>h.some(k=>k===21||k===15||k===16)));
  a = d ?? a?.key;
  if(!a) throw Error("mf`"+b);             // 不支持就报错
  return a
};
f_a=function(a){switch(a){case 1:return[21];case 2:return[15];case 3:return[16];default:return[]}};
```

**图片放大**（`t8a`）则只吃 `{mediaId, Ms}`，**完全不查源模型**：

```js
t8a=function(a,b){return _.z(function*(){
  var c=b.mediaId, d=b.Ms, e=b.ya;
  var f=yield a.ka.Uo("IMAGE_GENERATION");
  f=_.fu(fS(_.SK(eS(new _.TK,e)),f));
  var g=_.W(D5a());
  c=_.Sv(g,1,c);        // field1 = mediaId
  d=_.Vv(c,2,d);        // field2 = Ms
  d=_.wp(d,3,f);        // field3 = 上下文（项目 id 等）
  return yield a.ha.fetch(E5a.nb.call(E5a,_.uc(d)));
})};
u8a=function(a){return _.Ct(()=>({mutationKey:["UPSAMPLE_IMAGE"],mutationFn:b=>t8a(a,b)}))};
```

`SPrCad` 的 proto 也只有两个字段：`Ie()/ki()` → field 1（mediaId）、`X0()` → field 2：

```js
E5a=new _.Nx("SPrCad", class extends _.n{ Ie(){return _.Dp(this,_.Sx,1)}
                                           ki(){return _.Bp(this,_.Sx,1)}
                                           X0(){return _.yv(this,2)} },
             C5a, [_.hm, !0, _.em, "/FlowService.UpsampleImage"]);
```

> **所以"三个模型放大是否一样"在前端就已经是确定答案了：放大入口拿到的是资产，不是模型。**

---

## 3. UI 层面也一致

### 三个模型的参数面板

| | Nano Banana Pro | Nano Banana 2 Lite | Nano Banana 2.1 |
| --- | --- | --- | --- |
| 比例 | 16:9 / 4:3 / 1:1 / 3:4 / 9:16 | 同左 | 同左 |
| 张数 | x1 / x2 / x3 / x4 | 同左 | 同左 |
| 消耗 | 0 个点数 | 0 个点数 | 0 个点数 |

逐项切过去读 DOM，**没有任何一项不同**。

### 下载菜单（对每张图都一样）

```
1K 原始尺寸
2K 已高清重塑
4K 已高清重塑
```

档位映射（前端 `TWa`）：

```js
TWa=function(a){switch(a){
  case "1K":return "ORIGINAL_IMAGE";
  case "2K":return "2K";
  case "4K":return "4K";
  default:return _.sb(a,void 0)}};
```

---

## 4. 分辨率换算表（9:16）

| 档位 | 处理方式 | 尺寸 | 说明 |
| --- | --- | --- | --- |
| 1K 原始尺寸 | 纯 CDN GET，无服务端处理 | 768×1376 | 三个模型基准一模一样 |
| 2K 已高清重塑 | `SPrCad` 同步，响应内联 base64 | **1536×2752** | 正好 2×（4× 像素） |
| 4K 已高清重塑 | 同一 `SPrCad`，模型枚举换 `GEM_PIX_2_UPSAMPLE_4K`（未点击验证） | — | 前端枚举 26/27 成对出现 |

---

## 5. 点数：文案说扣 1 点，实测没看到扣

前端文案（`_.xXa`）：

```
{CREDIT_COST,plural, =1{已高清重塑 · 消耗 1 个点数}other{已高清重塑 · 消耗 # 个点数}}
```

**但连续做了 2 次 2K 之后，账号菜单仍显示「1,000 个 Google Flow 点数」**，与操作前一致。
两种可能：① 该数字是订阅额度的静态展示、不实时联动；② 这一档当前不扣。未能定论。

---

## 6. 本次副作用清点

- 项目里新增 **2 张生成的图**（都是同一个提示词 `a red apple on a wooden table, studio light` / 9:16）：
  - Nano Banana 2 Lite 版（`60d10e4f-…`）
  - Nano Banana 2.1 版（`52676eac-…`）
- 各自生成了 1 个 2K 资产（`f2031f47-…` / `686469a8-…`）
- Downloads 新增：`Red_apple_on_wooden_table_2K_20261008204400.jpg`、`…_20261008204558.jpg`
- 未注入任何抓包钩子（本轮全部用 DevTools 网络面板事后读取，零 hook）
- 面板已还原：模型 Nano Banana Pro · 9:16 · x1 · 输入框已清空

> 若要清掉这两张测试图，说一声即可（会连同它们的 2K 资产一起移入回收站）。

---

## 7. 证据文件

| 文件 | 内容 |
| --- | --- |
| `captures/flow-img-2k-SPrCad.network-request` | Nano Banana Pro 的 2K 请求体（上一轮存档） |
| `captures/flow-img-2k-2lite-SPrCad.json` | **Nano Banana 2 Lite** 的 2K 请求体（解码版） |
| `captures/flow-img-2k-21-SPrCad.json` | **Nano Banana 2.1** 的 2K 请求体（解码版） |
