# MPEG-1 解码器鲁棒性与合法流回归报告

日期：2026-11-14  
代码：`pl_mpeg`，基线提交 `c871f2b`（`Fix corrupt slice check; close #64`）  
被测接口：原始 MPEG-1 video elementary stream，`plm_video_*` 低层视频解码器

## 结论

| 验收项 | 结果 |
|---|---:|
| 畸形码流 | 5,000 |
| 崩溃 / 越界 / 泄漏 / UBSan / 超时 | **0** |
| 正常拒绝且带字节偏移、位偏移、原因 | 4,519 / 4,519 |
| 合法码流 | 100 |
| 与基线参考实现逐像素一致 | **100 / 100** |
| 合法流帧数、尺寸、Y/Cb/Cr 平面 SHA-256 一致 | **100 / 100** |

基线实现回放同一批 5,000 个畸形输入时出现 **176 个真实失败**：175 个
UBSan 有符号转移 UB，1 个 AddressSanitizer 堆越界读。修复后的实现全部
转为带诊断的拒绝或安全接受。

## 测试方法

- 每个输入在独立进程中运行，编译参数：
  `-fsanitize=address,undefined -fno-sanitize-recover=all`。
- AddressSanitizer：检测堆/栈越界、use-after-free 等；LeakSanitizer
  在每个进程退出时检查泄漏。
- UndefinedBehaviorSanitizer：`halt_on_error=1`，任何有符号溢出、非法
  移位等立即判失败。
- 单例超时 8 秒；解码器自身另有 5 秒 alarm。
- 每类 1,250 例，总计 5,000 例：

| 类别 | 用例数 | 接受 | 拒绝 |
|---|---:|---:|---:|
| 截断 | 1,250 | 63 | 1,187 |
| 错位/字节插入删除/位翻转 | 1,250 | 251 | 999 |
| 非法量化表（含 0 项） | 1,250 | 0 | 1,250 |
| 极端尺寸（最高 4095×4095） | 1,250 | 167 | 1,083 |
| 合计 | **5,000** | **481** | **4,519** |

“接受”不表示输入仍然合法。例如截断恰好发生在完整图像边界时，已解出的
完整帧仍可安全返回；部分错位输入仍构成可解析码流；4095×4095 是 MPEG-1
12 位尺寸字段内的语法合法值。所有这些路径均未触发内存错误。

所有 4,519 个拒绝样本都有非空原因和整数偏移；共 26 种具体原因，包括
截断、非法序列头、零尺寸、零量化矩阵项、非法图像类型、非法 slice、
宏块地址越界、运动矢量越界、非法 VLC 等。

完整逐例结果：

- `validation/results/malformed.json`
- 原始语料：`validation/malformed/<category>/NNNN.m1v`
- 基线回放：`validation/results/baseline.json`

容器层补充烟雾测试：对 300 个截断/字节错位的 MPEG-PS 输入运行高层
`plm_decode_video()` 路径，300/300 无 ASan/UBSan/LSan 失败。该组不计入
上面的 5,000 例 video elementary stream 统计。

## 基线失败与最小反例

基线回放结果：

| 类别 | 用例数 | UBSan | 堆越界 | 其他 |
|---|---:|---:|---:|---:|
| 截断 | 1,250 | 2 | 0 | 0 |
| 错位 | 1,250 | 173 | 1 | 0 |
| 非法量化表 | 1,250 | 0 | 0 | 0 |
| 极端尺寸 | 1,250 | 0 | 0 | 0 |
| 合计 | **5,000** | **175** | **1** | **0** |

基线没有错误上报 API，因此上表只能按 sanitizer 类型分类；它不是“拒绝
计数”。修复实现的逐例诊断见 `malformed.json`。

### 最小反例 1：半像素插值越界读

文件：`validation/counterexamples/prepatch_heap_overflow_min.m1v`（140 字节）

基线 ASan：

```
ERROR: AddressSanitizer: heap-buffer-overflow
READ of size 1
#0 plm_video_process_macroblock
#1 plm_video_copy_macroblock
#2 plm_video_predict_macroblock
#3 plm_video_decode_macroblock
```

读取位置为 27,648 字节帧数据区末尾后 1 字节。根因是最后一列/行使用半像素
运动矢量时，插值公式会读取右邻 `s[si+1]` 或下邻 `s[si+dw]`，而旧检查只
保证 8×8/16×16 块的起点在界内，没有把插值抽头计入边界。

候选实现对该文件输出：

```
status=rejected error=invalid_picture offset=24 bit_offset=192
reason="picture contains no slice"
```

候选在更早的结构检查处拒绝，不会进入越界路径。

### 最小反例 2：DC 预测器负值左移

文件：`validation/counterexamples/prepatch_shift_ub_min.m1v`（140 字节）

基线 UBSan：

```
runtime error: left shift of negative value -5
plm_video_decode_block: self->block_data[0] <<= (3 + 5);
```

候选实现改为带范围钳制的乘法，该文件输出：

