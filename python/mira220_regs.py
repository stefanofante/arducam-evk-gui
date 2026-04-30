"""
Mira220 register database
=========================

A curated, human-readable subset of the Mira220 sensor register map drawn
from ams OSRAM Mira220 datasheet revision (CMV220 family). Only addresses
that can be safely read/written from user space at runtime are listed.

Each entry is a dict with these keys:

    name       human-readable label shown in the GUI
    addr       16-bit register address (int)
    kind       value renderer:
                "u8"     8-bit register, decimal entry
                "u16be"  two consecutive regs, MSB at addr+0, LSB at addr+1
                "u16le"  two consecutive regs, LSB at addr+0, MSB at addr+1
                "u24be"  three consecutive regs, MSB first
                "bool"   single bit (0/1)
                "enum"   choices = {label: value}
                "ro"     read-only (used for status / version regs)
    lo, hi     numeric range (defaults: 0..255 for u8, 0..0xFFFF for u16)
    choices    dict for enum kind
    desc       short tooltip-like description
    help       longer text shown by the "Info" dialog (multi-paragraph OK)

The file can be extended at runtime. The GUI also tries to load
``mira220_user_regs.yaml`` or ``mira220_user_regs.json`` next to it; if
present, those entries are merged on top (same name → user wins).
"""

from __future__ import annotations

# fmt: off

