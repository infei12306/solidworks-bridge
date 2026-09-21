# 模型下载网站清单（实测结论，按"能不能拿到 STEP + 要不要钱"排序）

本清单里的每条都是这个项目里**真的打开过、真的下过或真的失败过**的记录。目的：下次找零件
模型时不要重复试错。价格/门槛随时会变，但"哪家有原厂 STEP、哪家要登录、哪家抓取器进不去"
这几件事变动很慢。

单位约定：**免费** = 不用付费；**需登录** = 免费但要注册账号；**付费** = 金币/积分/会员。
"抓取"一列指的是**用脚本直接抓**能不能拿到（`web_fetch` / `read_page` / `curl`）。

---

## 1. 第一优先：厂商自己的资料中心

| 站点 | 内容 | 价格 | 抓取 | 备注 |
|---|---|---|---|---|
| **正泰 CHINT 资料中心** https://www.chint.net/service/download/type | **原厂 `.stp`**，每型号一个文件 | 免费、**不用注册** | ✗ 普通 fetch 返回 `403 ... Denied by custom_acl`；用 `read_page`（JS 渲染）或浏览器 | **文档类型选「三维模型图」**。覆盖面是自家配电/工控产品线：NB1-63H / NB5LE-63FB / NXHB-125 断路器与隔离开关、ND16 信号灯、NP2 / NP8 按钮、TC / TB 接线端子、HZ5 组合开关、YBLX 行程开关。**没有 KCD 船型开关**（那是外购件）。直链在 `ztkbs.chint.com` 上是**签名链接，约 3 天过期** → 给用户页面链接，别存直链 |
| **WAGO PARTcommunity** https://wago.partcommunity.com | 原厂 222 / 221 系列接线端子 | 免费、需登录 | ✗ 需要第三方 cookie，常只返回页面壳 | 注意 **222 系列只有 412（2线）/ 413（3线）/ 415（5线），没有 414**；四线在 **221-414**。这是这套国产快速端子的"原版" |
| **PARTcommunity / 3Dfindit** https://www.3dfindit.com | 上千家厂商官方目录的聚合 | 免费、需登录 | 部分可读 | 找厂商官方件的第一站；比 TraceParts 好进 |
| **NKK Switches** https://www.nkkswitches.com/3d-cad-library/ | 全系列开关（含翘板） | 免费、不用注册 | 可读 | 但是 NKK 自家几何，跟国产 KCD 尺寸不通，只能当参考 |
| **McMaster-Carr** https://www.mcmaster.com/cad-models/ | 通用五金/电气件，STEP + SolidWorks | 免费、**不用注册** | ✗ 产品网格是纯 JS，抓不到货号 | 官方推荐用它的 **SolidWorks 插件或 API** |

## 2. 社区库（认准具体型号去搜）

| 站点 | 内容 | 价格 | 抓取 | 备注 |
|---|---|---|---|---|
| **GrabCAD** https://grabcad.com/library | 社区上传，STEP/IGES/SolidWorks/STL 都有 | 免费、需登录 | 正文要 JS；**`grabcad.com/screenshots/pics/...` 不带 `Referer` 头返回 `<!DOCTYPE`**，带上就能下 | **KCD 翘板开关这里最全**：`KCD4 30x25 开孔28x22`（作者按实物建模，31.10×25.5 mm，含外唇/卡扣/6.3 mm Faston）、`KCD1-104 15x21`、`KCD1-105 3脚`、`KCD1 圆形 Ø22.5/Ø20`、`KCD11 圆形 Ø16.5`、`KCD4-203`、`KCD1 T125`。**坑：标题可能是错的型号**（`wago-4-pole-or-wago-222-414` 里的 222-414 并不存在），画廊图也可能与文件名不符 → 推荐前先数一遍特征 |
| **3DContentCentral** https://www.3dcontentcentral.com | 供应商 + 用户库 | 免费、需登录 | ✗ 网页对抓取器常 `500` | 有**中国产件的同族克隆**：`KV223-2P/3P/4P/5P/6P`（上传者 simuel liu，分类 Terminal Crimps）。id：**6P = 2812840**、**5P = 3222026**，其余在 `parts/supplier/User-Library/30.aspx`。另有 `KCD11`（id 208370）、`220V船型开关`（id 1400914）、`LA16 方形点动按钮 16mm`（id 2035756）。国内镜像 `.cn`、繁中 `.tw` |
| **TraceParts** https://www.traceparts.com | 厂商官方目录（含**正泰**） | 免费、需登录 | ✗ `403`（CloudFront/WAF） | 直接搜 `rocker switch` 返回 **0 条**，必须按品牌目录进。正泰目录里有 `NXB-63H` 小型断路器 |

