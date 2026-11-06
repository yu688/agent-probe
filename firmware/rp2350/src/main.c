#include <string.h>
#include "pico/stdlib.h"
#include "pico/unique_id.h"
#include "hardware/clocks.h"
#include "tusb.h"
#include "agent_config.h"
#include "protocol.h"
#include "uart_capture.h"
#include "logic_capture.h"

#define RESPONSE_QUEUE_SIZE 4u

typedef struct
{
    uint8_t data[PROTO_MAX_FRAME];
    size_t length;
    size_t sent;
} tx_packet_t;

static proto_parser_t parser;
static tx_packet_t responses[RESPONSE_QUEUE_SIZE];
static uint32_t response_head;
static uint32_t response_tail;
static tx_packet_t active_packet;
static uint32_t tx_sequence;
static bool log_stream;
static bool was_mounted;
static uint64_t last_log_us;
static unsigned next_log_channel;
static uint64_t last_rx_us;

static bool response_space(void)
{
    return response_head - response_tail < RESPONSE_QUEUE_SIZE;
}

static void reply(const proto_frame_t *request, uint32_t status, const uint8_t *data, size_t length)
{
    // 命令在主循环串行处理，使用静态工作区，避免占满4KiB主栈。
    static uint8_t payload[PROTO_MAX_PAYLOAD];
    proto_put_u32(payload, status);
    if(length != 0)
    {
        memcpy(payload + 4, data, length);
    }
    proto_frame_t frame =
    {
        .type = request->type | 0x80u,
        .channel = request->channel,
        .request_id = request->request_id,
        .sequence = tx_sequence++,
        .length = (uint16_t)(length + 4),
        .payload = payload
    };
    tx_packet_t *packet = &responses[response_head % RESPONSE_QUEUE_SIZE];
    packet->length = proto_encode(packet->data, &frame);
    packet->sent = 0;
    ++response_head;
}

static void encode_capture_info(uint8_t *out)
{
    const logic_capture_info_t *info = logic_capture_info();
    proto_put_u32(out, info->id);
    proto_put_u32(out + 4, info->state);
    proto_put_u32(out + 8, info->requested_rate);
    proto_put_u32(out + 12, info->actual_rate);
    proto_put_u32(out + 16, info->samples);
    proto_put_u32(out + 20, info->bytes);
    proto_put_u32(out + 24, info->flags);
    proto_put_u64(out + 28, info->start_us);
    proto_put_u64(out + 36, info->end_us);
}

static void command_received(const proto_frame_t *request, void *context)
{
    (void)context;
    if(!response_space())
    {
        // 损坏帧内嵌多个合法帧时也必须保持队列有界。
        ++parser.bad_frames;
        return;
    }
    static uint8_t data[PROTO_MAX_PAYLOAD - 4];
    size_t length = 0;
    uint32_t status = STATUS_OK;
    if(request->flags != 0 || request->type >= 0x40)
    {
        reply(request, STATUS_BAD_ARGUMENT, NULL, 0);
        return;
    }
    switch(request->type)
    {
        case CMD_INFO:
        {
            if(request->length != 0)
            {
                status = STATUS_BAD_ARGUMENT;
                break;
            }
            // 能力位：UART监听、定长即时采集、第二UART可用。
            proto_put_u32(data, 3u | (AGENT_ENABLE_UART_B ? 4u : 0u));
            proto_put_u32(data + 4, AGENT_LOGIC_MAX_SAMPLES);
            proto_put_u32(data + 8, AGENT_LOGIC_MAX_RATE);
            proto_put_u32(data + 12, clock_get_hz(clk_sys));
            proto_put_u32(data + 16, AGENT_UART_A_RX_PIN);
            proto_put_u32(data + 20, AGENT_UART_B_RX_PIN);
            pico_get_unique_board_id_string((char *)(data + 24), 17);
            memcpy(data + 41, "0.1.0", 6);
            length = 47;
            break;
        }
        case CMD_STATUS:
        {
            if(request->length != 0)
            {
                status = STATUS_BAD_ARGUMENT;
                break;
            }
            proto_put_u64(data, time_us_64());
            proto_put_u32(data + 8, parser.bad_frames);
            proto_put_u32(data + 12, log_stream);
            for(unsigned channel = 0; channel < 2; ++channel)
            {
                uart_capture_stats_t stats;
                uart_capture_stats(channel, &stats);
                size_t base = 16 + channel * 24;
                proto_put_u32(data + base, stats.enabled);
                proto_put_u32(data + base + 4, stats.baud);
                proto_put_u32(data + base + 8, stats.received);
                proto_put_u32(data + base + 12, stats.dropped);
                proto_put_u32(data + base + 16, stats.errors);
                proto_put_u32(data + base + 20, stats.buffered);
            }
            length = 64;
            break;
        }
        case CMD_UART_CONFIG:
        {
            if(request->channel >= 2 || request->length != 8 || request->payload[0] > 1 ||
               request->payload[1] || request->payload[2] || request->payload[3])
            {
                status = STATUS_BAD_ARGUMENT;
            }
            else if(!uart_capture_configure(request->channel, request->payload[0] != 0,
                                            proto_get_u32(request->payload + 4)))
            {
                status = STATUS_BAD_ARGUMENT;
            }
            break;
        }
        case CMD_LOG_STREAM:
        {
            if(request->length != 1 || request->payload[0] > 1)
            {
                status = STATUS_BAD_ARGUMENT;
            }
            else
            {
                log_stream = request->payload[0] != 0;
            }
            break;
        }
        case CMD_CAPTURE_START:
        {
            if(logic_capture_info()->state == CAPTURE_RUNNING)
            {
                status = STATUS_BUSY;
            }
            else if(request->length != 8 || !logic_capture_start(proto_get_u32(request->payload),
                                                                  proto_get_u32(request->payload + 4)))
            {
                status = STATUS_BAD_ARGUMENT;
            }
            else
            {
                encode_capture_info(data);
                length = 44;
            }
            break;
        }
        case CMD_CAPTURE_STATUS:
        {
            if(request->length != 0)
            {
                status = STATUS_BAD_ARGUMENT;
            }
            else
            {
                encode_capture_info(data);
                length = 44;
            }
            break;
        }
        case CMD_CAPTURE_READ:
        {
            const logic_capture_info_t *info = logic_capture_info();
            if(request->length != 12)
            {
                status = STATUS_BAD_ARGUMENT;
                break;
            }
            uint32_t id = proto_get_u32(request->payload);
            uint32_t offset = proto_get_u32(request->payload + 4);
            uint32_t count = proto_get_u32(request->payload + 8);
            if(info->state != CAPTURE_DONE)
            {
                status = STATUS_NOT_READY;
            }
            else if(id != info->id || offset > info->bytes || count == 0 || count > 1012u ||
                    count > info->bytes - offset)
            {
                status = STATUS_BAD_ARGUMENT;
            }
            else
            {
                proto_put_u32(data, id);
                proto_put_u32(data + 4, offset);
                memcpy(data + 8, logic_capture_data() + offset, count);
                length = 8 + count;
            }
            break;
        }
        case CMD_CAPTURE_CANCEL:
        {
            if(request->length != 0)
            {
                status = STATUS_BAD_ARGUMENT;
            }
            else
            {
                logic_capture_cancel();
            }
            break;
        }
        default:
            status = STATUS_UNSUPPORTED;
            break;
    }
    reply(request, status, data, length);
}

