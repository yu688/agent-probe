#ifndef AGENT_UART_CAPTURE_H
#define AGENT_UART_CAPTURE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct
{
    uint32_t enabled;
    uint32_t baud;
    uint32_t received;
    uint32_t dropped;
    uint32_t errors;
    uint32_t buffered;
} uart_capture_stats_t;

void uart_capture_init(void);
bool uart_capture_configure(unsigned channel, bool enable, uint32_t baud);
void uart_capture_stats(unsigned channel, uart_capture_stats_t *stats);
size_t uart_capture_read(unsigned channel, uint8_t *out, size_t capacity);

#endif
