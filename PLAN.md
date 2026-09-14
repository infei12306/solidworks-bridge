# SOLIDWORKS Bridge —— 接入方案与执行计划

状态:**五个阶段全部完成;`tests/selftest.py` 四套件 57 项检查全绿。可发布(尚未推送)**
目标:让 DSH 像驱动 MATLAB 那样驱动本机**已运行的** SOLIDWORKS 2025(Windows COM 自动化),
并封装为 DSH skill `solidworks-bridge`。

---

## 一、探测结论(全部为实测,非推断)

### 1. Python 运行时

| 项 | 实测结果 | 结论 |
|---|---|---|
| 主用解释器 | `C:\Users\19443\AppData\Local\Programs\Python\Python311\python.exe` = 3.11.9 **64 bit (AMD64)** | ✓ 位数与 SW(64 位进程)匹配 |
| pywin32 | **312 已装** | ✓ COM 客户端可用 |
| numpy / pillow | 2.4.6 / 12.3.0 | ✓ 数组交换与 BMP→PNG 转换可用 |
| 3.12.3 | 已装,但 **没有 pywin32**,也没有 comtypes | ✗ 不要用 3.12,固定 3.11 |
| `pythoncom.CoRegisterMessageFilter` | **不存在**(pywin32 未暴露) | ⚠ 不能用 `IOleMessageFilter` 正解,改用重试 |

### 2. SOLIDWORKS 2025

