# RP2350 Agent 调试工具

这是基于正点原子 RP2350A 核心板的 Agent 调试工具。RP2350 负责采集和 USB，ESP32-S3 是网络与后续显示的协处理器。RP2350 常规固件通过单个厂商自定义 USB 接口接入 Windows WinUSB 或 macOS libusb。

## 工程目录

| 路径 | 内容 |
| --- | --- |
| `firmware/rp2350/` | 独立 Pico SDK 工程，包含板级定义、PIO 程序和采集固件 |
| `firmware/esp32/` | 独立 ESP-IDF 工程，固定目标为 ESP32-S3 |
| `host/` | Python 协议、共享设备会话、Windows WinUSB / macOS libusb 客户端和 PyQt6 界面 |
| `docs/`、`scripts/`、`tests/` | 项目文档、Windows / macOS 构建入口和回归测试 |
| `build/rp2350/`、`build/esp32/` | 两套固件的本机构建输出 |
| `build/macos/` | macOS 专用构建输出、ESP-IDF 依赖和本地配置（不提交） |

## 当前功能

| 模块 | 实现范围 |
| --- | --- |
| USB | Bulk OUT 0x01 / IN 0x81，64字节包，Microsoft OS 2.0自动绑定描述符 |
| 串口 | **通道0：GP5 / UART1 RX，100000 baud、8N1**，上电开始接收；所有目标TX引脚保持未配置 |
| 第二串口 | 默认关闭；只有显式使用 `-EnableUartB` 构建才允许启用GP13 / UART0 RX |
| 日志缓存 | 每路 16 KiB LwRB 接收缓冲，队满丢弃新字节并累计报告计数 |
| USB 缓冲 | WinUSB 设备端 RX 2096 字节、TX 4192 字节，空间不足时暂停消费或读取 |
| 逻辑采集 | GP16~19四路，PIO + DMA，定长即时采集；每字节打包两个采样点 |
| 采集容量 | 最多262144点，即128 KiB；单次点数必须是8的倍数 |
| 请求采样率 | 2000~25000000 S/s；返回实际平均采样率，范围不是实板性能承诺 |
| 电脑端 | Windows 使用标准库 WinUSB；macOS 使用 PyUSB 和虚拟环境内的 libusb |
| ESP32 SPI | RX 和 TX 各 2 KiB，已提供收发缓冲接口。GP20~23 保留给片间 SPI |

ESP32-S3 工程启动后输出一条启动日志。RP2350 的 SPI 缓冲接口位于 `firmware/rp2350/include/esp_spi_buffers.h`，返回实际入队或出队的字节数。片间协议、引脚初始化、Wi-Fi 和屏幕属于后续实现。逻辑采集支持定长即时采集，Windows / macOS 上位机提供四路波形查看和数据导出。

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

macOS 推荐使用下方的独立构建入口，将缓存与 Windows 分开。也可手动指定独立输出目录，先准备 Pico SDK 2.3.1（含子模块）、Arm GNU Toolchain、Ninja 和 CMake 3.21 或更新版本。将交叉编译器的 `bin` 加入 `PATH`，或在配置时传入 `-DPICO_TOOLCHAIN_PATH=工具链目录`。

```sh
cmake -S firmware/rp2350 -B build/macos/rp2350 -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DPICO_SDK_PATH="/path/to/pico-sdk"
cmake --build build/macos/rp2350 --parallel
```

如果已有预编译的 pioasm 和 picotool，也可在配置时传入 `-Dpioasm_DIR=目录` 和 `-Dpicotool_DIR=目录`。

板级配置位于 `firmware/rp2350/boards/agent_rp2350a.h`，使用4 MB Flash与保守的通用03h启动读取，未继承Pico 2的GP23电源控制、GP25 LED等板级假设。固件在SCRATCH_Y保留4 KiB主栈，大块协议工作区、采样缓冲和日志队列静态分配。

第二路明确不是默认能力。如后续确认GP13电路允许复用，可执行：

```powershell
.\scripts\build.ps1 -EnableUartB
```

该模式要求核对TF卡接口负载并取出TF卡。再次执行不带开关的构建命令会恢复单路默认配置。UART引脚在CMake缓存项 `AGENT_UART_A_RX_PIN`、`AGENT_UART_B_RX_PIN` 中配置，修改时需同时确认UART复用关系和引脚冲突。

### ESP32-S3

