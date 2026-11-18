# 仪表画面的矩形搬运与取证

`README.md` 记录现有录制、采集、固定区框领取和重开续接的接口。值班员现在也会收到屏内搬运指令。质检员按固定帧领取时，要能回查提供灰度的原图块，并知道这格灰度最近一次搬运取自屏幕何处。

## 录入

从 `intake` 增加 `MoveRect(source, target)`，沿用 `capture(db, recording_id, DeviceFrame(header, blocks), before_commit=...)`。`source` 和 `target` 都是屏内非空的零基半开 `Rect`，宽高分别相等。搬运限于同一录制的当前屏幕。首帧仍为一个整屏 `DeviceBlock`，包含搬运的首帧报 `BaseFrameError`。后继永久帧的 `blocks` 可以是一次性迭代器，按到达顺序混合提供 `DeviceBlock` 和 `MoveRect`，也可以为空。

搬运读取本命令开始前的候选画面，包含同帧较早命令的结果，源区与目标区可以重叠。目标中的每格对应源区相同偏移的格。命令只写目标区，源区与目标区完全相同时也记录本次搬运。原块号与搬运命令号均按本帧全部条目的到达顺序从零计数。设备原块正文和块内坐标沿用 V1。

非法搬运框报 `RectangleError`，遇到其他不支持的条目类型报 `ValueError`。帧序、原设备块、来源异常和提交错误沿用原约定。任一非法命令、来源异常或提交失败均不得发布该帧。旧帧仍可领取，`resume` 仍指向原确认点，可用新输入重送失败号。

## 按固定帧领取

新增 `inspection.crop_with_moves(db, recording_id, frame_no, rect)`。报告提供 `recording_id`、`frame_no`、`rect`、`pixels`、`origins`、`gray_total`，与 `crop` 同义，另有 `moves` 元组，与 `pixels` 严格按行序一一对应。每项是 `None`，或具有 `frame_no`、`command_index`、`x`、`y` 字段的记录。这里的 `x`、`y` 是搬运命令实际取样的绝对屏幕坐标。`origins` 继续用 `Origin(frame_no, block_index, x, y)` 表示提供灰度的原块，其中 `x`、`y` 仍是块内坐标。

再次搬运保留原块出处并替换搬运证据；原块直写清除搬运证据，未触及位置保持原证据。同值直写也要更新原块出处并清除搬运证据。旧 V1 档从新入口取得同序的 `None`。固定报告在停源、重开和后续采集后仍可按原号领取。继续支持原 `crop`、`inspect` 的报告类型及原有字段，支持 `resume` 的原有行为。新入口的未知录制、未发布帧、非法区框与 `crop` 使用相同异常。读取只需固定档案和请求区框。

## 单遍样例

`move_examples.py` 提供小屏输入和大屏最后一帧。基线可先运行 `python3 -B move_examples.py` 验证它在 V1 写入并重开领取第 10 帧。该检查预期得到 `[1,2,3,4]`、灰度和 10、原块 0 的四个位置及下一帧号 11。

实现新接口后，在一个新档案上用以下方式消费样例。`small_frames` 按原设备形式送帧，每帧的命令流只消费一次。

```python
from pathlib import Path
from tempfile import TemporaryDirectory

from archive import ArchiveDatabase
from inspection import crop_with_moves
from instrument_demo import collect
from intake import DeviceStream, MoveRect
from move_examples import SMALL_ID, SMALL_SCREEN, small_frames, small_spec
from registration import register

with TemporaryDirectory() as folder:
    path = Path(folder) / 'archive.sqlite'
    with ArchiveDatabase(path) as db, DeviceStream(small_frames(MoveRect)) as device:
        register(db, small_spec())
        collect(db, SMALL_ID, device)
    with ArchiveDatabase(path) as db:
        report = crop_with_moves(db, SMALL_ID, 11, SMALL_SCREEN)
        print(list(report.pixels), report.gray_total, report.origins, report.moves)
```

下表按从左到右列出结果。`origins` 的四元组依次为帧、原块、块内 x、块内 y。`moves` 的四元组依次为帧、命令、屏幕 x、屏幕 y。

| 帧 | 灰度 | 灰度和 | origins | moves |
| --- | --- | ---: | --- | --- |
| 10 | `[1,2,3,4]` | 10 | `[(10,0,0,0),(10,0,1,0),(10,0,2,0),(10,0,3,0)]` | `[None,None,None,None]` |
| 11 | `[9,9,2,3]` | 23 | `[(11,0,0,0),(11,0,0,0),(10,0,1,0),(10,0,2,0)]` | `[None,(11,1,0,0),(11,1,1,0),(11,1,2,0)]` |
| 12 | `[9,9,9,3]` | 30 | `[(11,0,0,0),(12,1,0,0),(11,0,0,0),(10,0,2,0)]` | `[None,None,(12,0,1,0),(11,1,2,0)]` |

## 联合验证与交接

保留并执行全部原回归。用样例验证先写后搬、再搬和同值直写，关源重开后核对各历史帧。保留用 V1 生成的档案供新入口读取。另用小屏横向及纵向跨分块的重叠搬运与逐命令快照参照核对。分别在合法搬运后送入非法框、让命令流抛异常及让提交失败，检查旧成果、未发布帧及同号重试。提交故障可沿用 `before_commit` 和 `tests/test_handover.py` 中的独立观察连接与拒绝 `COMMIT` 的写法。

搬运工作内存不随矩形面积增长，大屏采集峰值增量沿用 16 MiB 上限。可以使用磁盘工作区。按原大屏回归的 `ru_maxrss` 口径，采集进程在打开档案前读取起始值，完成采集后读取峰值，沿用原测试的平台字节换算。以 `instrument_demo.large_recording_spec()` 和 `large_frames()` 接收原 8192×8192 基图和更新，再送入 `move_examples.large_move_frame(MoveRect)` 生成的第 12 帧。采集结束后独立重开领取 `instrument_demo.LARGE_REGION`。该小区的灰度为 `[247,248,249,250,251,252,250,251,82,83,84,255]`，灰度和为 2502。每格搬运证据均为 `(12,0,x-1,y-1)`，其中 `(x,y)` 是目标屏幕坐标。原块出处依行顺序为 `[(10,0,125,126),(10,0,126,126),(10,0,127,126),(10,0,128,126),(10,0,129,126),(10,0,130,126),(10,0,125,127),(10,0,126,127),(11,0,0,0),(11,0,1,0),(11,0,2,0),(10,0,130,127)]`。

沿用 `.pointwise-runtime.json` 的格式。`version` 为 `1`，`kind` 为 `cli`，`setup` 为 argv 列表，`verify` 和 `demo` 各为一个 argv。交付时让 `verify` 实际执行原回归和上述联合核验，断言失败退出非零。`demo` 展示停源并重开后的实际固定报告。附一段说明，交代采集重建怎样把两类出处交给固定领取。
