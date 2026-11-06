#include "protocol.h"
#include <string.h>

// 多字节整数按小端逐字节读写，线上格式与 C 结构体对齐无关。
uint16_t proto_get_u16(const uint8_t *p)
{
    return (uint16_t)((uint16_t)p[0] | ((uint16_t)p[1] << 8));
}

uint32_t proto_get_u32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

void proto_put_u16(uint8_t *p, uint16_t value)
{
    p[0] = (uint8_t)value;
    p[1] = (uint8_t)(value >> 8);
}

void proto_put_u32(uint8_t *p, uint32_t value)
{
    for(unsigned i = 0; i < 4; ++i)
    {
        p[i] = (uint8_t)(value >> (8 * i));
    }
}

void proto_put_u64(uint8_t *p, uint64_t value)
{
    for(unsigned i = 0; i < 8; ++i)
    {
        p[i] = (uint8_t)(value >> (8 * i));
    }
}

uint32_t proto_crc_update(uint32_t crc, const uint8_t *data, size_t size)
{
    // IEEE CRC32，调用者负责初值与最终异或，允许分段计算。
    for(size_t i = 0; i < size; ++i)
    {
        crc ^= data[i];
        for(unsigned bit = 0; bit < 8; ++bit)
        {
            crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
        }
    }
    return crc;
}

size_t proto_encode(uint8_t *out, const proto_frame_t *frame)
{
    if(frame->length > PROTO_MAX_PAYLOAD)
    {
        return 0;
    }
    // 帧头固定 24 字节。CRC 位于偏移 20，payload 从偏移 24 开始。
    proto_put_u32(out, PROTO_MAGIC);
    out[4] = PROTO_VERSION;
    out[5] = frame->type;
    out[6] = frame->channel;
    out[7] = frame->flags;
    proto_put_u32(out + 8, frame->request_id);
    proto_put_u32(out + 12, frame->sequence);
    proto_put_u16(out + 16, frame->length);
    proto_put_u16(out + 18, 0);
    if(frame->length != 0)
    {
        memcpy(out + PROTO_HEADER_SIZE, frame->payload, frame->length);
    }
    // 头部和 payload 接续计算同一个 CRC，跳过 CRC 字段本身。
    uint32_t crc = proto_crc_update(0xFFFFFFFFu, out, 20);
    crc = proto_crc_update(crc, out + PROTO_HEADER_SIZE, frame->length);
    proto_put_u32(out + 20, crc ^ 0xFFFFFFFFu);
    return PROTO_HEADER_SIZE + frame->length;
}

static void discard_prefix(proto_parser_t *parser, size_t count)
{
    parser->used -= count;
    memmove(parser->data, parser->data + count, parser->used);
}

void proto_feed(proto_parser_t *parser, uint8_t byte, proto_handler_t handler, void *context)
{
    if(parser->used == sizeof(parser->data))
    {
        discard_prefix(parser, 1);
        ++parser->bad_frames;
    }
    parser->data[parser->used++] = byte;
    // USB 读写边界可能落在帧中间。数据不足时保留已有字节，等待下次输入。
    while(parser->used >= 4)
    {
        if(proto_get_u32(parser->data) != PROTO_MAGIC)
        {
            discard_prefix(parser, 1);
            continue;
        }
        if(parser->used < PROTO_HEADER_SIZE)
        {
            return;
        }
        uint16_t length = proto_get_u16(parser->data + 16);
        if(parser->data[4] != PROTO_VERSION || length > PROTO_MAX_PAYLOAD ||
           proto_get_u16(parser->data + 18) != 0)
        {
            ++parser->bad_frames;
            discard_prefix(parser, 1);
            continue;
        }
        size_t total = PROTO_HEADER_SIZE + length;
        if(parser->used < total)
        {
            return;
        }
        uint32_t crc = proto_crc_update(0xFFFFFFFFu, parser->data, 20);
        crc = proto_crc_update(crc, parser->data + PROTO_HEADER_SIZE, length);
        if((crc ^ 0xFFFFFFFFu) != proto_get_u32(parser->data + 20))
        {
            // 当前帧候选无效，向后滑动一个字节，继续寻找下一处帧头。
            ++parser->bad_frames;
            discard_prefix(parser, 1);
            continue;
        }
        proto_frame_t frame =
        {
            .type = parser->data[5],
            .channel = parser->data[6],
            .flags = parser->data[7],
            .request_id = proto_get_u32(parser->data + 8),
            .sequence = proto_get_u32(parser->data + 12),
            .length = length,
            .payload = parser->data + PROTO_HEADER_SIZE
        };
        // 回调先消费借用的 payload，随后移除已处理的帧。
        handler(&frame, context);
        discard_prefix(parser, total);
    }
}