| 项 | 实测结果 |
|---|---|
| 版本 | **SOLIDWORKS 2025 SP5.0**,`33.5.0.0053` |
| 安装位置 | `D:\Mango\sw2025\SOLIDWORKS Corp 2025\SOLIDWORKS\`(**不在 C 盘**) |
| ProgID | `SldWorks.Application` / `SldWorks.Application.33`(无 .31/.32) |
| CLSID / 服务 | `{6AF263BB-EB9F-4176-89E9-4F892EB0CA3D}` → `sldworks.exe`(LocalServer32,进程外) |
| 当前进程 | **主程序没在运行**;只有 `sldworks_fs.exe` |
| 历史可用性 | 有 2026/9/12 的 `swxJRNL.swj`、`graphicslog.json` → SW 此前正常启动运行过 |
| 界面语言 | 中文(`lang\chinese-simplified`) |
| VBA | `VBE7.DLL`(VBA7.1,64 位)与 `swvba.tlb`、`swVBAServer\` 均在 → `RunMacro2` 可用 |
| 空闲内存 | **4.4 GB / 15.2 GB** → 启动 SW 偏紧,动手前先关掉 Edge / 播放器等 |

### 3. 类型库(最关键的一条发现)

| 项 | 实测结果 |
|---|---|
| TLB 文件 | `sldworks.tlb`(2.0 MB)、`swconst.tlb`(769 KB) |
| LIBID | sldworks `{83A33D31-27C5-11CE-BFD4-00400513BB57}` / swconst `{4687F359-55D0-4CD3-B6CF-2EB42C11F989}` |
| **内部版本号** | **33.0**(与 SW 2025 = v33 一致) |
| 注册表登记版本 | 却被登记成 `21.0`(win64)和 `1.0`(win32) |
| 后果 | COM 校版本不符 → `LoadRegTypeLib` / `gencache.EnsureModule` / `EnsureDispatch` **全部报 `TYPE_E_LIBNOTREGISTERED` (0x8002801D)** |
| 绕法(**已验证可用**) | `pythoncom.LoadTypeLib("<tlb 路径>")` 直接读文件 → `makepy.GenerateFromTypeLibSpec` 生成成功 |
| 生成产物 | `…x0x33x0.py`:sldworks **6.27 MB / 998 个接口类**、swconst **688 KB**(枚举全在 `constants` 类内)、另有 1 个被引用的 TLB 70 KB |
| 必需 API 抽查 | `OpenDoc6` `SaveAs3` `SaveBMP` `RunMacro2` `ExitApp` `CommandInProgress` `GetMassProperties2` `ForceRebuild3` `GetBodies2` `InsertSketch` `SetUserPreferenceToggle` —— **全部在 TLB 内** ✓ |

> 这条发现同时解释了为什么常见教程里 `EnsureDispatch("SldWorks.Application")` 在这台机器上必然失败。
> 本方案**绕开注册表**,直接从 .tlb 文件生成 stubs,并把 stubs 落到固定目录(不用会被清理的 `%TEMP%\gen_py`)。
> 附带好处:拿到了真实枚举名(如 `swOpenDocOptions_Silent`),而不是硬编码数字。

### 4. 与 matlab-bridge 的机制对照

| 能力 | matlab-bridge 的做法 | solidworks-bridge 的做法 |
|---|---|---|
| 挂到已运行实例 | `Marshal.GetActiveObject('Matlab.Application')` | `GetActiveObject('SldWorks.Application.33')`,同样走 ROT |
| "执行任意代码" | `Execute("…")` 字符串 | **不需要 VBA**:DSH 直接写 Python 调早绑定 COM API,Python 本身就是注入语言 |
| 取回数据 | `GetVariable` / `-Push` `-Pull` CSV | API 返回 VARIANT 数组→numpy;导出 STEP/STL/CSV;自定义属性;`GetMassProperties2` |
| 抓画面 | 抢焦点 + 截屏裁切 | **优先 `IModelDoc2.SaveBMP`**(渲染图形区,不抢焦点、不怕遮挡);失败再退到窗口 GDI 抓取 |
| 长任务 | `timer` 异步 + 状态文件 | 子进程异步 + 状态文件(同构,且更简单) |
| 部署 | `%LOCALAPPDATA%\mlbridge\` + skills 目录 **Junction** | 同构:`%LOCALAPPDATA%\swbridge\` + Junction |

---

## 二、方向收敛(三个决定,不再动摇)

1. **驱动用纯 Python,不用 PowerShell 主驱动。**
   pywin32 本来就必须装;Python 里同时解决了 COM、numpy、图像处理,省掉 powershell↔python 的一层引号地狱
   (中文路径 + 括号在本机已经反复出过事故)。PowerShell 只保留一个 `install.ps1` 做部署与建 Junction。

2. **不走 VBA / 不生成 `.swp`。**
   `.swp` 是 OLE 复合文档,凭空生成既脏又脆;而 SW 对任何 COM 客户端都开放完整的 `IDispatch` 对象模型,
   Python 直接就是"能执行任意逻辑"的那一层。VBA 只作为**可选**能力保留给用户已有的宏(`RunMacro2`)。

3. **数据一律走文件,stdout 只放摘要。**
   沿用 matlab-bridge 的铁律:大数组、装配体遍历结果、图片都写盘,控制台只回 `STATE / 路径 / 计数 / 尾部 N 行`。

---

## 三、架构

```
D:\桌面文件\workspace\solidworks-bridge\      ← 仓库根(= 技能本体)
  SKILL.md            技能入口(英文,与 matlab-bridge 一致;含硬规则与工作流)
  REFERENCE.md        设计决策 + 踩坑日志 + 路径解析
  PLAN.md             本文件(内部)
  LICENSE  README.md  .gitignore  .gitattributes
  scripts/
    install.ps1       部署驱动到 %LOCALAPPDATA%\swbridge + 建 skills Junction + 生成 stubs
    genstubs.py       从 .tlb 直接生成早绑定 stubs(绕开注册表)
    selftest.py       端到端验收
  sw/
    swbridge.py       CLI 入口(子命令)
    swcore.py         attach/launch、STA 初始化、重试、日志与状态、异步投递
    swenums.py        stubs 加载器(暴露 swconst 枚举 + 常用常量)
    swdoc.py          文档/草图/特征/质量属性/导出 的高层封装(单位与 1-based 陷阱在此吸收)
    swshot.py         SaveBMP → PNG(含窗口 GDI 兜底)
