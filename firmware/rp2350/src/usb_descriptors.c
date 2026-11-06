/*
 * Microsoft OS 2.0 描述符布局参考 TinyUSB webusb_serial 示例。
 * 对应上游 MIT 许可证见 THIRD_PARTY_NOTICES.md。
 */
#include <string.h>
#include "tusb.h"
#include "pico/unique_id.h"

#define MS_VENDOR_REQUEST 0x20u

// 此处默认 VID/PID 仅用于本地实验，构建时可覆盖。
static const tusb_desc_device_t device_descriptor =
{
    .bLength = sizeof(tusb_desc_device_t),
    .bDescriptorType = TUSB_DESC_DEVICE,
    .bcdUSB = 0x0210,
    .bDeviceClass = 0,
    .bDeviceSubClass = 0,
    .bDeviceProtocol = 0,
    .bMaxPacketSize0 = CFG_TUD_ENDPOINT0_SIZE,
    .idVendor = AGENT_USB_VID,
    .idProduct = AGENT_USB_PID,
    .bcdDevice = 0x0100,
    .iManufacturer = 1,
    .iProduct = 2,
    .iSerialNumber = 3,
    .bNumConfigurations = 1
};

static const uint8_t configuration_descriptor[] =
{
    TUD_CONFIG_DESCRIPTOR(1, 1, 0, TUD_CONFIG_DESC_LEN + TUD_VENDOR_DESC_LEN, 0, 100),
    TUD_VENDOR_DESCRIPTOR(0, 4, 0x01, 0x81, 64)
};

// 单功能设备采用设备级 MS OS 2.0 特性，不添加复合设备子集。
typedef struct __attribute__((packed))
{
    uint16_t header_length;
    uint16_t header_type;
    uint32_t windows_version;
    uint16_t total_length;
    uint16_t compatible_length;
    uint16_t compatible_type;
    char compatible_id[8];
    char sub_compatible_id[8];
    uint16_t property_length;
    uint16_t property_type;
    uint16_t property_data_type;
    uint16_t property_name_length;
    uint16_t property_name[21];
    uint16_t property_data_length;
    uint16_t property_data[40];
} ms_os_descriptor_t;

static const ms_os_descriptor_t ms_os_descriptor =
{
    .header_length = 10,
    .header_type = 0,
    .windows_version = 0x06030000,
    .total_length = 162,
    .compatible_length = 20,
    .compatible_type = 3,
    .compatible_id = "WINUSB",
    .sub_compatible_id = {0},
    .property_length = 132,
    .property_type = 4,
    .property_data_type = 7,
    .property_name_length = 42,
    .property_name = u"DeviceInterfaceGUIDs",
    .property_data_length = 80,
    .property_data = u"{7C9B140E-5C85-4783-8B8A-F0BC97913546}\0"
};
_Static_assert(sizeof(ms_os_descriptor) == 162, "Microsoft OS 描述符长度错误");

static const uint8_t bos_descriptor[] =
{
    TUD_BOS_DESCRIPTOR(TUD_BOS_DESC_LEN + TUD_BOS_MICROSOFT_OS_DESC_LEN, 1),
    TUD_BOS_MS_OS_20_DESCRIPTOR(sizeof(ms_os_descriptor), MS_VENDOR_REQUEST)
};

uint8_t const *tud_descriptor_device_cb(void)
{
    return (const uint8_t *)&device_descriptor;
}

uint8_t const *tud_descriptor_configuration_cb(uint8_t index)
{
    (void)index;
    return configuration_descriptor;
}

uint8_t const *tud_descriptor_bos_cb(void)
{
    return bos_descriptor;
}

bool tud_vendor_control_xfer_cb(uint8_t rhport, uint8_t stage, const tusb_control_request_t *request)
{
    if(stage != CONTROL_STAGE_SETUP)
    {
        return true;
    }
    if(request->bmRequestType == 0xC0 && request->bRequest == MS_VENDOR_REQUEST &&
       request->wIndex == 7 && request->wValue == 0)
    {
        return tud_control_xfer(rhport, request, (void *)&ms_os_descriptor, sizeof(ms_os_descriptor));
    }
    return false;
}

uint16_t const *tud_descriptor_string_cb(uint8_t index, uint16_t langid)
{
    (void)langid;
    static uint16_t utf16[64];
    static char serial[PICO_UNIQUE_BOARD_ID_SIZE_BYTES * 2 + 1];
    const char *text = NULL;
    if(index == 0)
    {
        utf16[0] = (TUSB_DESC_STRING << 8) | 4;
        utf16[1] = 0x0409;
        return utf16;
    }
    switch(index)
    {
        case 1:
            text = "DIY Embedded Tools";
            break;
        case 2:
            text = "RP2350 Agent Probe";
            break;
        case 3:
            pico_get_unique_board_id_string(serial, sizeof(serial));
            text = serial;
            break;
        case 4:
            text = "Agent Data";
            break;
        default:
            return NULL;
    }
    size_t count = strlen(text);
    if(count > 63)
    {
        count = 63;
    }
    for(size_t i = 0; i < count; ++i)
    {
        utf16[i + 1] = (uint8_t)text[i];
    }
    utf16[0] = (uint16_t)((TUSB_DESC_STRING << 8) | (2 * count + 2));
    return utf16;
}