使用 ESP-IDF 6.1.0。Windows 可从桌面快捷方式 `IDF_v6.1_Powershell` 进入已激活的环境，本机 SDK 位于 `D:\esp\v6.1\esp-idf`。以下命令从仓库根目录运行；macOS 使用下一节的独立构建入口。 [Espressif 环境激活说明](https://docs.espressif.com/projects/idf-im-ui/en/latest/after_installing.html)

```sh
idf.py --version
idf.py -C firmware/esp32 -B build/esp32 build
```

应用镜像 `agent_esp32_coprocessor.bin`、引导程序、分区表和烧录参数均在 `build/esp32/`。启动验证使用 ESP-IDF 的默认 2 MB Flash 配置，PSRAM 留待选定模组后配置。两台机器通过源码同步，分别生成 CMake 缓存和本地 ESP-IDF 配置。

### macOS 独立构建

从仓库根目录执行，CMake 需在 `PATH` 中：

```sh
bash scripts/build-macos.sh rp2350
```

默认复用 `$HOME/.pico-sdk` 中的 SDK 2.3.1、Arm GNU Toolchain 15_2_Rel1、Ninja v1.13.2 和预编译 pioasm / picotool 2.3.1。可通过 `PICO_ROOT`、`SDK_VERSION`、`PICO_SDK_PATH`、`PICO_TOOLCHAIN_PATH`、`NINJA_BIN`、`CMAKE_BIN`、`PIOASM_DIR`、`PICOTOOL_DIR` 环境变量覆盖对应路径。启用第二路 UART 使用 `bash scripts/build-macos.sh rp2350 --enable-uart-b`；不带开关会恢复默认单路。

ESP32-S3 首次构建前，安装项目内隔离的 ESP-IDF v6.1（版本号为 6.1.0）及工具，只需执行一次。以下命令不会覆盖 `$HOME/.espressif` 中已有的工具和 Python 环境：

```sh
git clone --branch v6.1 --depth 1 --shallow-submodules --recursive \
  https://github.com/espressif/esp-idf.git build/macos/deps/esp-idf
IDF_TOOLS_PATH="$PWD/build/macos/deps/espressif" \
  bash build/macos/deps/esp-idf/install.sh esp32s3
bash scripts/build-macos.sh esp32
```

准备好两套依赖后，执行 `bash scripts/build-macos.sh all` 可编译两套固件。ESP-IDF 在构建脚本的子 shell 中激活；也可同时指定 `IDF_PATH` 与 `IDF_TOOLS_PATH` 使用其他已安装的 v6.1 环境。

macOS 产物分别位于 `build/macos/rp2350/agent_probe.{uf2,elf,bin}` 与 `build/macos/esp32/agent_esp32_coprocessor.bin`；ESP32 的 `sdkconfig` 也位于 `build/macos/esp32/`。整个 `build/` 已被 Git 忽略。Windows 的 `scripts/build.ps1`、`release` 预设及原有输出路径保持不变，不需要同步任何本机缓存或 SDK。

验证 macOS 实际编译的 USB 描述符及协议：

```sh
AGENT_RP2350_BUILD_DIR=build/macos/rp2350 python3 -m unittest discover -s tests -v
```

此入口完成固件交叉编译；macOS 上位机使用方法见下方。

## 上板连接与使用

1. 使用核心板的**原生USB接口**，CH343对应的USB UART接口不是本固件的WinUSB连接。
2. 通过BOOTSEL方式进入芯片USB启动模式，将UF2复制到对应盘符。当前工程不自动发现盘符或烧录设备。
3. 将目标TX接GP5，连接共地。目标接口电压须先确认；核心板IO不得直接接RS232电平。RX监听不要求连接目标RX。
4. 逻辑输入接GP16~19；信号需符合核心板电平，输入保护和电平转换属于底板电路。
5. 核对核心板芯片stepping。早期RP2350输入漏电勘误需要按芯片文档及底板设计处理。

Windows 10/11的免驱目标由固件MS OS 2.0描述符实现。默认实验标识是 **VID 0xCAFE / PID 0x40F0**，不代表项目拥有该VID；用于本地原型，正式分发前更换合法分配的标识。可通过CMake `AGENT_USB_VID`、`AGENT_USB_PID` 覆盖。

设备接口GUID：`{7C9B140E-5C85-4783-8B8A-F0BC97913546}`。默认不出现COM口。Windows按GUID枚举，USB序列号使用板载Flash唯一ID。

### PyQt6 上位机

从项目根目录创建项目虚拟环境并安装 GUI 依赖。环境建好后，后续直接执行最后一行。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r host/requirements-gui.txt
.\.venv\Scripts\python.exe .\host\probe_gui.py
```

扫描并连接设备后可查看序列号、固件版本、系统时钟、能力位和 UART RX 引脚。串口测试针对通道 0（默认 GP5），支持设置波特率、开始和停止接收、UTF-8 文本或十六进制显示，并显示接收、丢弃、错误和积压计数。实时窗口仅保留最近的显示内容，完整原始日志可通过下方 CLI 的 `log` 命令保存。

### macOS USB 与上位机

常规固件枚举为 `RP2350 Agent Probe`（默认 `CAFE:40F0`），提供 `Agent Data` 厂商自定义 Bulk 接口，不会生成 `/dev/cu.*` 串口或磁盘。可以通过 `ioreg -p IOUSB -w 0` 查看系统是否已枚举设备。

从项目根目录安装项目虚拟环境依赖并运行：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r host/requirements-gui.txt
.venv/bin/python host/probe_cli.py list
.venv/bin/python host/probe_cli.py info
.venv/bin/python host/probe_cli.py status
.venv/bin/python host/probe_gui.py
```

仅使用 CLI 时，可改装 `host/requirements-macos.txt`。libusb 由 `libusb-package` 放在虚拟环境内，不需安装系统驱动。Windows 仍使用原有 WinUSB，依赖文件中的平台条件会跳过 macOS USB 包。

macOS 按 VID/PID 枚举，通过 USB 总线与设备地址生成会话路径；重新插拔后请重新扫描。自定义固件 VID/PID 时，可设置 `AGENT_USB_VID` / `AGENT_USB_PID` 环境变量（例如 `0xCAFE` / `0x40F0`）。GUI 与 CLI 使用同一设备时，请先断开另一个会话。

### CLI 与 Agent 调用

CLI 使用 Python 3.11 或更高版本；Windows 仅需标准库，macOS 需安装上述 USB 依赖。Agent 可直接执行以下命令，解析标准输出中的 JSON，也可在项目根目录导入 `host.probe_client.Client`，传入已打开的 `host.usb_transport.USB`。同一设备请在 GUI 断开后交给 CLI 或 Agent，以保证同一时刻只有一个会话消费 USB 回包。

```powershell
python .\host\probe_cli.py list
python .\host\probe_cli.py info
python .\host\probe_cli.py status
python .\host\probe_cli.py uart-config --baud 100000
python .\host\probe_cli.py log --seconds 30 --output uart.bin
python .\host\probe_cli.py capture --rate 1000000 --samples 65536 --output capture.bin
```

多台设备时用 `--device` 选择序号，例如 `python .\host\probe_cli.py --device 1 info`。

日志保存为原始字节文件，并生成 `.jsonl` 块元数据和 `.meta.json` 会话信息。采样保存为打包字节文件与 `.json` 元数据，包含采集编号、实际采样率、GPIO定义和SHA256。每个采样点的bit0~3对应GP16~19；同一字节低四位为较早样本，高四位为下一样本。

### 逻辑 IO 采集与波形查看

使用已刷写的 0.1.0 固件即可，无需为此功能重新刷写。连接目标信号到 GP16、GP17、GP18、GP19，并连接共地；这些引脚仅作输入，电压必须符合核心板 IO 规格。

1. 在 GUI 连接设备，切换到“逻辑 IO 采集”页。
2. 设置采样率（2000～25000000 S/s）和点数（8～262144，必须为 8 的倍数），点击“开始采集”。四路同时采样，采样窗口约为点数除以实际采样率。
3. 采集结束后自动分块读取数据并显示四路波形，顶部显示实际采样率。可在采集中或读数期间取消，不发布部分数据；取消后可以重新采集。
4. 调整“起点”和“显示点数”平移、放大波形。概览采用逐像素高低包络保留窄脉冲，放大后可逐点查看；时间轴零点为首样本，使用设备返回的实际平均采样率。
5. “保存 BIN + 元数据”导出打包原始字节；“导出 CSV”生成 `sample,time_s,GP16,GP17,GP18,GP19` 六列。两种格式均生成同名追加 `.json` 的元数据文件，记录设备、采集参数和校验值。`sha256` 对应原始打包字节，`file_sha256` 对应实际导出文件。

串口日志与逻辑采集在同一 USB 工作线程中串行调度，可以同时开启。采集和下载期间界面保持可操作，断开连接会取消任务并释放会话。波形保留最近一次成功结果；取消或错误不会覆盖它。

CLI 的 `capture` 命令与 GUI 共用采集会话，检查设备能力、采集编号、数据偏移、长度与错误标志。现阶段为单次即时采集，尚未提供外部触发、循环采集、协议解码或连续流式逻辑采集。

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
- macOS 使用 libusb 完成 RP2350 实板枚举、INFO 和 STATUS 请求，读取到固件 0.1.0、150 MHz 系统时钟及 UART 状态。
- UART 日志开启时，实板完成 25 MS/s、262144 点采集并读回 128 KiB，错误标志和协议坏帧数均为 0；另验证分数分频采样率、2 kS/s、取消及取消后重新采集。未连接已知码型源，尚未核对实际采样值与边沿时序。
- 26 项回归测试通过，覆盖采集分块、编号校验、取消、超时、导出、界面任务清理、USB 故障处理及 Windows 传输层选择；无 PyQt6 时跳过可选界面测试。

执行测试：

```powershell
python -m unittest discover -s tests -v
```

这些验证不替代实板测试。尚未烧录验证WinUSB首次绑定、UART接收、PIO样本正确性、采样率、A2/A4输入表现或长时间并发完整性。建议上板后先执行 `info/status`，再用已知字节序列和独立GPIO码型源逐项验证。