```

安装后:

```
%LOCALAPPDATA%\swbridge\       驱动运行副本 + stubs\ + logs\ status\ shots\ out\
C:\Users\19443\.dsh\skills\solidworks-bridge   → Junction 指向仓库根
```

### CLI 表面(初稿)

| 命令 | 作用 |
|---|---|
| `swbridge doctor` | 环境体检:Python 位数、pywin32、SW 安装与版本、TLB 版本错配、ROT 是否可挂、内存 |
| `swbridge attach` | 挂到已运行实例,报告版本/进程号/打开文档列表/活动文档/是否正在命令中 |
| `swbridge launch [--wait 180]` | 未运行则启动并等待 COM 就绪(记录耗时与内存) |
| `swbridge run --file job.py` / `--code "…"` `[--async]` | **主入口**:在活会话里跑任意 Python,状态/日志落盘 |
| `swbridge open <path>` `close` | 打开/关闭文档(静默、只读、错误码全查) |
| `swbridge info` | 文档摘要:类型、路径、单位、特征数、配置、质量属性、包围盒 |
| `swbridge save <path> [--as step\|stl\|pdf\|png\|iges\|dxf]` | 导出并校验产物存在且非空 |
| `swbridge shot [--out x.png]` | 抓图形区;失败退窗口抓取 |
| `swbridge status --id <id>` | 只读状态文件,可在 SW 忙时轮询 |
| `swbridge quit` | 退出实例(**默认只退我们自己启动的**) |

---

## 四、分阶段执行与验收标准

### Phase 1 — 地基验证(不过不进下一步)
1. `genstubs.py` 把 stubs 生成到 `%LOCALAPPDATA%\swbridge\stubs\`,**不依赖 `%TEMP%\gen_py`**。
2. 启动 SW(记录冷启动耗时、峰值内存),确认 `SldWorks.Application.33` 进入 ROT。
3. attach 冒烟:取到 `RevisionNumber=33`、进程号、文档数。
4. 新建零件 → 草图 → 拉伸 → `SaveBMP` 出 PNG,**确认不是黑屏**。
5. **验收**:上述 4 步全绿,并把"冷启动耗时/内存"写进 REFERENCE.md。

### Phase 2 — 驱动骨架
`swcore` + `swbridge` CLI 全子命令可用;`run --async` 能在 SW 忙着的时候被 `status` 轮询。

### Phase 3 — 硬骨头(本项目的真正风险所在)
- `RPC_E_CALL_REJECTED (0x80010001)` / `RPC_E_SERVERCALL_RETRYLATER (0x8001010A)`:**重试+退避**先落地;
  不够再上 ctypes 手搓 `IOleMessageFilter`(列为可选,不阻塞交付)。
- **模态对话框**:预先用 `SetUserPreferenceToggle` 关掉确认框并把改动记进日志(可回滚);
  任何可能弹框的调用都要有超时看门狗,绝不允许无限期挂死。
- **单位陷阱**:SW API 长度单位是**米**、质量是**千克**;对外一律提供 `mm()` / `from_m()` 显式换算。
- **索引陷阱**:SW 集合多为 **1-based**;`GetBodies2` 返回 0-based 数组。封装层统一成 Python 语义。
- `OpenDoc6` / `SaveAs3` 的错误码走 out 参数,**必须逐一检查**否则会静默返回 `None` 文档。

### Phase 4 — 端到端验收(可重复)
`tests/selftest.py`:新建零件 → 草图 → 拉伸 → 质量属性对账 → 导出 STEP/STL/PNG → 回读校验尺寸,
最后 `quit` 清场。**要求全绿**,产出截图与校验记录。

### Phase 5 — 打包为 DSH skill
`SKILL.md`(英文,硬规则 + 工作流 + 故障表)/ `REFERENCE.md` / `scripts/install.ps1`;建 Junction 安装。
发布到 GitHub 与 `matlab-bridge` 对称 —— **以 Phase 4 全绿为前提**,不达标就不发。

---

## 五、风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| TLB 注册版本错配(已实测) | `EnsureDispatch` 必失败 | 直接从 .tlb 生成 stubs,已绕开 |
| 模态框挂死 COM 调用 | 桥永久卡住,占住 SW | prefs 抑制 + 看门狗超时 + 只读 `status` 轮询 |
| `RPC_E_CALL_REJECTED` | 随机失败 | 重试退避;必要时 ctypes 消息过滤器 |
| 内存只剩 4.4 GB | 启动/大装配可能失败 | 动手前提示关内存占用大的程序;失败要给出可读原因 |
| 中文界面 | 弹窗/错误文本是中文 | 不做英文串匹配;错误处理基于错误码与对象状态 |
| SW 是 GUI 程序、抢焦点 | 打扰用户 | 尽量用免焦点 API(`SaveBMP`、静默打开);仅截图兜底时才抢焦点 |
| 用户已有文档 | 误改误删 | 只在自建临时目录造件;`quit` 默认只退自启实例;开关文档前查 `GetSaveFlag` |

## 六、明确不做

- 不写 .NET add-in(那是"SW 反过来调我们"的另一条路线,不属本次目标)。
- 不改用户 SW 的用户偏好(除必要的弹窗抑制,且记录原文以便回滚)。
- 不碰用户现有文档;不发布未通过 Phase 4 的成果。

---

## 七、Phase 1 实测结果(已通过 ✅)

`tests/phase1_smoke.py` 端到端一次跑通,退出码 0:

```
OK    attach       revision=33.5.0 pid=33744 docs=0 attach_ms=13
      state        CommandInProgress=False
