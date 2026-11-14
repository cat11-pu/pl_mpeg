# 参考帧丢失：复现、修复与恢复测试

当播放的 MPEG-PS 码流丢失了前面的参考帧（I 帧或 P 帧所在的 PES 包），
后续 P/B 帧仍被拿去做运动补偿时，会引用无效/未初始化的参考平面，轻则输出
花屏帧，重则在运动补偿里越界读取导致进程崩溃。本目录提供：

1. 可重复生成的“丢参考帧”复现码流；
2. 解码器侧的修复（发现参考帧缺失立即返回错误码并跳过该帧）；
3. 基于 AddressSanitizer/UBSan 的崩溃与恢复回归测试。

## 运行

```sh
cd test
./recover_test.sh
```

脚本会用 ffmpeg 生成基准码流，构造各种丢帧/损坏码流，编译 ASan+UBSan
版本的解码 harness 并断言全部通过（结尾打印 `ALL TESTS PASSED`）。
依赖：`gcc`（支持 sanitizer）、`ffmpeg`、`python3`。

## 复现码流

所有码流由 `base.mpg`（176x144、25fps、GOP=12、无 B 帧，共 100 帧）派生：

| 文件 | 构造方式 | 预期行为 |
| --- | --- | --- |
| `muxed_full.mpg` | 完整 ES 重新封装 | 100 帧全部输出，0 跳过 |
| `loss_i.mpg` | 删除首个 I 帧（含其全部 slice） | 11 个无参考 P 帧返回 `PLM_ERROR_MISSING_REFERENCE` 跳过，下一个 I 帧恢复，共输出 88 帧 |
| `loss_10.mpg` | 连续删除前 10 帧（I+9P） | 剩余 2 个孤立 P 帧跳过，下一个关键帧恢复，输出 88 帧 |
| `loss_mid10.mpg` | GOP 中段连续删除 10 个 P 帧 | 关键帧处重建参考，输出 90 帧，0 花屏 |
| `bf_loss.mpg` | 带 B 帧码流删除首个 I 帧 | 双向参考不完整的 B/P 帧全部跳过，恢复后 0 花屏 |
| `trunc.mpg` | 截断的码流 | 返回 `PLM_ERROR_CORRUPT`，不崩溃 |

辅助工具：

- `mpeg_es.py`：按视频起始码（`00 00 01 xx`）切分/重组裸 ES，识别帧类型。
- `ps_mux.py`：最小 MPEG-1 PS 复用器。ffmpeg 拒绝以非 I 帧起始的 PS，因此
  损坏码流用它封装，保证丢帧后的流仍能被 demux。
- `build_streams.py` / `build_bframe_loss.py`：生成上面的复现码流。

## 新增错误码与 API（`pl_mpeg.h`）

```c
#define PLM_OK 0
#define PLM_ERROR_TRY_AGAIN 1          // 缓冲数据不足，需要继续喂数据
#define PLM_ERROR_MISSING_REFERENCE 2  // 参考帧缺失，该帧已跳过
#define PLM_ERROR_CORRUPT 3            // 结构/位流错误，该帧已跳过
#define PLM_ERROR_END 4                // 码流结束

plm_frame_t *plm_decode_video_ex(plm_t *self, int *error);
int plm_get_last_picture_type(plm_t *self);
int plm_get_skipped_frame_count(plm_t *self);
const char *plm_error_name(int code);
```

`plm_decode_video()` 旧 API 保持兼容：内部遇到可恢复错误会继续解码下一帧，
只有真正结束/缺数据时才返回 `NULL`，因此现有的
`while (plm_decode_video(plm))` 调用方式无需改动。

## 修复要点

- 新增 `reference_valid`：首个完整 I 帧解码成功前为假；P/B 帧在此状态下
  直接跳过，绝不对未初始化参考平面做运动补偿。I 帧将其置真，P 帧只能维持、
  不能恢复该状态。
- 新增 `skipped_since_intra`：丢帧后到“恢复 I 帧 + 其后首个 P 帧”完成前
  持续为真，用于门控 B 帧。B 帧需要前向与后向两个参考，重排序后紧跟恢复
  I 帧的 B 帧后向参考仍指向被丢链，必须跳过，直到双向参考重新完整。
- `plm_video_decode_picture()` 拆出 `plm_video_decode_picture_header()`，
  先读帧类型再决定解码还是 `plm_video_skip_picture()`（仅扫描起始码、不触碰
  任何帧平面），保证跳帧不越界、不产出半帧。
- 宏块/slice/block 层的所有结构性错误（宏块地址越界、系数越界、运动补偿
  目标矩形越界等）统一置 `picture_error`，整帧丢弃并令参考链失效，等待
  下一个 I 帧。
- 重写 `plm_video_process_macroblock()` 的边界检查：旧的单一 `max_address`
  判断无法覆盖负向运动矢量和半像素插值多读的一行/一列；新逻辑按源、目的
  实际访问的矩形（含 `odd_h/odd_v` 增量）对平面宽高做完整校验。
- 修复若干未定义行为：负值左移改为有符号乘法（DC 系数、运动矢量、DCT
  level），UBSan 下不再报 `left shift of negative value`。

## 恢复保证

- 不崩：1000 次随机损坏（截断、`0xFF`/`0x00` 覆写、帧内字节翻转）在
  ASan+UBSan 下 0 崩溃、0 sanitizer 报告（见 `fuzz_run.py`）。
- 不越界：所有参考平面访问都经过矩形边界校验。
- 不花屏：`verify_pixels.py` 断言丢帧流恢复后的每一帧与干净参考解码逐字节
  一致（FNV 哈希）；`verify_bframes.py` 断言 B 帧流不存在干净参考里没有的
  输出帧。
- 可恢复：连续丢十帧后，下一个关键帧即重建参考链并恢复正常输出。
