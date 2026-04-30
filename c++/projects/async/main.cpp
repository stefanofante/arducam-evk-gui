#include <stdio.h>

#include <condition_variable>
#include <iostream>
#include <string>
#include <string_view>

#include "utils.h"

bool exit_flag = false;
uint8_t key = 0;
std::mutex mtx;
std::condition_variable event_cond;

void preview(Arducam::Camera &camera, ArducamImageFrame image)
{
    int key = cv::waitKey(1);
    if (key != -1)
    {
        ::key = key;
        event_cond.notify_one();
    }

    show_image(image, "Test");
}

void error_process(Arducam::Camera &camera, ArducamLoggerLevel type, std::string_view error)
{
    printf("[Error] %s\n", error.data());
}

void PrintDeviceInfo(Arducam::Camera &camera, ArducamDeviceHandle device)
{
    printf("device id vendor: 0x%04X\n", device->id_vendor);
    printf("device id product: 0x%04X\n", device->id_product);
    printf("device in used: %d\n", device->in_used);

    const uint8_t *serial_number = device->serial_number;
    printf("device serial number: %c%c%c%c-%c%c%c%c-%c%c%c%c\n", serial_number[0], serial_number[1], serial_number[2],
           serial_number[3], serial_number[4], serial_number[5], serial_number[6], serial_number[7], serial_number[8],
           serial_number[9], serial_number[10], serial_number[11]);
    printf("device usb type: %s\n", camera.usbType());
    printf("device speed: %d\n", device->speed);
}

#define USB_CPLD_I2C_ADDRESS 0x46
#ifdef WITH_STD_OPTIONAL
#define read_8_8(camera, addr) camera.readReg(Arducam::I2CMode::I2C_MODE_8_8, USB_CPLD_I2C_ADDRESS, addr).value();
#else
#define read_8_8(camera, addr) camera.readReg(Arducam::I2CMode::I2C_MODE_8_8, USB_CPLD_I2C_ADDRESS, addr);
#endif

void dumpDeviceInfo(Arducam::Camera &camera) noexcept
{
    uint32_t version = 0, year = 0, mouth = 0, day = 0;
    version = read_8_8(camera, 0x00);
    year = read_8_8(camera, 0x05);
    mouth = read_8_8(camera, 0x06);
    day = read_8_8(camera, 0x07);
    printf("CPLD version: v%d.%d date: 20%d-%02d-%02d\n", version >> 4, version & 0x0F, year, mouth, day);
    uint8_t data[16];
    if (camera.readBoardConfig(0x80, 0x00, 0x00, 2, data))
    {
        printf("fw_version: v%d.%d \n", data[0] & 0xFF, data[1] & 0xFF);
    }
}
#undef read_8_8

int main(int argc, char **argv)
{
    using namespace std::literals;

    std::string_view config;
    bool bin_config = false;
    uint32_t deviceID = 0;
    int target_fps = 30;

    auto print_usage = [&](const char *prog)
    {
        printf("Usage: %s [config_file] [options]\n"
               "  config_file              Path to .cfg or .bin (default: %s)\n"
               "  -f, --fps <value>        Target framerate (default: 30, 0 = no change)\n"
               "  -d, --device <index>     Device index (default: 0)\n"
               "  -h, --help               Show this help\n",
               prog, DEFAULT_CONFIG_FILE);
    };

    for (int i = 1; i < argc; ++i)
    {
        std::string_view a = argv[i];
        if (a == "-h" || a == "--help")
        {
            print_usage(argv[0]);
            return 0;
        }
        else if ((a == "-f" || a == "--fps") && i + 1 < argc)
        {
            target_fps = atoi(argv[++i]);
        }
        else if ((a == "-d" || a == "--device") && i + 1 < argc)
        {
            deviceID = static_cast<uint32_t>(atoi(argv[++i]));
        }
        else if (!a.empty() && a[0] != '-' && config.empty())
        {
            config = a;
        }
        else
        {
            printf("Unknown argument: %s\n", argv[i]);
            print_usage(argv[0]);
            return 1;
        }
    }

    if (config.empty())
    {
        config = DEFAULT_CONFIG_FILE;
        printf("No configuration file provided, using default: %s\n", config.data());
    }
    if (config.size() >= 4 && config.substr(config.size() - 4) == ".bin")
    {
        bin_config = true;
    }

    Arducam::DeviceList device_list = Arducam::DeviceList::listDevices();
    const uint32_t device_list_size = device_list.size();
    printf("device list size: %d\n", device_list_size);
    if (deviceID >= device_list_size)
    {
        printf("Please select the correct device ID\n");
        return -1;
    }
    ArducamDeviceHandle device = device_list[deviceID];
    Arducam::Camera camera;
    Arducam::Param param;
    param.config_file_name = config.data();
    param.bin_config = bin_config;
    param.device = device;

    if (!camera.open(param))
    {
        printf("Failed to open camera. ret = %x\n", camera.lastError());
        return -1;
    }
    if (!camera.init())
    {
        printf("Failed to init camera. ret = %x\n", camera.lastError());
        return -1;
    }

    if (target_fps > 0)
    {
        if (camera.setControl("Framerate", target_fps))
        {
            printf("Framerate set to %d fps\n", target_fps);
        }
        else
        {
            printf("Warning: failed to set Framerate to %d fps (ret=%x)\n", target_fps, camera.lastError());
        }
    }

    PrintDeviceInfo(camera, device);
    dumpDeviceInfo(camera);
    printf("width: %d, height: %d\n", camera.width(), camera.height());

    camera.enableConsoleLog();
    // camera.setLogLevel(info);
    camera.setCaptureCallback(std::bind(preview, std::ref(camera), std::placeholders::_1));
    camera.setMessageCallback(std::bind(error_process, std::ref(camera), std::placeholders::_1, std::placeholders::_2));
    camera.start();

    // camera.setControl("setExposureTime", 10000);

    {
        std::unique_lock lk(mtx);
        while (!exit_flag)
        {
            if (key == 'q')
            {
                exit_flag = true;
                break;
            }
            key = 0;
            if (!event_cond.wait_for(lk, 500ms, []
                                     { return key != 0; }))
            {
                key = -1;
                // key = cv::pollKey();
            }
        }
        // exit_cond.wait(lk, [] { return exit_flag; });
    }

    camera.close();

    cv::destroyAllWindows();
    return 0;
}