```
status=rejected error=invalid_block offset=60 bit_offset=480
reason="AC coefficient run outside block"
```

### 各类拒绝的最小回归样本

| 文件 | 大小 | error | 字节偏移 | 原因 |
|---|---:|---|---:|---|
| `min_truncated.m1v` | 0 | truncated | 0 | no sequence header found |
| `min_invalid_dimensions.m1v` | 12 | invalid_dimensions | 7 | zero width or height in sequence header |
| `min_invalid_quant_matrix.m1v` | 76 | invalid_quant_matrix | 14 | zero entry in non-intra quant matrix |
| `min_invalid_picture.m1v` | 20 | invalid_picture | 19 | invalid picture coding type |
| `min_invalid_slice.m1v` | 25 | invalid_slice | 24 | zero quantizer scale in slice |
| `min_invalid_macroblock.m1v` | 35 | invalid_macroblock | 32 | invalid macroblock type |
| `min_invalid_block.m1v` | 30 | invalid_block | 29 | invalid AC coefficient code |

所有样本的完整输出见 `validation/results/counterexamples.json`。

## 合法流逐像素回归

生成 100 条合法 MPEG-1 Program Stream：

- 12 种分辨率：16×16 到 720×576；
- 24/25/30/50 fps；
- 每流 4–12 帧；
- `-q:v` 1–20，GOP 1–15，B 帧 0–2；
- 合计 779 帧、79,316,736 个亮度像素、118,975,104 字节 YUV。

比较方式：分别用候选实现和基线提交 `c871f2b` 解码，裁剪到显示尺寸后，
对 Y、Cb、Cr 每个平面逐字节比较；同时比较帧数、宽高和完整 YUV SHA-256。

结果：**100/100 完全相同，0 个差异**。结果文件：
`validation/results/legal.json`。

### 与 ffmpeg 的额外交叉对照

另用 ffmpeg 8.0.1（`-idct simple`）对同一 100 条流做外部对照：

- 0/100 位精确；
- 82/100 输出字节数相同；
- 最大单分量绝对差 11；
- 18/100 帧数不同，均为流尾 B 帧刷新差异。

这不是本次硬化引入的回归：候选与未修改基线的 100/100 输出完全一致。
`pl_mpeg` 的整数 IDCT/反量化与 ffmpeg 的选择并不相同，因此不能把
ffmpeg 当作“位精确参考”。如果验收方要求与 ffmpeg 位精确，需要替换
IDCT 并补齐流尾 B 帧刷新，这属于超出本次“加固不改变输出”的另一项工作。

交叉对照数据：`validation/results/ffmpeg_crosscheck.json`。

## 主要修复

1. 新增 `plm_error_t` 及 `plm_video_get_error*()`，记录首个错误的字节
   偏移、位偏移和静态原因字符串。
2. VLC 读取在 EOF 或表项非法时返回显式错误，不再把缺数据当 0 继续解析；
   stuffing/escape 链增加上限。
3. 序列头校验：零尺寸、尺寸上限、像素数上限、非法帧率、自定义量化矩阵
   中的 0 项；帧缓冲分配检查 `NULL`。
4. slice/宏块检查负行号、负列号、地址上界、零量化步长、非法宏块类型和
   coded block pattern。
5. DC 预测器钳制到安全范围，使用乘法替代负值左移；AC 系数 run 越界拒绝。
6. 运动补偿改为“整块 + 半像素插值抽头”都必须位于平面内，消除帧尾
   1 字节越界读。
7. 固定内存缓冲恢复 EOF/rewind 语义；错误偏移保持源文件绝对位置。
8. PES 包长度在减去 stuffing/P-STD/PTS/DTS 前做下界检查，避免 `size_t`
   下溢导致超大 packet/realloc/memcpy。

## 复现

```bash
tests/build.sh
tests/gen_legal.py --out validation/legal --count 100
tests/verify_legal.py --dir validation/legal --build validation/build \
  --out validation/results/legal.json
tests/fuzz_campaign.py --build validation/build --out validation/malformed \
  --results validation/results/malformed.json \
  --counterexamples validation/counterexamples
tests/replay_baseline.py --build validation/build --dir validation/malformed \
  --out validation/results/baseline.json
```

或直接运行 `tests/run_validation.sh`。

## 范围与限制

- 5,000 例活动针对 MPEG-1 video elementary stream；MP2 音频解码器未纳入
  本次 5,000 例活动。
- MPEG-PS 容器路径通过 100 条合法流做逐像素回归，并用 300 个畸形容器流
  做烟雾测试；PES 长度下溢已修复，但容器畸形流不计入 5,000 例。
- `PLM_VIDEO_MAX_DIMENSION` 默认 4096，覆盖全部 12 位 MPEG-1 尺寸；
  4095×4095 会被安全接受并分配约 75.5 MiB 帧数据，不会越界。
- “逐像素一致”的验收基准是本仓库未修改的基线解码器；与 ffmpeg 的差异
  已在上一节单独量化。
