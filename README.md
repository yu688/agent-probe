# RP2350 Agent 调试工具

这是基于正点原子 RP2350A 核心板的 Agent 调试工具。RP2350 负责采集和 USB，ESP32-S3 是网络与后续显示的协处理器。RP2350 常规固件通过单个厂商自定义 USB 接口接入 Windows WinUSB。

## 工程目录

| 路径 | 内容 |
| --- | --- |
| `firmware/rp2350/` | 独立 Pico SDK 工程，包含板级定义、PIO 程序和采集固件 |
| `firmware/esp32/` | 独立 ESP-IDF 工程，固定目标为 ESP32-S3 |
| `host/` | Python 协议和 Windows WinUSB 客户端 |
| `docs/`、`scripts/`、`tests/` | 项目文档、Windows 构建入口和回归测试 |
| `build/rp2350/`、`build/esp32/` | 两套固件的本机构建输出 |

## 当前功能

| 模块 | 实现范围 |
| --- | --- |
| USB | Bulk OUT 0x01 / IN 0x81，64字节包，Microsoft OS 2.0自动绑定描述符 |
| 串口 | **通道0：GP5 / UART1 RX，100000 baud、8N1**，上电开始接收；所有目标TX引脚保持未配置 |
| 第二串口 | 默认关闭；只有显式使用 `-EnableUartB` 构建才允许启用GP13 / UART0 RX |
| 日志缓存 | 每路16 KiB中断环形缓冲，队满丢弃新字节并累计报告计数 |
| 逻辑采集 | GP16~19四路，PIO + DMA，定长即时采集；每字节打包两个采样点 |
| 采集容量 | 最多262144点，即128 KiB；单次点数必须是8的倍数 |
| 请求采样率 | 2000~25000000 S/s；返回实际平均采样率，范围不是实板性能承诺 |
| 电脑端 | 标准库Python CLI，直接调用Windows WinUSB，不依赖PyUSB或libusb |
| 保留引脚 | GP20~23留给ESP32 SPI，固件不初始化或驱动 |

ESP32-S3 工程启动后输出一条启动日志。片间 SPI 协议、Wi-Fi 和屏幕属于后续实现。逻辑采集目前支持定长即时采集，Windows 客户端用于硬件通信验证。

## 开发环境与构建

### RP2350

构建脚本默认使用用户现有的 `C:\Users\Administrator\.pico-sdk`：

- Pico SDK 2.3.1。
- Arm GNU Toolchain 15.2.Rel1。
- CMake v4.3.4、Ninja v1.13.2。
- 本地预编译 pioasm 和 picotool 2.3.1；不需要下载或编译这些主机工具。

在工程根目录运行：

```powershell
.\scripts\build.ps1
```

产物 `agent_probe.uf2`、`agent_probe.elf` 和 `agent_probe.bin` 位于 `build/rp2350/`。可用 `-PicoRoot` 指定其他环境目录。跨目录构建时用绝对脚本路径。

macOS 也可使用工程内的 `release` 预设。从仓库根目录运行以下命令，先将 Pico SDK 2.3.1（含子模块）、Arm GNU Toolchain、Ninja 和 CMake 3.21 或更新版本准备好，并将示例 SDK 路径替换为本机路径。将交叉编译器的 `bin` 加入 `PATH`，或在配置时传入 `-DPICO_TOOLCHAIN_PATH=工具链目录`。

```sh
cd firmware/rp2350
cmake --preset release -DPICO_SDK_PATH="/path/to/pico-sdk"
cmake --build --preset release
```

如果已有预编译的 pioasm 和 picotool，也可在配置时传入 `-Dpioasm_DIR=目录` 和 `-Dpicotool_DIR=目录`。

板级配置位于 `firmware/rp2350/boards/agent_rp2350a.h`，使用4 MB Flash与保守的通用03h启动读取，未继承Pico 2的GP23电源控制、GP25 LED等板级假设。固件在SCRATCH_Y保留4 KiB主栈，大块协议工作区、采样缓冲和日志队列静态分配。

第二路明确不是默认能力。如后续确认GP13电路允许复用，可执行：

```powershell
.\scripts\build.ps1 -EnableUartB
```

该模式要求核对TF卡接口负载并取出TF卡。再次执行不带开关的构建命令会恢复单路默认配置。UART引脚在CMake缓存项 `AGENT_UART_A_RX_PIN`、`AGENT_UART_B_RX_PIN` 中配置，修改时需同时确认UART复用关系和引脚冲突。

### ESP32-S3

