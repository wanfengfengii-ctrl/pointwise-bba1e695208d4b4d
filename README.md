# 栖原仪表录制归档 V1

栖原环境试验中心的设备员在舱外记录高温试验仪表的灰度画面。脚本设备先发送整屏，再发送局部更新。质检员会按固定帧号核对读数和告警，并从报告中的原位置回查设备图块。交班员可在设备停用后领取证据，翌班设备员可重开档案继续录制。

## 运行

使用 Python 3.11 标准库。完整回归与演示从项目根目录执行。控制台物化时提供 `check_project.py`。

```sh
python3 -B check_project.py
python3 -B instrument_demo.py
```

演示在独立临时目录建立归档，输出各阶段实际取得的像素、灰度和、来源及续接号。大屏回归生成 8192 × 8192 的单遍输入，分别启动采集和停源领取进程。Linux 和 macOS 上以各进程开始业务前的 `ru_maxrss` 为基线，检查峰值增量并输出字节数。小区核账从档案中实际取数。合法较大区框仍可领取，返回结果的大小随请求面积增长。

## 一次录制

1. 设备员用 `register` 固定录制身份、屏幕规格和供班组使用的具名区框。
2. 设备按登记首号发送一个整屏块，`capture` 校验正文并发布基图。
3. 质检员用 `inspect` 领取读数区和告警区，核对行序灰度及整数和。
4. 运行中，脚本按连续帧号发送永久更新，同帧后到块覆盖先到块。
5. 质检员比较新旧帧报告，并用逐像素来源定位变化所用的设备图块。
6. 关闭设备流和采集连接，交班员通过新连接领取原帧证据。
7. 翌班查询 `resume`，用正确的下一帧号录入旁栏更新。
8. 质检员再次领取原帧和新帧，核对旁栏新出处和沿用的读数出处。

## Python 接口

| 导入位置 | 调用 | 结果 |
| --- | --- | --- |
| `archive` | `ArchiveDatabase(path)` | 打开 SQLite 文件并幂等初始化。支持 `with`，也可调用 `close()` |
| `registration` | `register(db, RecordingSpec(identity, screen, regions))` | 返回固定的 `RecordingIdentity` |
| `intake` | `capture(db, recording_id, DeviceFrame(header, blocks))` | 返回 `PublishedFrame`，含录制号和帧号 |
| `archive` | `resume(db, recording_id)` | 返回 `ResumeState(last_frame, next_frame, base_frame)` |
| `inspection` | `crop(db, recording_id, frame_no, rect)` | 返回指定绝对区框的 `RegionReport` |
| `inspection` | `inspect(db, recording_id, frame_no, name)` | 按原登记区框返回 `RegionReport` |
| `intake` | `MoveRect(source, target)` | 屏内等尺寸矩形搬运指令，可与 `DeviceBlock` 混排进同一帧 |
| `inspection` | `crop_with_moves(db, recording_id, frame_no, rect)` | 返回带逐像素搬运证据的 `MoveRegionReport` |

`RecordingIdentity(recording_id, device_id)` 使用非空字符串。`ScreenSpec(width, height, first_frame)` 的宽高为正整数，整数键采用 SQLite 有符号 64 位范围。`NamedRegions` 接受名称到 `Rect` 的映射并固定其内容。登记内容相同可重投，改动身份绑定、规格或区框报 `RegistrationConflict`。初始化和重开保留已登记的数据。

`FrameHeader(frame_no)` 使用 V1 的 `permanent` 模式。`DeviceBlock(rect, body)` 的 `body` 为该块独立的二进制读流，`read(n)` 至多返回 `n` 字节，以空字节串表示块结束。调用方管理块读流的生命周期。输入可为一次性迭代器。使用 `DeviceStream` 时逐次 `next_frame()`，采集该帧后再取下一帧，末尾返回 `None`，也可显式关闭事件源。

