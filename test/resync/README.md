# 字节级重同步

当网络流在半帧处断开时，不再丢弃整个解码器。重同步从指定字节偏移向后
扫描，丢掉当前半帧和所有不能独立恢复的 P/B 帧，停在下一个“完整且带
slice 的 I 帧”的 picture start code 上，然后继续正常解码。

## 接口

```c
#define PLM_RESYNC_OK               0
#define PLM_RESYNC_NEED_MORE_DATA   1
#define PLM_RESYNC_END_OF_STREAM    2
#define PLM_RESYNC_BAD_OFFSET       3
#define PLM_RESYNC_CONTINUE         ((size_t)-1)

typedef struct {
    int status;
    size_t source_offset;
    size_t frame_offset;
    double time;
    int picture_type;
    int pictures_discarded;
    int frames_lost;
    size_t bytes_discarded;
} plm_resync_info_t;

int plm_video_resync(plm_video_t *self, size_t offset,
                     plm_resync_info_t *info);
int plm_resync(plm_t *self, size_t offset, plm_resync_info_t *info);
```

- `plm_video_resync()`：裸 MPEG-1 video elementary stream，偏移相对当前
  `plm_buffer` 内容。固定内存/文件可直接给绝对字节偏移。
- `plm_resync()`：MPEG-PS 高层接口；跳转后重置 PES 状态，并让视频解码器
  在解复用出的 ES 中重同步。可 seek 的源使用绝对偏移；动态 ring buffer
  的偏移相对当前未读数据。
- 返回 `PLM_RESYNC_NEED_MORE_DATA` 时，追加网络数据后传
  `PLM_RESYNC_CONTINUE` 继续同一扫描；不会回退或重复计数。
- 返回 `PLM_RESYNC_OK` 后，照常调用 `plm_video_decode()` /
  `plm_decode_video()`。重同步本身不输出半帧。

典型用法：

```c
plm_resync_info_t info;
int st = plm_resync(plm, offset, &info);
while (st == PLM_RESYNC_NEED_MORE_DATA) {
    feed_more_network_bytes(plm);
    st = plm_resync(plm, PLM_RESYNC_CONTINUE, &info);
}
if (st == PLM_RESYNC_OK) {
    plm_frame_t *frame;
    while ((frame = plm_decode_video(plm))) {
        render(frame);
    }
}
```

## 边界处理

- 半帧/截断 slice：扫描到下一个 picture 边界前返回 `NEED_MORE_DATA`；
  数据到齐后从同一偏移继续，不产生半帧。
- 精确落在 picture start code：`frames_lost = 0`；落在帧内则先计 1 个
  半帧，再计被跳过的完整 picture。
- 只有 sequence header、没有 I 帧：继续扫描；流结束返回
  `END_OF_STREAM`。没有可用 sequence header 时不会用旧尺寸猜测解码。
- 只有 I 帧但没有 slice：不算可解帧，继续向后。
- 重复 sequence header：重新读取尺寸、帧率和量化矩阵；重同步可跨 GOP。
- 文件/内存偏移等于文件长度：返回 `END_OF_STREAM`；大于长度返回
  `BAD_OFFSET`。
- MPEG-PS 尾部 `0xFF` padding、截断 PES：扫描到源结束返回
  `END_OF_STREAM`，不会把 padding 当帧。
- 开放 GOP：恢复 I 帧后紧跟的 B 帧可能引用丢失的旧 GOP，重同步会跳过 B
  直到首个 P 帧，避免输出与从头解码不同的帧。
- MPEG-PS 时间戳：用恢复帧所在 PES 的 PTS，加上该 PES 内到目标 I 帧之间
  的 picture 数，得到与线性解码一致的帧时间。
- 裸 ES 从任意偏移冷启动时没有绝对 PTS；`frames_lost` 仍用于推进计帧
  时间，但绝对时间只能由调用方通过 `plm_video_set_time()` 锚定。

## 延迟说明

`frames_lost` 是实际丢失的显示帧数（半帧 + 跳过的 picture）。在
I 帧间隔不超过 2 帧的流上，所有测试切点均满足 `frames_lost <= 2`。
如果 GOP 更长，为了保证“恢复后逐帧与从头解码一致”，解码器不会用损坏的
参考链提前输出 P/B 帧，而是等到下一个 I 帧；此时 `frames_lost` 会如实
报告等待的帧数。这个下界由 MPEG-1 的参考关系决定，不能在不牺牲像素一致
性的前提下突破。

## 一致性数据

`run.sh` 用 ffmpeg 生成 176x144、25fps、2 秒素材：

- GOP=2、无 B 流：裸 ES 与 MPEG-PS；
- GOP=12、B 帧流：验证开放 GOP 的 B 帧门控；
- GOP=1、全 I 流：验证最坏情况下 1 帧内恢复。

每帧对 Y/Cr/Cb 三个平面做 FNV-1a 校验；切流后从恢复 I 帧解到 EOF，要求
恢复序列是线性解码哈希序列的严格后缀。当前实测（ASan+UBSan）：

| 场景 | 切点 | 成功重同步 | 哈希差异 | 最大丢帧 | 最大丢弃字节 |
|---|---:|---:|---:|---:|---:|
| 裸 ES GOP=2 | 180 | 180 | 0 | 2 | 6847 |
| MPEG-PS GOP=2 | 17 | 17 | 0 | 2 | - |
| PTS 时间一致性 | 17 | 17 | 0 | 2 | - |
| B 帧 GOP=12 | 30 | 30 | 0 | - | - |
| 分块流式追加 | 12 | 12 | 0 | 1 | 496 |
| 裸 ES GOP=1 | 180 | 180 | 0 | 1 | 5730 |

边界用例（越界、EOF、尾部 padding、中途无 sequence header、截断后追加）
全部通过；AddressSanitizer、UndefinedBehaviorSanitizer、LeakSanitizer
均无报告。

复现：

```sh
./test/resync/run.sh
```
