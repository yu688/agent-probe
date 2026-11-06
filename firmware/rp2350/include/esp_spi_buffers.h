#ifndef AGENT_ESP_SPI_BUFFERS_H
#define AGENT_ESP_SPI_BUFFERS_H

#include <stddef.h>
#include <stdint.h>

// 两端先在同一执行上下文使用，接入 ISR、DMA 或双核时需验证同步。
void esp_spi_buffers_init(void);

// 供协议层使用。返回实际入队或出队的字节数。
size_t esp_spi_queue_tx(const uint8_t *data, size_t length);
size_t esp_spi_read_rx(uint8_t *data, size_t capacity);

// 供 SPI 传输层使用。写入空间不足时，仅接收能入队的字节。
size_t esp_spi_take_tx(uint8_t *data, size_t capacity);
size_t esp_spi_push_rx(const uint8_t *data, size_t length);

#endif
