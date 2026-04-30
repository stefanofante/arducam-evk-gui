#include <stdio.h>

#include <chrono>
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>
#include <string_view>
#include <thread>
#include <vector>

#include "utils.h"

bool exit_flag = false;
bool g_show_fps_overlay = false;
double g_current_fps = 0.0;
double g_current_bw_mb = 0.0;

void show_image(Arducam::Camera &camera, ArducamImageFrame image)
{
    int key = cv::waitKey(1);
    if (key == 'q')
    {
        exit_flag = true;
    }

    if (g_show_fps_overlay)
    {
        cv::Mat frame = from_image(image);
        if (!frame.empty())
        {
            char text[128];
            snprintf(text, sizeof(text), "FPS: %.2f  BW: %.2f MB/s", g_current_fps, g_current_bw_mb);
            cv::putText(frame, text, cv::Point(10, 25), cv::FONT_HERSHEY_SIMPLEX, 0.6,
                        cv::Scalar(0, 255, 0), 2);
            cv::imshow("Test", frame);
            cv::setWindowTitle("Test", "Test " + std::to_string(image.seq));
        }
    }
    else
    {
        show_image(image, "Test");
    }
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
void dumpDeviceInfo(Arducam::Camera &camera)
{
    uint32_t version = 0, year = 0, mouth = 0, day = 0;
    version = camera.readReg(Arducam::I2CMode::I2C_MODE_8_8, USB_CPLD_I2C_ADDRESS, 0x00);
    year = camera.readReg(Arducam::I2CMode::I2C_MODE_8_8, USB_CPLD_I2C_ADDRESS, 0x05);
    mouth = camera.readReg(Arducam::I2CMode::I2C_MODE_8_8, USB_CPLD_I2C_ADDRESS, 0x06);
    day = camera.readReg(Arducam::I2CMode::I2C_MODE_8_8, USB_CPLD_I2C_ADDRESS, 0x07);
    printf("CPLD version: v%d.%d date: 20%d-%02d-%02d\n", version >> 4, version & 0x0F, year, mouth, day);
    uint8_t data[16];
    if (camera.readBoardConfig(0x80, 0x00, 0x00, 2, data))
    {
        printf("fw_version: v%d.%d \n", data[0] & 0xFF, data[1] & 0xFF);
    }
}

static void list_controls(Arducam::Camera &camera)
{
    const Control *controls = camera.controls();
    const uint32_t n = camera.controlSize();
    printf("Controls (%u):\n", n);
    for (uint32_t i = 0; i < n; ++i)
    {
        const auto &c = controls[i];
        printf("  %-20s (func=%s) range=[%lld:%lld:%lld] default=%lld\n",
               c.name, c.func, (long long)c.min, (long long)c.max,
               (long long)c.step, (long long)c.def);
    }
}

static void list_modes(Arducam::Camera &camera)
{
    if (camera.configType() != Arducam::ConfigType::BINARY)
    {
        printf("List modes is only available with binary (.bin) config.\n");
        return;
    }
    auto n = camera.modeSize();
    std::vector<uint32_t> ids(n);
    std::vector<ArducamCameraConfig> configs(n);
    if (!camera.listMode(ids.data(), configs.data()))
    {
        printf("Failed to list modes.\n");
        return;
    }
    printf("Sensor modes (%u):\n", n);
    for (uint32_t i = 0; i < n; ++i)
    {
        printf("  id=%u: %ux%u\n", ids[i], configs[i].width, configs[i].height);
    }
}

static int parse_hex_or_dec(const std::string &s)
{
    return (int)std::stoul(s, nullptr, 0);
}

static void dump_sensor_regs(Arducam::Camera &camera, uint32_t start, uint32_t end, const std::string &out_path)
{
    std::ofstream fout;
    bool to_file = !out_path.empty();
    if (to_file)
    {
        fout.open(out_path);
        if (!fout)
        {
            printf("Cannot open output file: %s\n", out_path.c_str());
            return;
        }
    }

    auto &os = to_file ? (std::ostream &)fout : std::cout;
    os << "; sensor register dump 0x" << std::hex << start << " .. 0x" << end << std::dec << "\n";
    printf("Dumping sensor registers 0x%04X - 0x%04X ...\n", start, end);

    int ok = 0, fail = 0;
    for (uint32_t addr = start; addr <= end; ++addr)
    {
        uint32_t val = camera.readSensorReg(addr);
        if (camera.lastError() != Arducam::Errorcode::Success)
        {
            ++fail;
            continue;
        }
        ++ok;
        os << "REG = 0x" << std::hex << std::setw(4) << std::setfill('0') << addr
           << ", 0x" << std::setw(2) << std::setfill('0') << (val & 0xFF)
           << std::dec << "\n";
        if ((addr & 0xFF) == 0)
        {
            printf("  ... 0x%04X (ok=%d, fail=%d)\r", addr, ok, fail);
            fflush(stdout);
        }
    }
    printf("\nDump done: %d registers OK, %d failed.\n", ok, fail);
    if (to_file)
        printf("Saved to: %s\n", out_path.c_str());
}

struct CtrlSet
{
    std::string name;
    int64_t value;
};

static bool parse_ctrl_arg(const std::string &arg, CtrlSet &out)
{
    auto pos = arg.find('=');
    if (pos == std::string::npos || pos == 0 || pos == arg.size() - 1)
        return false;
    out.name = arg.substr(0, pos);
    try
    {
        out.value = std::stoll(arg.substr(pos + 1), nullptr, 0);
    }
    catch (...)
    {
        return false;
    }
    return true;
}

int main(int argc, char **argv)
{
    using namespace std::literals;

    std::string_view config;
    bool bin_config = false;
    uint32_t deviceID = 0;
    int target_fps = 30;

    bool opt_list_controls = false;
    bool opt_list_modes = false;
    bool opt_show_fps = false;
    bool opt_dma = false;
    bool opt_verbose = false;
    bool opt_info_only = false;
    int opt_mode_id = -1;
    int opt_frames = -1;
    int opt_transfer_count = -1;
    int opt_transfer_size = -1;
    std::string opt_save_dir;
    std::string opt_log_file;
    bool opt_dump_regs = false;
    uint32_t opt_dump_start = 0x1000;
    uint32_t opt_dump_end = 0x6FFF;
    std::string opt_dump_out;
    std::vector<CtrlSet> opt_ctrls;

    auto print_usage = [&](const char *prog)
    {
        printf("Usage: %s [config_file] [options]\n"
               "\n"
               "Capture & visualization:\n"
               "  -f, --fps <n>            Target framerate (default: 30, 0 = no change)\n"
               "  -d, --device <i>         Device index (default: 0)\n"
               "      --frames <n>         Capture N frames then exit (default: infinite)\n"
               "      --save <dir>         Save each captured frame as PNG into <dir>\n"
               "      --show-fps           Overlay FPS/bandwidth on the preview\n"
               "\n"
               "Camera controls:\n"
               "      --ctrl <name=value>  Set a control by name (repeatable)\n"
               "      --list-controls      Print available controls and exit\n"
               "      --list-modes         Print sensor modes (bin config) and exit\n"
               "      --mode <id>          Switch to sensor mode <id> (bin config)\n"
               "\n"
               "Open / transport:\n"
               "      --dma                Open camera with DMA memory type\n"
               "      --transfer-count <n> USB transfer count (with --transfer-size)\n"
               "      --transfer-size  <n> USB transfer buffer size in bytes\n"
               "\n"
               "Diagnostics:\n"
               "      --info               Print camera info and exit\n"
               "      --dump-regs [s-e]    Dump sensor registers, default range 0x1000-0x6FFF\n"
               "      --dump-out <file>    Save register dump to file (cfg-style)\n"
               "      --verbose            Enable SDK console log\n"
               "      --log-file <path>    Save SDK log to file (level=trace)\n"
               "\n"
               "  -h, --help               Show this help\n"
               "  config_file              Path to .cfg or .bin (default: %s)\n",
               prog, DEFAULT_CONFIG_FILE);
    };

    auto need_arg = [&](int &i, const char *flag) -> const char *
    {
        if (i + 1 >= argc)
        {
            printf("Missing value for %s\n", flag);
            std::exit(1);
        }
        return argv[++i];
    };

    for (int i = 1; i < argc; ++i)
    {
        std::string a = argv[i];
        if (a == "-h" || a == "--help")
        {
            print_usage(argv[0]);
            return 0;
        }
        else if (a == "-f" || a == "--fps")
            target_fps = atoi(need_arg(i, "--fps"));
        else if (a == "-d" || a == "--device")
            deviceID = (uint32_t)atoi(need_arg(i, "--device"));
        else if (a == "--frames")
            opt_frames = atoi(need_arg(i, "--frames"));
        else if (a == "--save")
            opt_save_dir = need_arg(i, "--save");
        else if (a == "--show-fps")
            opt_show_fps = true;
        else if (a == "--ctrl")
        {
            CtrlSet cs;
            std::string val = need_arg(i, "--ctrl");
            if (!parse_ctrl_arg(val, cs))
            {
                printf("Invalid --ctrl value (expected name=value): %s\n", val.c_str());
                return 1;
            }
            opt_ctrls.push_back(cs);
        }
        else if (a == "--list-controls")
            opt_list_controls = true;
        else if (a == "--list-modes")
            opt_list_modes = true;
        else if (a == "--mode")
            opt_mode_id = atoi(need_arg(i, "--mode"));
        else if (a == "--dma")
            opt_dma = true;
        else if (a == "--transfer-count")
            opt_transfer_count = atoi(need_arg(i, "--transfer-count"));
        else if (a == "--transfer-size")
            opt_transfer_size = atoi(need_arg(i, "--transfer-size"));
        else if (a == "--info")
            opt_info_only = true;
        else if (a == "--dump-regs")
        {
            opt_dump_regs = true;
            if (i + 1 < argc && argv[i + 1][0] != '-')
            {
                std::string range = argv[i + 1];
                auto dash = range.find('-');
                if (dash != std::string::npos)
                {
                    try
                    {
                        opt_dump_start = (uint32_t)parse_hex_or_dec(range.substr(0, dash));
                        opt_dump_end = (uint32_t)parse_hex_or_dec(range.substr(dash + 1));
                        ++i;
                    }
                    catch (...)
                    { /* keep defaults */
                    }
                }
            }
        }
        else if (a == "--dump-out")
            opt_dump_out = need_arg(i, "--dump-out");
        else if (a == "--verbose")
            opt_verbose = true;
        else if (a == "--log-file")
            opt_log_file = need_arg(i, "--log-file");
        else if (!a.empty() && a[0] != '-' && config.empty())
            config = argv[i];
        else
        {
            printf("Unknown argument: %s\n", argv[i]);
            print_usage(argv[0]);
            return 1;
        }
    }

    g_show_fps_overlay = opt_show_fps;

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
    if (opt_verbose)
        camera.enableConsoleLog(true);
    if (!opt_log_file.empty())
    {
        camera.setLogLevel(Arducam::LoggerLevel::trace);
        camera.addLogFile(opt_log_file.c_str());
        printf("SDK log -> %s\n", opt_log_file.c_str());
    }

    Arducam::Param param;
    param.config_file_name = config.data();
    param.bin_config = bin_config;
    param.device = device;
    if (opt_dma)
        param.mem_type = Arducam::MemType::DMA;

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

    PrintDeviceInfo(camera, device);
    dumpDeviceInfo(camera);

    if (camera.configType() == Arducam::ConfigType::TEXT)
    {
        printf("width: %d, height: %d\n", camera.width(), camera.height());
    }
    else if (camera.configType() == Arducam::ConfigType::BINARY)
    {
        printf("Mode size: %d\n", (int)camera.modeSize());
    }

    if (opt_list_controls)
    {
        list_controls(camera);
        camera.close();
        return 0;
    }
    if (opt_list_modes)
    {
        list_modes(camera);
        camera.close();
        return 0;
    }
    if (opt_dump_regs)
    {
        dump_sensor_regs(camera, opt_dump_start, opt_dump_end, opt_dump_out);
        camera.close();
        return 0;
    }
    if (opt_info_only)
    {
        camera.close();
        return 0;
    }

    if (opt_mode_id >= 0)
    {
        if (camera.switchMode((uint32_t)opt_mode_id))
            printf("Switched to sensor mode %d\n", opt_mode_id);
        else
            printf("Warning: switchMode failed (ret=%x)\n", camera.lastError());
    }

    if (target_fps > 0)
    {
        if (camera.setControl("Framerate", target_fps))
            printf("Framerate set to %d fps\n", target_fps);
        else
            printf("Warning: failed to set Framerate to %d fps (ret=%x)\n", target_fps, camera.lastError());
    }
    for (const auto &cs : opt_ctrls)
    {
        if (camera.setControl(cs.name.c_str(), cs.value))
            printf("Control '%s' set to %lld\n", cs.name.c_str(), (long long)cs.value);
        else
            printf("Warning: failed to set control '%s' (ret=%x)\n", cs.name.c_str(), camera.lastError());
    }

    if (opt_transfer_count > 0 && opt_transfer_size > 0)
    {
        if (camera.setTransfer(opt_transfer_count, opt_transfer_size))
            printf("Transfer set to count=%d size=%d\n", opt_transfer_count, opt_transfer_size);
        else
            printf("Warning: setTransfer failed (ret=%x)\n", camera.lastError());
    }

    camera.start();

    int saved = 0;
    int captured = 0;
    auto last_fps_update = std::chrono::steady_clock::now();
    while (!exit_flag)
    {
        ArducamImageFrame image;
        if (!camera.capture(image, 1000) || image.data == nullptr)
        {
            printf("Error reading frame.\n");
            continue;
        }

        if (opt_show_fps)
        {
            auto now = std::chrono::steady_clock::now();
            if (std::chrono::duration_cast<std::chrono::milliseconds>(now - last_fps_update).count() >= 500)
            {
                g_current_fps = camera.captureFps();
                g_current_bw_mb = camera.bandwidth() / 1024.0 / 1024.0;
                last_fps_update = now;
            }
        }

        show_image(camera, image);

        if (!opt_save_dir.empty())
        {
            char path[1024];
            snprintf(path, sizeof(path), "%s/frame_%06u.png", opt_save_dir.c_str(), image.seq);
            cv::Mat m = from_image(image);
            if (!m.empty() && cv::imwrite(path, m))
                ++saved;
        }

        camera.freeImage(std::move(image));
        ++captured;
        if (opt_frames > 0 && captured >= opt_frames)
            break;
    }

    if (opt_save_dir.size())
        printf("Saved %d frames to %s\n", saved, opt_save_dir.c_str());

    printf("Final FPS: %.2f, bandwidth: %.2f MB/s\n",
           camera.captureFps(), camera.bandwidth() / 1024.0 / 1024.0);

    camera.close();
    cv::destroyAllWindows();
    return 0;
}