static bool prepare_log_packet(void)
{
    uint64_t now = time_us_64();
    if(!log_stream || now - last_log_us < AGENT_LOG_INTERVAL_US)
    {
        return false;
    }
    uint8_t payload[24 + AGENT_LOG_CHUNK_SIZE];
    for(unsigned attempt = 0; attempt < 2; ++attempt)
    {
        unsigned channel = next_log_channel++ % 2;
        size_t count = uart_capture_read(channel, payload + 24, AGENT_LOG_CHUNK_SIZE);
        if(count == 0)
        {
            continue;
        }
        uart_capture_stats_t stats;
        uart_capture_stats(channel, &stats);
        proto_put_u64(payload, now);
        proto_put_u32(payload + 8, stats.received);
        proto_put_u32(payload + 12, stats.dropped);
        proto_put_u32(payload + 16, stats.errors);
        proto_put_u32(payload + 20, 1u); // 时间戳类型1：主循环出队时间。
        proto_frame_t frame =
        {
            .type = EVENT_UART_DATA,
            .channel = (uint8_t)channel,
            .flags = (stats.dropped || stats.errors) ? 1 : 0,
            .sequence = tx_sequence++,
            .length = (uint16_t)(24 + count),
            .payload = payload
        };
        active_packet.length = proto_encode(active_packet.data, &frame);
        active_packet.sent = 0;
        last_log_us = now;
        return true;
    }
    return false;
}

static void transmit_poll(void)
{
    if(active_packet.length == 0)
    {
        if(response_head != response_tail)
        {
            active_packet = responses[response_tail % RESPONSE_QUEUE_SIZE];
            ++response_tail;
        }
        else if(!prepare_log_packet())
        {
            return;
        }
    }
    size_t available = tud_vendor_write_available();
    size_t count = active_packet.length - active_packet.sent;
    if(count > available)
    {
        count = available;
    }
    if(count != 0)
    {
        active_packet.sent += tud_vendor_write(active_packet.data + active_packet.sent, count);
        tud_vendor_write_flush();
    }
    if(active_packet.sent == active_packet.length)
    {
        active_packet.length = 0;
    }
}

int main(void)
{
    uart_capture_init();
    logic_capture_init();
    tusb_init();
    while(true)
    {
        tud_task();
        logic_capture_poll();
        bool mounted = tud_mounted();
        if(!mounted)
        {
            if(was_mounted)
            {
                // USB断连终止会话，日志队列保留；已发出的帧可能未到达主机。
                logic_capture_cancel();
                log_stream = false;
                parser.used = 0;
                response_head = response_tail = 0;
                active_packet.length = 0;
            }
            was_mounted = false;
            continue;
        }
        was_mounted = true;
        if(parser.used != 0 && time_us_64() - last_rx_us > 500000u)
        {
            // 不完整命令超时后丢弃，避免错误长度长期阻塞后续请求。
            parser.used = 0;
            ++parser.bad_frames;
        }
        // 每轮最多消费64字节，让USB发送和采集状态都能及时推进。
        for(unsigned budget = 0; budget < 64 && response_space() && tud_vendor_available(); ++budget)
        {
            uint8_t byte;
            if(tud_vendor_read(&byte, 1) != 1)
            {
                break;
            }
            last_rx_us = time_us_64();
            proto_feed(&parser, byte, command_received, NULL);
        }
        transmit_poll();
    }
}
