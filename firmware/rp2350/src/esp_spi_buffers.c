#include "esp_spi_buffers.h"
#include "agent_config.h"
#include "lwrb/lwrb.h"

static lwrb_t tx;
static lwrb_t rx;
static uint8_t tx_data[AGENT_ESP_SPI_RING_SIZE + 1u];
static uint8_t rx_data[AGENT_ESP_SPI_RING_SIZE + 1u];

void esp_spi_buffers_init(void)
{
    lwrb_init(&tx, tx_data, sizeof(tx_data));
    lwrb_init(&rx, rx_data, sizeof(rx_data));
}

size_t esp_spi_queue_tx(const uint8_t *data, size_t length)
{
    return lwrb_write(&tx, data, length);
}

size_t esp_spi_read_rx(uint8_t *data, size_t capacity)
{
    return lwrb_read(&rx, data, capacity);
}

size_t esp_spi_take_tx(uint8_t *data, size_t capacity)
{
    return lwrb_read(&tx, data, capacity);
}

size_t esp_spi_push_rx(const uint8_t *data, size_t length)
{
    return lwrb_write(&rx, data, length);
}