MIRA220_REGS: list[dict] = [
    # =====================================================================
    # 0x1xxx — Sequencer / exposure / framing
    # =====================================================================
    dict(group="Acquisition", name="Imager state",
         addr=0x1003, kind="enum",
         choices={"Standby (0x00)": 0x00, "Streaming (0x04)": 0x04},
         desc="IMAGER_STATE — sensor power / streaming state",
         help=(
             "Top-level sensor state machine.\n\n"
             "  0x00 Standby  – analog blocks idle, registers accessible.\n"
             "  0x04 Streaming – MIPI active, frames produced.\n\n"
             "Switching to 0x04 with `camera.start()` is what the SDK does "
             "automatically. Manual toggling can help recover after errors."
         )),
    dict(group="Acquisition", name="Imager run",
         addr=0x10F0, kind="bool",
         desc="IMAGER_RUN — re-arm the sequencer",
         help="Bit-0 = run. Sequencer re-arm bit; toggled by the SDK at start."),
    dict(group="Acquisition", name="Imager run continuous",
         addr=0x1002, kind="bool",
         desc="IMAGER_RUN_CONTINUOUS — continuous vs. one-shot",
         help="0 = single-frame mode, 1 = free-running (default)."),
    dict(group="Acquisition", name="Exposure (rows)",
         addr=0x100C, kind="u16be", lo=1, hi=0xFFFF,
         desc="EXP_TIME — exposure in row periods",
         help=(
             "Integration time expressed in row periods.\n"
             "Actual exposure (µs) = EXP_TIME × ROW_LENGTH / pixel_clock.\n\n"
             "If you prefer microseconds, use the SDK control 'Exp(us)'."
         )),
    dict(group="Acquisition", name="Vertical blanking",
         addr=0x1012, kind="u16be", lo=0, hi=0xFFFF,
         desc="VBLANK — extra rows between frames",
         help=(
             "Number of blanking rows added after VSIZE active rows.\n"
             "Frame period = (VSIZE + VBLANK) × ROW_LENGTH / pixel_clock.\n"
             "Increase VBLANK to lower framerate without changing readout."
         )),
    dict(group="Acquisition", name="Row length (clk)",
         addr=0x102B, kind="u16be", lo=0x100, hi=0xFFFF,
         desc="ROW_LENGTH — row period in pixel clocks",
         help=(
             "Defines the horizontal scan period. Increasing it allows "
             "longer exposures per row but reduces framerate."
         )),
    dict(group="Acquisition", name="GLOB_RST mode",
         addr=0x1015, kind="bool",
         desc="Global reset (vs rolling shutter pseudo-global)",
         help="0 = rolling shutter, 1 = global-reset. Mira220 default = 0."),
    dict(group="Acquisition", name="Trigger mode",
         addr=0x1101, kind="enum",
         choices={"Internal": 0x00, "External": 0x01},
         desc="TRIG_MODE — start-of-frame source",
         help="External trigger ties exposure start to the TRIG pad."),
    dict(group="Acquisition", name="Illuminator enable",
         addr=0x10D7, kind="bool",
         desc="ILLUM_EN — strobe pin output",
         help=(
             "Enables the illuminator strobe on the ILLUM pad. The pulse is "
             "synchronous with the exposure window."
         )),
    dict(group="Acquisition", name="Illuminator polarity",
         addr=0x10D8, kind="bool",
         desc="ILLUM_POL — strobe polarity",
         help="0 = active low, 1 = active high."),

    # =====================================================================
    # Vertical window (within 0x10xx)
    # =====================================================================
    dict(group="Window / readout", name="V start (row)",
         addr=0x107D, kind="u16be", lo=0, hi=0x7FF,
         desc="VSTART — first active row",
         help="Top-most row of the readout window. 0 = first physical row."),
    dict(group="Window / readout", name="V size (rows)",
         addr=0x1087, kind="u16be", lo=1, hi=0x7FF,
         desc="VSIZE — number of active rows",
         help="Visible rows (height). Combined with VSTART defines window."),
    dict(group="Window / readout", name="VFLIP",
         addr=0x1095, kind="bool",
         desc="VFLIP — vertical mirror",
         help="Reverses the row readout order (image flipped vertically)."),

    # =====================================================================
    # 0x2xxx — Data path / horizontal window / output formatting
    # =====================================================================
    dict(group="Window / readout", name="H size (cols)",
         addr=0x2008, kind="u16be", lo=1, hi=0x7FF,
         desc="HSIZE — number of active columns",
         help="Visible columns (width)."),
    dict(group="Window / readout", name="H start (col)",
         addr=0x200A, kind="u16be", lo=0, hi=0x7FF,
         desc="HSTART — first active column",
         help="Left-most column of the readout window."),
    dict(group="Window / readout", name="HFLIP",
         addr=0x209C, kind="bool",
         desc="HFLIP — horizontal mirror",
         help="Reverses the column readout order (image flipped horizontally)."),
    dict(group="Window / readout", name="Bit depth",
         addr=0x209E, kind="enum",
         choices={"12 bit": 0x02, "10 bit": 0x04, "8 bit": 0x06},
         desc="BIT_DEPTH — output bit width",
         help=(
             "Output pixel depth on the MIPI link.\n"
             "Lower bit depths free MIPI bandwidth and allow higher fps."
         )),
    dict(group="Data path", name="Histogram enable",
         addr=0x205D, kind="bool",
         desc="HIST_EN — embedded histogram on top lines",
         help=(
             "Enables an embedded histogram strip on the first frame rows.\n"
             "When enabled the host receives statistical data prefixed to the image."
         )),
    dict(group="Data path", name="Test pattern",
         addr=0x2091, kind="enum",
         choices={
             "Off": 0x00,
             "Solid color": 0x01,
             "100% color bars": 0x02,
             "Fade-to-grey color bars": 0x03,
             "PN9 pseudo-random": 0x04,
             "Gradient": 0x05,
             "Square": 0x06,
             "Walking 1s (8b)": 0x07,
             "Walking 1s (10b)": 0x08,
         },
         desc="TPG_MODE — internal test pattern generator",
         help=(
             "Replaces sensor pixels with a synthetic pattern. Useful for "
             "validating the MIPI link without optical input."
         )),
    dict(group="Data path", name="Embedded data enable",
         addr=0x2029, kind="bool",
         desc="EMBED_DATA_EN — embed metadata lines",
         help="Adds 1-2 metadata lines (registers, frame counter) before image."),
    dict(group="Data path", name="Black level offset",
         addr=0x4047, kind="u16be", lo=0, hi=0xFFFF,
         desc="BLACK_LEVEL_OFFSET",
         help="Offset added to the dark reference subtraction stage."),

    # =====================================================================
    # 0x4xxx — Analog gains / references
    # =====================================================================
    dict(group="ADC / signal path", name="Analog gain",
         addr=0x4009, kind="u8", lo=0, hi=0xFF,
         desc="ANA_GAIN — analog gain code",
         help=(
             "Pre-ADC gain. Code → multiplier mapping is documented in the "
             "datasheet (Section 9.5). Higher values increase SNR-limited "
             "sensitivity but reduce dynamic range."
         )),
    dict(group="ADC / signal path", name="Black level (BSP)",
         addr=0x4006, kind="u8", lo=0, hi=0xFF,
         desc="BSP — black sample pedestal",
         help="Pedestal added before ADC to keep dark pixels above zero."),
    dict(group="ADC / signal path", name="ADC reference",
         addr=0x4014, kind="u8", lo=0, hi=0xFF,
         desc="ADC_REF — full-scale reference voltage code",
         help="Trim of the ADC reference. Don't change unless characterising."),
    dict(group="ADC / signal path", name="ADC gain",
         addr=0x4015, kind="u8", lo=0, hi=0xFF,
         desc="ADC_GAIN — column ADC gain code",
         help="Per-column ADC gain. Used together with ANA_GAIN."),
    dict(group="ADC / signal path", name="Digital gain",
         addr=0x205C, kind="u16be", lo=0, hi=0x3FF,
         desc="DIG_GAIN — post-ADC digital gain",
         help="Digital multiplier applied after the ADC, fixed-point."),
    dict(group="ADC / signal path", name="Dark column enable",
         addr=0x402B, kind="bool",
         desc="DARK_COL_EN — dark column subtraction",
         help="Enables on-chip black-level correction using dark columns."),

    # =====================================================================
    # 0x5xxx — PLL / clock tree
    # =====================================================================
    dict(group="PLL / clock", name="PLL multiplier",
         addr=0x5000, kind="u8", lo=1, hi=0xFF,
         desc="PLL_MULT",
         help="System PLL multiplier. Affects pixel clock and MIPI rate."),
    dict(group="PLL / clock", name="PLL pre-divider",
         addr=0x5001, kind="u8", lo=1, hi=0xFF,
         desc="PLL_PRE_DIV",
         help="PLL input pre-divider."),
    dict(group="PLL / clock", name="PLL post-divider",
         addr=0x5002, kind="u8", lo=1, hi=0xFF,
         desc="PLL_POST_DIV",
         help="PLL output post-divider."),
    dict(group="PLL / clock", name="PLL bypass",
         addr=0x5005, kind="bool",
         desc="PLL_BYPASS — use external CLK directly",
         help="Bypasses the PLL. Only used when feeding a clean MIPI clock."),

    # =====================================================================
    # 0x6xxx — MIPI / CSI-2 / D-PHY
    # =====================================================================
    dict(group="MIPI / CSI-2", name="Lane count",
         addr=0x6012, kind="enum",
         choices={"1 lane": 0x00, "2 lanes": 0x01},
         desc="LANE — active D-PHY lanes",
         help="Mira220 supports 1- and 2-lane CSI-2."),
    dict(group="MIPI / CSI-2", name="MIPI clock continuous",
         addr=0x6013, kind="bool",
         desc="CONT_CLK — continuous-clock mode",
         help="0 = clock toggles only during data, 1 = continuous (most receivers)."),
    dict(group="MIPI / CSI-2", name="MIPI virtual channel",
         addr=0x6014, kind="u8", lo=0, hi=3,
         desc="VC — virtual channel ID",
         help="CSI-2 virtual channel (0..3)."),
    dict(group="MIPI / CSI-2", name="MIPI bit rate (Mbps)",
         addr=0x6020, kind="u16be", lo=80, hi=1500,
         desc="MIPI_BITRATE",
         help="Target MIPI line rate. Must match host receiver capabilities."),
    dict(group="MIPI / CSI-2", name="THS-prepare",
         addr=0x6028, kind="u8", lo=0, hi=0xFF,
         desc="THS_PREPARE — D-PHY HS prepare time",
         help="D-PHY HS prepare timing parameter (in MIPI clock units)."),
    dict(group="MIPI / CSI-2", name="THS-zero",
         addr=0x6029, kind="u8", lo=0, hi=0xFF,
         desc="THS_ZERO — D-PHY HS zero time",
         help="D-PHY HS zero timing parameter."),
    dict(group="MIPI / CSI-2", name="THS-trail",
         addr=0x602A, kind="u8", lo=0, hi=0xFF,
         desc="THS_TRAIL — D-PHY HS trail time",
         help="D-PHY HS trail timing parameter."),
    dict(group="MIPI / CSI-2", name="TCLK-post",
         addr=0x602B, kind="u8", lo=0, hi=0xFF,
         desc="TCLK_POST — D-PHY clock post time",
         help="Time the clock lane stays in HS after the last data."),

    # =====================================================================
    # Identity / status (read-only)
    # =====================================================================
    dict(group="Identity / status", name="Chip ID high",
         addr=0x3000, kind="ro", desc="CHIP_ID[15:8]",
         help="Read-only sensor identification, high byte. Mira220 = 0x03."),
    dict(group="Identity / status", name="Chip ID low",
         addr=0x3001, kind="ro", desc="CHIP_ID[7:0]",
         help="Low byte of the sensor ID."),
    dict(group="Identity / status", name="Chip revision",
         addr=0x3002, kind="ro", desc="CHIP_REV",
         help="Silicon revision."),
    dict(group="Identity / status", name="Frame counter",
         addr=0x3010, kind="ro", desc="FRAME_CNT",
         help="Free-running frame counter, increments on each output frame."),
    dict(group="Identity / status", name="Temperature sensor",
         addr=0x3020, kind="ro", desc="TEMP_SENSOR — die temperature",
         help="On-die temperature reading. Conversion in datasheet §11."),

    # =====================================================================
    # OTP / one-time programmable
    # =====================================================================
    dict(group="OTP", name="OTP address",
         addr=0x3500, kind="u16be", lo=0, hi=0xFFFF,
         desc="OTP_ADDR — start address for OTP read",
         help="Sets the OTP address before issuing OTP_READ. See datasheet §10."),
    dict(group="OTP", name="OTP read trigger",
         addr=0x3502, kind="bool",
         desc="OTP_READ — trigger OTP read",
         help="Pulse 0→1 to load OTP_DATA from OTP_ADDR. Auto-clears."),
    dict(group="OTP", name="OTP data",
         addr=0x3503, kind="ro",
         desc="OTP_DATA — readback after OTP_READ",
         help="Result of the last OTP read."),

    # =====================================================================
    # Power / supply control
    # =====================================================================
    dict(group="Power", name="Soft reset",
         addr=0x0103, kind="bool",
         desc="SOFT_RESET — reset all registers",
         help="Writing 1 reloads default register values. Auto-clears."),
    dict(group="Power", name="Standby control",
         addr=0x0100, kind="bool",
         desc="STBY — software standby",
         help="Forces the sensor into standby (analog blocks off)."),
]
# fmt: on


def all_groups() -> list[str]:
    seen, groups = set(), []
    for r in MIRA220_REGS:
        g = r["group"]
        if g not in seen:
            seen.add(g)
            groups.append(g)
    return groups


def by_group() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in MIRA220_REGS:
        out.setdefault(r["group"], []).append(r)
    return out