矩形 `Rect(left, top, right, bottom)` 使用零基、半开像素坐标。像素按行排列，每个值占一个无符号字节。首帧仅接受一个整屏块，后继帧可为空。帧内末块优先，未覆盖位置延续既有灰度与来源。`Origin(frame_no, block_index, x, y)` 定位原块，块号按帧内到达顺序从零起算，`x`、`y` 为块内坐标。报告的 `pixels` 为 `bytes`，`origins` 为同序元组，`gray_total` 为精确 Python 整数，包含零灰像素。

未知录制、未知区名和未发布帧分别报 `UnknownRecording`、`UnknownRegion` 和 `FrameNotPublished`。非法范围报 `RectangleError`。帧序号错误报 `FrameSequenceError`，不支持的模式报 `FrameModeError`，首帧结构非法报 `BaseFrameError`，正文长度错误报 `PayloadError`。设备读取异常和 SQLite 错误向原调用方传播。任一帧失败后均可用新输入流重送原号，旧档案和确认进度保持有效。`capture` 的可选关键字 `before_commit` 接收无参回调，供集成核验在同一次 SQLite 事务的提交边界注入故障。

## 屏内矩形搬运与两类出处

后继永久帧的条目流可按到达顺序混排 `DeviceBlock` 与 `MoveRect(source, target)`，也可为一次性迭代器或空流。`source`、`target` 是同一录制当前屏幕内非空、等宽等高的零基半开 `Rect`，允许区域重叠；首帧含搬运报 `BaseFrameError`，非法搬运框报 `RectangleError`，其他条目类型报 `ValueError`。原块号与搬运命令号共用本帧全部条目的到达序号，从零计数。

搬运读取本命令开始时的候选画面，因此包含同帧较早条目的结果；目标格取源区同偏移格。实现按拓扑顺序逐分块处理，重叠时从远离源区的方向取样，使每格在被覆盖前先被读取。任一非法命令、来源异常或提交失败都不发布该帧，旧帧仍可领取，`resume` 仍指向原确认点，原帧号可用新输入重送。

每个像素同时保留两类出处：`Origin(frame_no, block_index, x, y)` 始终指向真正提供灰度的设备原块（`x`、`y` 为块内坐标）；搬运证据 `MoveEvidence(frame_no, command_index, x, y)` 指向最近一次搬运实际取样的绝对屏幕坐标。再次搬运保留原块出处并替换搬运证据；设备原块直写会刷新原块出处并清除该格搬运证据，同值直写也一样；未触及位置沿用旧证据。`crop_with_moves` 返回的 `moves` 与 `pixels` 严格按行序一一对应，每项为 `None` 或搬运记录。用 V1 写入、不含搬运段的旧分块从新入口读取时，`moves` 为同序的 `None`。未知录制、未发布帧和非法区框与 `crop` 使用相同异常。

## 分块与固定证据

采集工作区将候选分块暂存在临时 SQLite 文件中。每个分块保存灰度和按行编码的来源区间。覆盖仅替换写入像素的灰度与来源。归档事务把帧清单及候选分块一起提交。已提交分块以录制号、块坐标和帧号为键，仅追加新修订；读固定历史时，每个所需分块选不晚于目标帧的最近修订。续采以最后确认的永久画面为底，只读取本次更新涉及的分块。设备停用和进程重开不影响已提交证据的可领性。

含搬运的分块在原有灰度与来源区间之后追加一段带 `MV01` 标记的搬运证据区间：直写记为清除，搬运记为带帧号、命令号和绝对取样坐标的样本区间，按行以区间合并编码，工作内存只随单个分块与缓存带增长，不随搬运矩形面积增长。不含搬运段的旧 V1 分块仍按原格式读取，新入口将其证据视为全 `None`。8192×8192 大屏在整屏搬运下的采集峰值增量沿用 16 MiB 上限，由 `check_project.py` 的联合核验按 `ru_maxrss` 口径在独立进程中检查。
