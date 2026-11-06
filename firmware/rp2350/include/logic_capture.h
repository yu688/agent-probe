#ifndef AGENT_LOGIC_CAPTURE_H
#define AGENT_LOGIC_CAPTURE_H

#include <stdbool.h>
#include <stdint.h>

enum
{
    CAPTURE_IDLE = 0,
    CAPTURE_RUNNING = 1,
    CAPTURE_DONE = 2,
    CAPTURE_CANCELLED = 3,
    CAPTURE_ERROR = 4
};

typedef struct
{
    uint32_t id;
    uint32_t state;
    uint32_t requested_rate;
    uint32_t actual_rate;
    uint32_t samples;
    uint32_t bytes;
    uint32_t flags;
    uint64_t start_us;
    uint64_t end_us;
} logic_capture_info_t;

void logic_capture_init(void);
bool logic_capture_start(uint32_t rate, uint32_t samples);
void logic_capture_poll(void);
void logic_capture_cancel(void);
const logic_capture_info_t *logic_capture_info(void);
const uint8_t *logic_capture_data(void);

#endif