## 3. 中文站（便宜但基本都要付/积分）

| 站点 | 内容 | 价格 | 备注 |
|---|---|---|---|
| **迪威模型** https://www.3dwhere.com | 中文件，**SOLIDWORKS 原生**居多 | **￥1/个**；个别 ￥0 | 搜索页是**纯 HTML，好抓**（比 3DCC 那种 500 强多了）。有 `KV223-2P/3P/4P/5P/6P`、`快速接线端子5进5出`、`20进20出`、`12位接线端子` 等 |
| **三维模型网（开拔网）** https://www.sanweimoxing.com | 非标设备 + 电气件，STEP/SolidWorks | 需会员/金币 | 页面写"免费下载"是宣传语，实际看 `所需金币` / VIP 等级 |
| **宏图网** https://www.hongtucad.com | 机械图纸/模型 | **10 金币/份** | 部分条目页脚已提示「图纸不存在或已下架」 |
| **爱给网** https://www.aigei.com | 3D 模型/音效素材 | 金币/VIP | 有 `带灯船型开关`、`D型指示灯 AD116-22DS/B` 等 |
| **3d66（3d溜溜）** https://3d.3d66.com | 3ds Max 室内素材 | 会员 | 对机械 CAD **基本无用** |

## 4. 许可现实（每次都要跟用户讲清楚）

**不存在 CC0 / MIT 授权的断路器、接线端子、开关 CAD。** 结论分三档：

- **厂商门户**（正泰资料中心、WAGO、NKK、McMaster）：免费下载，**版权仍归厂商**，工程选型/内部设计可用，**不可再分发或商用**。
- **社区库**（GrabCAD、3DContentCentral 用户库）：免费账号，**ToU 限非商业**，且是第三方上传，尺寸不保证。
- **中文站**（迪威、开拔、宏图、爱给）：本质是**付费/积分资源**，许可更不明确。

要一份**能自由再分发**的几何，只有两条路：① 按实物自建；② 在现有原厂模型上改（本项目
`一对多端子\` 就是这么来的）。

## 5. 本机网络现实（别在死路上耗时间）

- **`github.com` 经常被 reset**：`git push` 要写成"重试 20 次、每次间隔 60 s"的后台任务，通常几轮内会通一次。
- **`chint.net` 对普通抓取 403**（`Denied by custom_acl`），`grabcad.com` / `traceparts.com` 403，`3dcontentcentral.com` 常 500，`www.chint.net` 偶尔直接 DNS 失败 → **统一走 `read_page`**（Firecrawl，会跑 JS），比 `web_fetch` 成功率高得多。
- 页面正文有 **50 000 字符截断**，厂商产品页的「产品资料」在最后 → 正文看不到时，**从 `Links:` 列表里按顺序取签名直链**（顺序 = 产品样本 → 认证证书 → 试验报告 → 使用说明书 → 三维模型图 → 外形安装尺寸图源文件）。**外形安装尺寸图源文件是 0 实体的图纸源文件，不是零件** → 下载后先数 `MANIFOLD_SOLID_BREP`。
- 下载后**先量尺寸再交给用户**：STEP 文本里用一条正则就能算包围盒（`CARTESIAN_POINT` 的 min/max），并且**正泰不同产品的单位不一致**（ND16 系列是 mm，NB1-63H 是 m）→ 读文件自己的 `SI_UNIT`，不要沿用上一个文件的尺度。
