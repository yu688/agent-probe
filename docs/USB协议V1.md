# USB协议 V1

用于常规采集固件。单个厂商接口0，EP0负责标准请求和MS OS描述符，Bulk OUT=0x01、Bulk IN=0x81。所有整数均为小端。USB读写边界不是业务帧边界，双方必须维护流式解析器。

## 帧头

| 偏移 | 字节数 | 字段 |
| --- | --- | --- |
| 0 | 4 | ASCII `RPD1` |
| 4 | 1 | 版本1 |
| 5 | 1 | 消息类型 |
| 6 | 1 | 通道；UART A=0、UART B=1 |
| 7 | 1 | flags；请求必须为0 |
| 8 | 4 | request_id；请求与应答匹配，日志事件为0 |
| 12 | 4 | sequence；设备发送帧序号，重启后归零 |
| 16 | 2 | payload长度，0~1024 |
| 18 | 2 | 保留，必须为0 |
| 20 | 4 | IEEE CRC32 |
| 24 | 可变 | payload |

CRC覆盖头部0~19字节及payload，跳过头部CRC字段。算法为IEEE CRC32，初值0xFFFFFFFF，最终异或0xFFFFFFFF，与Python `zlib.crc32(header[:20]+payload)` 一致。坏帧逐字节寻找下一帧；设备端不完整命令超过500毫秒未收到新字节时丢弃。上位机一次只发一个待应答请求，不对有副作用命令自动重试。

应答类型为请求类型按位OR 0x80。应答payload前4字节始终是状态：0成功、1参数错误、2忙、3未支持、4结果未就绪。下表中的应答数据均指状态码之后的内容。

## 命令

| 类型 | 名称 | 请求payload | 成功应答数据 |
| --- | --- | --- | --- |
| 0x01 | INFO | 空 | 47字节能力与版本 |
| 0x02 | STATUS | 空 | 64字节设备及UART状态 |
| 0x03 | UART_CONFIG | enabled:u8，保留0:3字节，baud:u32 | 空；使用头部channel |
| 0x04 | LOG_STREAM | enabled:u8，0或1 | 空 |
| 0x10 | CAPTURE_START | rate:u32，samples:u32 | 44字节采集状态 |
| 0x11 | CAPTURE_STATUS | 空 | 44字节采集状态 |
| 0x12 | CAPTURE_READ | capture_id:u32，offset:u32，count:u32 | capture_id:u32，offset:u32，原始数据 |
| 0x13 | CAPTURE_CANCEL | 空 | 空 |

默认GP5 / UART1 RX为通道0。通道1对应GP13 / UART0 RX，仅显式启用第二路的构建允许配置；默认构建拒绝该通道配置。UART只支持8N1、1200~1000000 baud，返回STATUS中的实际波特率。

LOG_STREAM初始关闭，UART接收一直运行；打开后推送两个通道中非空的缓冲。无TX发送、目标复位、远程烧录或任意内存访问命令。

CAPTURE_START只支持四路同时、定长即时采集。rate范围2000~25000000，samples范围8~262144且必须整除8；运行中再次启动返回忙。下一次成功启动覆盖上一记录，capture_id递增且跳过0。读取必须指定当前capture_id，count范围1~1012，offset/count必须处于结果范围。只有DONE状态可读；分块读取时主机需独占采集控制权，防止其他客户端覆盖记录。

## INFO 数据

依次为6个u32：capabilities、max_samples、max_rate、sys_hz、uart_a_rx_pin、uart_b_rx_pin；随后17字节以零结尾的板卡序列号，以及6字节以零结尾的版本字符串。

capabilities的bit0=UART监听、bit1=四路即时采集、bit2=第二UART可配置。未启用的DAP、ESP32、边沿触发和TX能力不对外宣称。

## STATUS 数据

uptime_us:u64、bad_frames:u32、log_stream:u32；随后每通道24字节，包含6个u32：enabled、实际baud、received、dropped、errors、buffered。通道顺序0、1。bad_frames还包括接收超时和极端损坏输入导致的拒绝计数，用于诊断，不代表精确CRC错误次数。

## UART_DATA 事件

类型0x40，channel标识来源，payload为：

- dequeue_us:u64：主循环出队时间。
- received:u32、dropped:u32、errors:u32：累计计数。
- timestamp_kind:u32：固定1，明确表示出队时间。
- 后续1~256字节：串口原始字节。

flags bit0表示累计发生过丢弃或UART错误，不表示当前块必然损坏；主机通过累计计数差和会话状态判定缺口。日志推送每5毫秒最多准备一帧，两通道轮询。USB断连区间视为潜在丢失，不提供端到端日志确认或重放保证。

## 采集状态数据

顺序为7个u32：capture_id、state、requested_rate、actual_rate、samples、bytes、flags；再接start_us:u64、end_us:u64，总计44字节。

state：0空闲、1运行、2完成、3取消、4错误。flags bit0=PIO RX FIFO发生过stall，bit1=任务超时。start/end是CPU时间标记；actual_rate是硬件分频后的平均采样率，不是首沿实测值。

每个采样点4 bit：bit0=GP16，bit1=GP17，bit2=GP18，bit3=GP19。一个字节先取低四位、再取高四位；每8点形成一个32位DMA字。

## Windows 接入

接口GUID为 `{7C9B140E-5C85-4783-8B8A-F0BC97913546}`。BOS只包含Microsoft OS 2.0平台能力，vendor request=0x20，wIndex=7。162字节描述符集包含设备级WINUSB Compatible ID与DeviceInterfaceGUIDs多字符串属性。无WebUSB、CDC或DAP接口。

主机参考实现见 `host/winusb_transport.py`；其同步调用加200毫秒管道超时用于初版验证，后续高吞吐服务应采用并行排队的异步读取。