OK    newpart      title='零件6' type=1
      plane        '前视基准面'  (tree head: ['FavoriteFolder', 'HistoryFolder', ...])
OK    sketch       rectangle 50x30 mm on '前视基准面'
OK    extrude      feature='凸台-拉伸1'
      bodies       1
OK    savebmp      returned=True exists=True size=4800054
      render       100.0% non-black, 793 unique colours, (1600, 1000)
OK    close        '零件6' closed, docs now 0
```

截图经视觉确认:等轴测视角的长方体,**画面里没有任何 UI 元素**(纯图形区 + 渐变色背景),
说明 `SaveBMP` 正是抓图该用的通道——不抢焦点、不怕遮挡、不含菜单栏。

### 新增的能力与实测数字

| 项 | 结果 |
|---|---|
| 挂载耗时 | **13 ms**(pywin32 `GetActiveObject`);本机 .NET `Marshal.GetActiveObject` 反而不通 |
| 版本串 | `33.5.0`(不是 `33`)——版本判断要取小数点前 |
| 冷启动 | 5 s 出 splash;主窗口在随后轮询中已就绪;空载内存约 416 MB |
| stubs 生成 | **2 s**(6.27 MB + 0.69 MB + 8199 个常量) |
| `SaveBMP(1600,1000)` | 4.8 MB BMP,100% 非黑,793 色 |
| 建件入口 | `ISldWorks.NewPart()` **不需要模板路径**;`GetType()`=1=swDocPART |
| 查签名工具 | `sw/swapi.py` 已可用(离线解析 stubs),例:`IPartDoc.GetBodies2(BodyType, BVisibleOnly)` |

### Phase 1 挖出的五个坑(全部已写成代码注释与规则)

1. **`ActiveDoc`/`GetFirstDocument` 在 TLB 里没有返回类型的 CLSID** → pywin32 给的是后期绑定
   `CDispatch`;此时**属性被当成方法调用**,报 `DISP_E_MEMBERNOTFOUND 0x80020003 '找不到成员'`。
   而 `PyIDispatch.GetTypeInfo()` 在 SW 对象上直接报 `DISP_E_BADINDEX 0x8002000B '无效索引'`,
   所以**无法在运行时探测类型**。→ 只能显式 `cast(obj, 'IModelDoc2')`;
   按 memid 猜接口会有"调用到无关方法"的真实副作用风险,**禁止**。
2. **接口转换是必需的**:`GetBodies2` 在 `IPartDoc` 上、`Extension` 在 `IModelDoc2` 上且返回
   `IModelDocExtension`。同一个 `_oleobj_` 换类包装即可。
3. **属性 vs 方法**:`Extension`/`SketchManager`/`FeatureManager` 是属性;
   `GetTitle`/`GetPathName`/`GetType`/`FirstFeature` 是**方法**,必须调用。
   用 pywin32 自己的 `_prop_map_get_` 判定,不要用 `callable()` 猜(后期绑定时会误判)。
4. **中文界面**:基准面叫 `前视基准面`、拉伸特征叫 `凸台-拉伸1`。→ 按 `GetTypeName2()` 找面
   (`RefPlane`),不按名字。
5. **`CloseDoc` 遇到未保存文档会弹模态框并把桥永久挂死** → 先 `SetSaveFlag()` 再关。

单位:**米**(0.05 = 50 mm)。这条会进 REFERENCE.md 的醒目位置。

---

## 八、Phase 2 实测结果(已通过 ✅)

`tests/phase2_driver.py` —— **22/22 全过**,退出码 0。全部通过 shell 出去调 `swbridge.py`
完成,即按 DSH 实际会用的方式验收:

```
PASS attach reports a 2025 session        revision : 33.5.0
PASS run make_box exits 0
PASS mass properties decoded              status=0 values=13
PASS part written                         61251 bytes
PASS render is not a black frame          99.9% non-black, 681 colours
PASS volume matches an analytic box       volume=3e-05 m^3
PASS surface area matches                 area=0.0062 m^2
PASS centroid at the box centre           com=[0.025, 0.015, 0.01]
PASS open re-opens the saved part / info sees 1 body
PASS shot writes a PNG
PASS --async returns immediately          0.09s
PASS async task is RUNNING right after launch / async task finishes
PASS failing job reports ERR + keeps the message
PASS session left clean                   (0 documents)
```

### 交付物

| 文件 | 作用 |
|---|---|
| `sw/swcore.py` | 机器房:ROT 挂载、`cast/getv/call/call_out`、重试、Session、状态/日志/异步、doctor |
| `sw/swbridge.py` | CLI:`doctor attach launch run status shot open info close quit api enum` |
| `sw/swapi.py` | 离线查真实签名 / 枚举 / **属性 vs 方法**(`--props`) |
| `tests/phase1_smoke.py` | 地基验收(建件→渲染→关闭) |
| `tests/phase2_driver.py` | 驱动验收 22 项,拒绝在已有文档的会话上乱动 |
| `tests/jobs/make_box.py` | 参考 job:50×30×20mm 方块,建模/测量/保存/渲染 |

### 关键发现:`GetMassProperties2` 的 13 个返回值(实测十进制)

```
[0..2] 质心 x,y,z        0.025, 0.015, 0.01     (m)
[3]    体积              3.0e-05                = 30000 mm³
[4]    表面积            0.0062                 = 6200 mm²
[5]    质量              0.03                   (kg,默认材料密度 1000 kg/m³)
[6..8] 惯性矩 Lxx,Lyy,Lzz 3.25e-6, 7.25e-6, 8.5e-6   ← 与 m(a²+b²)/12 解析解逐项相符
[9..11] 惯性积 Lxy,Lzx,Lyz ≈ 0
[12]   密度              1.0                    (g/cm³)
```

三项都与解析解吻合到 1e-6 相对误差,这就是 Phase 4 的验收基准。

### Phase 2 挖出的四个坑

1. **`[in,out]` 参数 pywin32 不写回,而是追加到返回值**。四种写法只有一种能用:
   `VARIANT(VT_BYREF|VT_I4)` → `TypeError: int() argument ... not 'VARIANT'`;
   `[0]` → 同样的 TypeError;`pythoncom.Missing` → `DISP_E_PARAMNOTOPTIONAL`;
   **普通 `0` → 成功**,且 out 值作为元组尾部返回。→ 封装成 `OUTARG` + `call_out()`。
2. **同一个名字在一条路径上是属性、另一条上是方法**:`FirstFeature()` 返回**后期绑定**对象,
   而后期绑定对象上 `GetTypeName2` 会被 PROPERTYGET 静默解析成字符串,于是 `feature.GetTypeName2()`
   报 `TypeError: 'str' object is not callable`。→ 必须 `cast(feat,'IFeature')`;
   `call()` 现在遇到非可调用值会直接给出"这是属性,请用 getv"的提示。
3. **`SaveAs3` 声明为 `VT_I4`,返回的是错误码不是 bool,`0` 表示成功**。按真假判断会把成功当失败。
   (带 Errors/Warnings 的是 `SaveAs4`。)
4. **错误处理自己会掩盖真因**:`exc.args[0]` 可能是字符串,却拿去和 `0xFFFFFFFF` 做 `&`,
   抛出的 `TypeError` 把真正的异常盖掉了。→ `_hresult_of()` 只认 int。

另外确认:pywin32 的 `GetActiveObject(CLSID)` 是**纯 ROT 查询**,SW 同时以
`SolidWorks_PID_33744` 和 `!{CLSID}` 两个 moniker 注册,所以整条链路不碰注册表和
`%TEMP%\gen_py`,坏注册表彻底绕开。

---

## 九、Phase 3 实测结果(已通过 ✅)

`tests/phase3_hard.py` **26/26**、`tests/phase3_modal.py` **9/9**。

### 发现一:busy 拒斥的重试逻辑已验证,但真实拒斥在这台机器上**没能复现**

重试路径用合成 `com_error` 做确定性验证:重试到成功即止、耗尽后报错并保留 HRESULT 与
可读提示、**不重试**不可重试的 HRESULT(只尝试 1 次)、以及普通异常不被伪装成 HRESULT。

### 发现二(最重要):模态框的危害是**阻塞**,不是拒斥

真的用 `SendMsgToUser2` 弹出模态框后,从**另一个进程**连打 150 次 COM 调用:
**150/150 全部成功,零错误**。而发起对话框的那个 job 一直停在 `RUNNING` —— 因为
`SendMsgToUser2` 要等对话框被回答才返回。也就是说 SW 的模态循环会**重入式地**继续服务
其他客户端的 COM 调用,并不拒绝它们。于是:

* 模态框只能靠"**发现 + 代答**"救援(`dialogs` / `dismiss`),不能指望超时或重试;
  这正是那套工具存在的理由,而它工作正常:代答 Cancel 后,卡住的 job 正常跑完并记录
  `dialog answered with 7`(`swMbHitCancel`)。
* 反向警告:模态框期间**别**从第二个进程猛打 API —— 调用会重入进模态循环里执行。
* `dismiss` 默认 **Cancel**:在"是否保存"上点 OK 等于同意覆盖文件。

### 发现三:跨进程读不到对话框正文

`GetWindowText` 拿得到按钮(`确定 | 取消`),拿不到承载正文的 Static。阻塞式的
`SendMessage(WM_GETTEXT)` 能读,但**可能卡死在我们正想解除的那个对话框上** → 故意不读;
job 自己的日志才是"它问了什么"的记录。

### 导出矩阵(实测)

`SaveAs3(path, 0, swSaveAsOptions_Silent)`:**格式由扩展名决定,version 必须 0,options 必须含 Silent**,
返回值是错误码(0 = 成功):

| 格式 | 字节 | 结构验真 |
|---|---|---|
| `.STEP` | 15740 | 以 `ISO-10303-21;` 开头 |
| `.STL` | 684 | 二进制,头部声明 12 三角形,`84 + 12*50 == 684` |
| `.IGS` | 22386 | `SolidWorks IGES file ...` |
| `.PDF` | 164193 | `%PDF-1.4` … `%%EOF` |
| `.X_T` | 7426 | `**ABCDEFGHIJKLMNOPQRSTUVWXYZ` |
| `.3MF` | 4341 | `PK`(zip) |
| `.OBJ` | — | **失败,码 256**(未装该转换器);`version=1` 失败,码 32 |

STL 那条是本项目最强的验真手段:`84 + 50*n == size` 且 n = 12 —— 截断或空文件**不可能通过**。

---

## 十、Phase 4 / 5:验收与封装(已完成 ✅)

```
SELFTEST: ALL GREEN  (4 suite(s), 57 checks)         23 秒
  phase1         OK       -              3.7s
  phase2         OK       22/22 checks  12.5s
  phase3         OK       26/26 checks   4.0s
  phase3-modal   OK       9/9  checks    2.8s
  session left with 0 document(s)
```

* 18 个受版本控制文件;本地提交 `63c7b2f`(分支 `main`),**尚未推送**。
* `scripts/install.ps1` 已验证:定位 64 位 Python/校验 pywin32 → 部署到
  `%LOCALAPPDATA%\swbridge` → 生成 stubs → 建 junction
  `~\.dsh\skills\solidworks-bridge`。安装后 **DSH 的技能目录里已出现 `solidworks-bridge`**,
  即该技能真的可被发现和调用。
* 三个会动到会话的测试套件(`phase2_driver`/`phase3_hard`/`phase3_modal`)在检测到
  已有文档时**直接拒绝运行**;`selftest.py` 更是先确认会话干净才开始,因此它不会碰用户的工作。
