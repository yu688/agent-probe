#ifndef AGENT_PROTOCOL_H
#define AGENT_PROTOCOL_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define PROTO_MAGIC 0x31445052u
#define PROTO_VERSION 1u
#define PROTO_HEADER_SIZE 24u
#define PROTO_MAX_PAYLOAD 1024u
#define PROTO_MAX_FRAME (PROTO_HEADER_SIZE + PROTO_MAX_PAYLOAD)

enum
{
    CMD_INFO = 0x01,
    CMD_STATUS = 0x02,
    CMD_UART_CONFIG = 0x03,
    CMD_LOG_STREAM = 0x04,
    CMD_CAPTURE_START = 0x10,
    CMD_CAPTURE_STATUS = 0x11,
    CMD_CAPTURE_READ = 0x12,
    CMD_CAPTURE_CANCEL = 0x13,
    EVENT_UART_DATA = 0x40
};

enum
{
    STATUS_OK = 0,
    STATUS_BAD_ARGUMENT = 1,
    STATUS_BUSY = 2,
    STATUS_UNSUPPORTED = 3,
    STATUS_NOT_READY = 4
};

typedef struct
{
    uint8_t type;
    uint8_t channel;
    uint8_t flags;
    uint32_t request_id;
    uint32_t sequence;
    uint16_t length;
    const uint8_t *payload;
} proto_frame_t;

typedef struct
{
    uint8_t data[PROTO_MAX_FRAME];
    size_t used;
    uint32_t bad_frames;
} proto_parser_t;

typedef void (*proto_handler_t)(const proto_frame_t *frame, void *context);

uint16_t proto_get_u16(const uint8_t *p);
uint32_t proto_get_u32(const uint8_t *p);
void proto_put_u16(uint8_t *p, uint16_t value);
void proto_put_u32(uint8_t *p, uint32_t value);
void proto_put_u64(uint8_t *p, uint64_t value);
uint32_t proto_crc_update(uint32_t crc, const uint8_t *data, size_t size);
size_t proto_encode(uint8_t *out, const proto_frame_t *frame);
void proto_feed(proto_parser_t *parser, uint8_t byte, proto_handler_t handler, void *context);

#endif
