#include "uart_capture.h"
#include "agent_config.h"
#include "lwrb/lwrb.h"
#include "hardware/uart.h"
#include "hardware/irq.h"
#include "hardware/sync.h"
#include "hardware/regs/uart.h"
#include "pico/stdlib.h"

typedef struct
{
    uart_inst_t *uart;
    uint pin;
    uint irq;
    bool enabled;
    uint32_t baud;
    volatile uint32_t received;
    volatile uint32_t dropped;
    volatile uint32_t errors;
    lwrb_t rx;
    uint8_t data[AGENT_UART_RING_SIZE + 1u];
} rx_port_t;

static rx_port_t ports[2];

static void drain_fifo(rx_port_t *port)
{
    // 每次批量入队，满时仍要继续读取硬件 FIFO 和错误标志。
    uint8_t batch[32];
    do
    {
        size_t count = 0;
        while(count < sizeof(batch) && uart_is_readable(port->uart))
        {
            uint32_t value = uart_get_hw(port->uart)->dr;
            if(value & (UART_UARTDR_OE_BITS | UART_UARTDR_BE_BITS |
                        UART_UARTDR_PE_BITS | UART_UARTDR_FE_BITS))
            {
                ++port->errors;
            }
            batch[count++] = (uint8_t)value;
        }
        port->received += count;
        port->dropped += count - lwrb_write(&port->rx, batch, count);
    } while(uart_is_readable(port->uart));
}

static void uart_a_irq(void)
{
    drain_fifo(&ports[0]);
}

static void uart_b_irq(void)
{
    drain_fifo(&ports[1]);
}

bool uart_capture_configure(unsigned channel, bool enable, uint32_t baud)
{
    if(channel >= 2 || baud < 1200u || baud > 1000000u ||
       (channel == 1 && !AGENT_ENABLE_UART_B))
    {
        return false;
    }
    rx_port_t *port = &ports[channel];
    // 配置中断仅在本核执行；更改波特率前必须先关闭 RX 中断。
    irq_set_enabled(port->irq, false);
    if(port->enabled)
    {
        uart_set_irq_enables(port->uart, false, false);
        uart_deinit(port->uart);
    }
    port->enabled = false;
    if(enable)
    {
        port->baud = uart_init(port->uart, baud);
        gpio_set_function(port->pin, GPIO_FUNC_UART);
        gpio_disable_pulls(port->pin);
        uart_set_hw_flow(port->uart, false, false);
        uart_set_format(port->uart, 8, 1, UART_PARITY_NONE);
        uart_set_fifo_enabled(port->uart, true);
        port->enabled = true;
        uart_set_irq_enables(port->uart, true, false);
        irq_set_enabled(port->irq, true);
    }
    else
    {
        gpio_init(port->pin);
        gpio_set_dir(port->pin, GPIO_IN);
        gpio_disable_pulls(port->pin);
    }
    // 不初始化任何 UART TX 引脚，避免驱动被监听目标。
    return true;
}

void uart_capture_init(void)
{
    for(unsigned channel = 0; channel < 2; ++channel)
    {
        lwrb_init(&ports[channel].rx, ports[channel].data, sizeof(ports[channel].data));
    }
    ports[0].uart = uart1;
    ports[0].pin = AGENT_UART_A_RX_PIN;
    ports[0].irq = UART1_IRQ;
    ports[1].uart = uart0;
    ports[1].pin = AGENT_UART_B_RX_PIN;
    ports[1].irq = UART0_IRQ;
    irq_set_exclusive_handler(UART1_IRQ, uart_a_irq);
    irq_set_exclusive_handler(UART0_IRQ, uart_b_irq);
    uart_capture_configure(0, true, AGENT_UART_DEFAULT_BAUD);
    if(AGENT_ENABLE_UART_B)
    {
        uart_capture_configure(1, true, AGENT_UART_DEFAULT_BAUD);
    }
}

void uart_capture_stats(unsigned channel, uart_capture_stats_t *stats)
{
    rx_port_t *port = &ports[channel];
    uint32_t state = save_and_disable_interrupts();
    stats->enabled = port->enabled;
    stats->baud = port->baud;
    stats->received = port->received;
    stats->dropped = port->dropped;
    stats->errors = port->errors;
    stats->buffered = lwrb_get_full(&port->rx);
    restore_interrupts(state);
}

size_t uart_capture_read(unsigned channel, uint8_t *out, size_t capacity)
{
    rx_port_t *port = &ports[channel];
    uint32_t state = save_and_disable_interrupts();
    // UART 中断和消费端位于同一核，保留读操作的中断保护。
    size_t count = lwrb_read(&port->rx, out, capacity);
    restore_interrupts(state);
    return count;
}
