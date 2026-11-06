#include "logic_capture.h"
#include "agent_config.h"
#include "hardware/pio.h"
#include "hardware/dma.h"
#include "hardware/clocks.h"
#include "hardware/regs/pio.h"
#include "pico/stdlib.h"
#include "logic_capture.pio.h"

static PIO capture_pio = pio0;
static uint capture_sm;
static uint program_offset;
static int dma_channel;
static uint32_t samples_buffer[AGENT_LOGIC_MAX_SAMPLES / 8u];
static logic_capture_info_t info;
static uint64_t deadline_us;

void logic_capture_init(void)
{
    capture_sm = pio_claim_unused_sm(capture_pio, true);
    program_offset = pio_add_program(capture_pio, &logic_capture_program);
    dma_channel = dma_claim_unused_channel(true);
    for(uint pin = AGENT_LOGIC_BASE_PIN; pin < AGENT_LOGIC_BASE_PIN + AGENT_LOGIC_CHANNELS; ++pin)
    {
        pio_gpio_init(capture_pio, pin);
        gpio_disable_pulls(pin);
    }
    pio_sm_set_consecutive_pindirs(capture_pio, capture_sm, AGENT_LOGIC_BASE_PIN, AGENT_LOGIC_CHANNELS, false);
}

bool logic_capture_start(uint32_t rate, uint32_t samples)
{
    if(info.state == CAPTURE_RUNNING || rate < AGENT_LOGIC_MIN_RATE ||
       rate > AGENT_LOGIC_MAX_RATE || samples == 0 ||
       samples > AGENT_LOGIC_MAX_SAMPLES || (samples % 8u) != 0)
    {
        return false;
    }
    uint32_t sys_hz = clock_get_hz(clk_sys);
    uint32_t divider256 = (uint32_t)(((uint64_t)sys_hz * 128u + rate - 1u) / rate);
    if(divider256 < 256u || divider256 > 0xFFFFFFu)
    {
        return false;
    }
    pio_sm_set_enabled(capture_pio, capture_sm, false);
    dma_channel_abort(dma_channel);
    pio_interrupt_clear(capture_pio, 0);
    capture_pio->fdebug = 1u << (PIO_FDEBUG_RXSTALL_LSB + capture_sm);

    // 向上取整分频，实际采样率不超过请求值；保留输入同步器。
    pio_sm_config config = logic_capture_program_get_default_config(program_offset);
    sm_config_set_in_pins(&config, AGENT_LOGIC_BASE_PIN);
    sm_config_set_in_shift(&config, true, true, 32);
    sm_config_set_out_shift(&config, true, false, 32);
    // 本程序需要一次 TX pull，因此不使用会关闭 TX FIFO 的 RX 合并。
    sm_config_set_fifo_join(&config, PIO_FIFO_JOIN_NONE);
    sm_config_set_clkdiv_int_frac8(&config, divider256 >> 8, divider256 & 0xFFu);
    pio_sm_init(capture_pio, capture_sm, program_offset, &config);
    pio_sm_put(capture_pio, capture_sm, samples - 1u);

    dma_channel_config dma_config = dma_channel_get_default_config(dma_channel);
    channel_config_set_transfer_data_size(&dma_config, DMA_SIZE_32);
    channel_config_set_read_increment(&dma_config, false);
    channel_config_set_write_increment(&dma_config, true);
    channel_config_set_dreq(&dma_config, pio_get_dreq(capture_pio, capture_sm, false));
    dma_channel_configure(dma_channel, &dma_config, samples_buffer,
                          &capture_pio->rxf[capture_sm], samples / 8u, false);
    ++info.id;
    if(info.id == 0)
    {
        ++info.id;
    }
    info.state = CAPTURE_RUNNING;
    info.requested_rate = rate;
    info.actual_rate = (uint32_t)(((uint64_t)sys_hz * 128u) / divider256);
    info.samples = samples;
    info.bytes = samples / 2u;
    info.flags = 0;
    info.end_us = 0;
    // 这是 CPU 启动标记，不宣称是首个采样沿的精确时间。
    info.start_us = time_us_64();
    deadline_us = info.start_us + ((uint64_t)samples * 1000000u / info.actual_rate) + 1000000u;
    dma_start_channel_mask(1u << dma_channel);
    pio_sm_set_enabled(capture_pio, capture_sm, true);
    return true;
}

void logic_capture_poll(void)
{
    if(info.state != CAPTURE_RUNNING)
    {
        return;
    }
    if(!dma_channel_is_busy(dma_channel) && pio_interrupt_get(capture_pio, 0))
    {
        pio_sm_set_enabled(capture_pio, capture_sm, false);
        info.end_us = time_us_64();
        info.flags = (capture_pio->fdebug >> (PIO_FDEBUG_RXSTALL_LSB + capture_sm)) & 1u;
        info.state = info.flags ? CAPTURE_ERROR : CAPTURE_DONE;
    }
    else if(time_us_64() > deadline_us)
    {
        logic_capture_cancel();
        info.flags |= 2u;
        info.state = CAPTURE_ERROR;
    }
}

void logic_capture_cancel(void)
{
    if(info.state == CAPTURE_RUNNING)
    {
        pio_sm_set_enabled(capture_pio, capture_sm, false);
        dma_channel_abort(dma_channel);
        info.end_us = time_us_64();
        info.state = CAPTURE_CANCELLED;
    }
}

const logic_capture_info_t *logic_capture_info(void)
{
    return &info;
}

const uint8_t *logic_capture_data(void)
{
    return (const uint8_t *)samples_buffer;
}