使用 ESP-IDF 6.1.0。Windows 可从桌面快捷方式 `IDF_v6.1_Powershell` 进入已激活的环境，本机 SDK 位于 `D:\esp\v6.1\esp-idf`。macOS 激活本机 ESP-IDF 6.1 环境后，执行相同命令。两端均从仓库根目录运行。 [Espressif 环境激活说明](https://docs.espressif.com/projects/idf-im-ui/en/latest/after_installing.html)

```sh
idf.py --version
idf.py -C firmware/esp32 -B build/esp32 build
```

应用镜像 `agent_esp32_coprocessor.bin`、引导程序、分区表和烧录参数均在 `build/esp32/`。启动验证使用 ESP-IDF 的默认 2 MB Flash 配置，PSRAM 留待选定模组后配置。两台机器通过源码同步，分别生成 CMake 缓存和本地 ESP-IDF 配置。上位机的设备通信使用 Windows WinUSB。

## 上板连接与使用

1. 使用核心板的**原生USB接口**，CH343对应的USB UART接口不是本固件的WinUSB连接。
2. 通过BOOTSEL方式进入芯片USB启动模式，将UF2复制到对应盘符。当前工程不自动发现盘符或烧录设备。
3. 将目标TX接GP5，连接共地。目标接口电压须先确认；核心板IO不得直接接RS232电平。RX监听不要求连接目标RX。
4. 逻辑输入接GP16~19；信号需符合核心板电平，输入保护和电平转换属于底板电路。
5. 核对核心板芯片stepping。早期RP2350输入漏电勘误需要按芯片文档及底板设计处理。

Windows 10/11的免驱目标由固件MS OS 2.0描述符实现。默认实验标识是 **VID 0xCAFE / PID 0x40F0**，不代表项目拥有该VID；用于本地原型，正式分发前更换合法分配的标识。可通过CMake `AGENT_USB_VID`、`AGENT_USB_PID` 覆盖。

设备接口GUID：`{7C9B140E-5C85-4783-8B8A-F0BC97913546}`。默认不出现COM口。Windows按GUID枚举，USB序列号使用板载Flash唯一ID。

Python需要3.11或更高版本，全部使用标准库：

```powershell
python .\host\probe_cli.py list
python .\host\probe_cli.py info
python .\host\probe_cli.py status
python .\host\probe_cli.py log --seconds 30 --output uart.bin
python .\host\probe_cli.py capture --rate 1000000 --samples 65536 --output capture.bin
```

多台设备时用 `--device` 选择序号，例如 `python .\host\probe_cli.py --device 1 info`。

日志保存为原始字节文件，并生成 `.jsonl` 块元数据和 `.meta.json` 会话信息。采样保存为打包字节文件与 `.json` 元数据，包含采集编号、实际采样率、GPIO定义和SHA256。每个采样点的bit0~3对应GP16~19；同一字节低四位为较早样本，高四位为下一样本。

## 数据完整性与时间含义

- 固件在USB枚举前已启用GP5接收。16 KiB只能保存100000 baud下约1.64秒的满速数据，无法保证电脑尚未启动时完整保存无限启动日志；`dropped`报告缓冲溢出。
- `received`统计从UART读取的字节，`errors`统计带硬件错误标志的读取次数；硬件overrun意味着可能丢了多个字节，不能用它精确恢复丢失数量。计数器为32位并会回绕。
- 日志时间戳是**数据从环形缓冲出队时的设备时间**，不是每个字节或首字节的精确到达时间。积压数据的时间戳尤其不能用于推断精确线间时序。
- 采集时间戳是CPU启动、完成标记；每点间隔按PIO分频推导。使用小数分频时，瞬时间隔可能有系统时钟粒度的抖动。
- PIO FIFO中途停顿会将采集标记为错误；超时或取消的数据不提供为完整采集结果。
- USB断连取消运行中的采集并关闭日志推送，主机需重新开始会话。已经出队或进入USB发送缓冲的数据可能未被主机接收，断连区间必须视为潜在缺口。
- 改变UART配置会中断接收，现有缓冲仍保留；跨配置边界不是连续无缝记录。
- 开启日志推送后，命令与事件复用同一字节流，控制应答优先排队，完整帧不会相互交错。

协议字段与命令见 [USB协议](docs/USB协议V1.md)。

## 已执行的验证

- 使用上述本地工具链完成Release交叉编译，应用源码启用 `-Wall -Wextra -Werror`。
- 运行Python协议测试，覆盖拆包、粘包、CRC损坏、非法长度、应答与日志混合。
- 从实际编译后的ELF对象读取USB描述符，检查WinUSB标识、UTF-16 GUID、BOS长度、接口和端点。
- Windows设备枚举脚本可运行；当前未发现对应WinUSB设备。

执行测试：

```powershell
python -m unittest discover -s tests -v
```

这些验证不替代实板测试。尚未烧录验证WinUSB首次绑定、UART接收、PIO样本正确性、采样率、A2/A4输入表现或长时间并发完整性。建议上板后先执行 `info/status`，再用已知字节序列和独立GPIO码型源逐项验证。
