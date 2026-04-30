# Arducam EVK — C++ examples

```
.
├── functions/   # one .cpp per SDK feature (capture, controls, dump_info, …)
└── projects/
    ├── sync/          # demo_cpp_sync — full-featured sync capture (recommended)
    ├── async/         # demo_cpp_async — async capture variant
    ├── simple/        # minimal capture example
    ├── simple-async/  # minimal async capture example
    └── watch-devices/ # USB hot-plug monitor
```

## Recommended entry point

[`projects/sync`](projects/sync/README.md) is the all-in-one demo. It now
integrates every capability of the standalone `functions/` examples behind
flags (`--list-controls`, `--list-modes`, `--mode`, `--ctrl name=value`,
`--show-fps`, `--save`, `--frames`, `--dma`, `--transfer-count`,
`--transfer-size`, `--info`, `--verbose`, `--log-file`) and adds a
**sensor register dump** (`--dump-regs`). See its
[README](projects/sync/README.md) for build instructions and examples.

## Build (any project)

```powershell
# Windows — make the VS-bundled CMake visible
$env:Path = "C:\Program Files\Microsoft Visual Studio\18\Insiders\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin;" + $env:Path

cd C:\EVK\c++\projects\sync   # or async, simple, …
cmake -S . -B build `
      -DOpenCV_DIR="C:/opencv/build/x64/vc16/lib" `
      "-DCMAKE_POLICY_VERSION_MINIMUM=3.5"
cmake --build build --config Release
```

```bash
# Linux
cd c++/projects/sync
mkdir -p build && cd build && cmake ..
make -j
```

The shared CMake helpers live in:

- [CppOption.cmake](CppOption.cmake) — toolchain, default config file, DLL
  copy helper.
- [projects/FindOpenCV.cmake](projects/FindOpenCV.cmake) — OpenCV discovery
  and runtime DLL collection on Windows.

## Default camera configuration

When started without a `config_file` argument, all demos load the cfg pinned by
the `DEFAULT_CONFIG_FILE` cache variable (default:
`<repo>/Mira220_MIPI_2Lane_640x480_12b_80fps.cfg`). Override at configure time
with `-DDEFAULT_CONFIG_FILE=<absolute-path>`.
