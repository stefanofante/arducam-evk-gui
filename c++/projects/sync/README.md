# demo_cpp_sync — Arducam EVK sync capture demo (extended)

This demo opens an Arducam EVK USB camera, captures frames in **sync** mode and
displays them with OpenCV. It also bundles every feature from
[c++/functions](../../functions) as a CLI flag, so a single executable can be
used for capture, diagnostics, control tweaking and **sensor register dump**.

## Build (Windows)

Prerequisites:

- Visual Studio 2022/Insiders with the C++ workload (provides `cl.exe` and a
  bundled CMake under `Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin`).
- OpenCV ≥ 4.x installed at `C:\opencv` (or set `OpenCV_DIR` accordingly).
- Arducam EVK SDK already shipped under [evk_sdk](../../../evk_sdk).

```powershell
$env:Path = "C:\Program Files\Microsoft Visual Studio\18\Insiders\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin;" + $env:Path

cd C:\EVK\c++\projects\sync
cmake -S . -B build `
      -DOpenCV_DIR="C:/opencv/build/x64/vc16/lib" `
      "-DCMAKE_POLICY_VERSION_MINIMUM=3.5"
cmake --build build --config Release
```

OpenCV runtime DLLs and Arducam EVK DLLs are copied next to the executable as a
post-build step (see [c++/CppOption.cmake](../../CppOption.cmake) and
[FindOpenCV.cmake](../FindOpenCV.cmake)).

The same recipe works for [c++/projects/async](../async) and
[c++/functions](../../functions).

### Default config file

If no `config_file` argument is given, the demo loads the cache variable
`DEFAULT_CONFIG_FILE` baked at configure time. Shipped default is the Mira220
80 fps profile at the repository root:

```
C:/EVK/Mira220_MIPI_2Lane_640x480_12b_80fps.cfg
```

Override with `-DDEFAULT_CONFIG_FILE=<absolute-path>` at configure time.

## Build (Linux)

```bash
cd c++/projects/sync
mkdir -p build && cd build
cmake ..
make -j
./demo_cpp_sync [config_file] [options]
```

## Usage

```text
demo_cpp_sync [config_file] [options]

Capture & visualization:
  -f, --fps <n>            Target framerate (default: 30, 0 = no change)
  -d, --device <i>         Device index (default: 0)
      --frames <n>         Capture N frames then exit (default: infinite)
      --save <dir>         Save each captured frame as PNG into <dir>
      --show-fps           Overlay FPS/bandwidth on the preview

Camera controls:
      --ctrl <name=value>  Set a control by name (repeatable)
      --list-controls      Print available controls and exit
      --list-modes         Print sensor modes (bin config) and exit
      --mode <id>          Switch to sensor mode <id> (bin config)

Open / transport:
      --dma                Open camera with DMA memory type
      --transfer-count <n> USB transfer count (with --transfer-size)
      --transfer-size  <n> USB transfer buffer size in bytes

Diagnostics:
      --info               Print camera info and exit
      --dump-regs [s-e]    Dump sensor registers (default 0x1000-0x6FFF)
      --dump-out <file>    Save register dump to file (cfg-style)
      --verbose            Enable SDK console log
      --log-file <path>    Save SDK log to file (level=trace)

  -h, --help               Show this help
```

### Examples

```powershell
.\demo_cpp_sync.exe                                            # default cfg, 30 fps
.\demo_cpp_sync.exe -f 60 --show-fps                           # 60 fps with overlay
.\demo_cpp_sync.exe --list-controls                            # discover controls
.\demo_cpp_sync.exe --ctrl "Exp(us)=2000" -f 30                # tune exposure
.\demo_cpp_sync.exe --frames 50 --save C:\tmp\mira220          # batch capture
.\demo_cpp_sync.exe --dump-regs --dump-out mira220_regs.cfg    # full reg dump
.\demo_cpp_sync.exe --dump-regs 0x1000-0x10FF                  # narrow dump (datasheet §9)
```

Press `q` in the preview window to quit.

## Notes

- `--list-modes` / `--mode` only work when the config is a `.bin` file.
- `--ctrl` accepts the control names declared in the `.cfg` Lua blocks
  (Mira220: `Framerate`, `Exp(us)`).
- `--dump-regs` uses `Arducam::Camera::readSensorReg()`. Defaults cover the
  documented Mira220 register space (0x1000-0x6FFF). The output uses the same
  `REG = 0xAAAA, 0xVV` syntax as `.cfg` files.
- 12-bit Bayer / monochrome frames are auto-stretched (`cv::normalize` MINMAX)
  before display, mirroring the Python demo.
