"""
MIT License

Copyright (c) Luis Clóvis

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "pyserial",
# ]
# ///
import os
import sys
import time
import subprocess
import sqlite3
import struct
import glob
import json
import shutil
from enum import IntEnum
from datetime import datetime, timedelta

SCRIPT_VERSION = "1.1.0"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")
SERVICE_NAME = "dpi110_poller.service"
SERVICE_PATH = f"/etc/systemd/system/{SERVICE_NAME}"
DB_PATH = os.path.join(SCRIPT_DIR, "dpi110_logs.db")
# DB_PATH = "/opt/dpi110_logs.db"

DEFAULT_CONFIG = {
    "logger_settings": {
        "interval_s": 1.0,
        "trip_set": 0.0,
        "trip_reset": 0.0,
        "max_samples": 1000,
        "log_mode": 1,
        "pressure_measurement": 1,
        "temperature_measurement": 1,
        "pressure_unit": 0,
        "temperature_unit": 0,
    },
    "data_log_identity": {"log_index": 1, "technician": "", "tag": ""},
    "leak_test_settings": {
        "run_time_in_s": 60,
        "pressure_unit": 0,
        "time_unit": 1,
        "leak_rate_precision": 2,
        "max_leak_rate": 1.0,
    },
    "leak_test_identity": {"log_index": 1, "technician": "", "tag": ""},
}

# ==========================================
# 0. BINARY EXTRACTION HELPERS
# ==========================================


def read_u8(data, offset):
    """Extracts a Little-Endian UInt8 from a buffer at the specified offset."""
    return struct.unpack_from("<B", data, offset)[0]


def read_i8(data, offset):
    """Extracts a Little-Endian Int8 from a buffer at the specified offset."""
    return struct.unpack_from("<b", data, offset)[0]


def read_u16(data, offset):
    """Extracts a Little-Endian UInt16 from a buffer at the specified offset."""
    return struct.unpack_from("<H", data, offset)[0]


def read_u32(data, offset):
    """Extracts a Little-Endian UInt32 from a buffer at the specified offset."""
    return struct.unpack_from("<I", data, offset)[0]


def read_i32(data, offset):
    """Extracts a Little-Endian Int32 from a buffer at the specified offset."""
    return struct.unpack_from("<i", data, offset)[0]


def read_float(data, offset):
    """Extracts a Little-Endian Float32 from a buffer at the specified offset."""
    return struct.unpack_from("<f", data, offset)[0]


def read_ascii(data, offset, length):
    """Extracts a null-terminated ASCII string from a fixed-size field."""
    return (
        data[offset : offset + length]
        .replace(b"\x00", b"")
        .decode("ascii", "ignore")
        .strip()
    )


# ==========================================
# 1. ENUMS & PROTOCOL MAPPING
# ==========================================


class IntEnumFmt(IntEnum):
    symbol: str
    factor: float

    def __new__(cls, value, symbol="", factor=1.0):
        obj = int.__new__(cls, value)
        obj._value_ = value
        obj.symbol = symbol
        obj.factor = factor
        return obj


class LogMode(IntEnum):
    Continuous = 0x00
    TripHigh = 0x01
    TripLow = 0x02


class CommandNumber(IntEnum):
    ReadDeviceInfo = 0x00
    ReadBatteryInfo = 0x01
    ReadCalibrationCertificateInfo = 0x02
    ReadLoggerInfo = 0x08
    ReadLeakTestLoggerInfo = 0x0A
    ReadLoggerSettings = 0x10
    WriteLoggerSettings = 0x11
    ReadLeakTestSettings = 0x18
    WriteLeakTestSettings = 0x19
    ReadLogHeader = 0x20
    ReadLogData = 0x22
    ReadLogTechnician = 0x24
    WriteLogTechnician = 0x25
    ReadLogTag = 0x26
    WriteLogTag = 0x27
    WriteLogSettings = 0x29
    ClearLogs = 0x2F
    ReadLeakTestLog = 0x32
    ReadLeakTestLogTechnician = 0x34
    WriteLeakTestLogTechnician = 0x35
    ReadLeakTestLogTag = 0x36
    WriteLeakTestLogTag = 0x37
    ClearLeakTestLogs = 0x3F
    ReadLeakTestRecipe = 0x40
    WriteLeakTestRecipe = 0x41
    SelectLeakTestRecipe = 0x42
    UnselectLeakTestRecipe = 0x43
    ReadSelectedLeakTestRecipeNumber = 0x44
    ClearLeakTestRecipe = 0x48
    ClearAllLeakTestRecipes = 0x49
    # ReadPressureAndTemperature = 0x50
    ReadPressureUnit = 0x52
    WritePressureUnit = 0x53
    ReadTemperatureUnit = 0x54
    WriteTemperatureUnit = 0x55
    ReadAutoOffMode = 0x60
    WriteAutoOffMode = 0x61
    ReadBrightness = 0x62
    WriteBrightness = 0x63
    ReadZeroProtectionMode = 0x64
    WriteZeroProtectionMode = 0x65
    ReadLockMode = 0x66
    WriteLockMode = 0x67
    ReadClockDate = 0x6A
    WriteClockDate = 0x6B
    ReadClockTime = 0x6C
    WriteClockTime = 0x6D
    SetPressureZero = 0x70
    ResetPressureZero = 0x71
    SetTemperatureReference = 0x72
    ResetTemperatureReference = 0x73
    ReadPressureTrimParameters = 0x74
    WritePressureTrimParameters = 0x75
    # ResetMemory = 0x80
    ReadTrueSensorType = 0x82
    # WriteSensorType = 0x83
    ReadTrueSensorLimits = 0x84
    # WriteSensorLimits = 0x85
    ReadSensorAccuracyClass = 0x86
    # WriteSensorAccuracyClass = 0x87
    ReadSerialNumber = 0x8A
    # WriteSerialNumber = 0x8B
    # WriteCalibrationCertificateInfo = 0x92
    # SetFactoryTemperatureReference = 0xA0
    # ResetFactoryTemperatureReference = 0xA1
    # ReadPressureLinearization = 0xA2
    # WritePressureLinearization = 0xA3
    # ReadUnlinearizedPressure = 0xAA
    # ReadUntrimmedPressure = 0xAB
    # ReadMemory = 0xE0
    SetLEDState = 0x95

    Undefined = 0xFF


class LogMeasurement(IntEnum):
    Blank = 0x00
    EndValue = 0x01
    Maximum = 0x02
    Minimum = 0x03
    Mean = 0x04
    MaxMinMean = 0x05


class LogStatus(IntEnum):
    Blank = 0x00
    TechPreConfigured = 0x01
    TagPreConfigured = 0x02
    SettingsPreConfigured = 0x04
    TechAndTagPreConfigured = TechPreConfigured | TagPreConfigured
    TechAndSettingsPreConfigured = TechPreConfigured | SettingsPreConfigured
    TagAndSettingsPreConfigured = TagPreConfigured | SettingsPreConfigured
    AllPreConfigured = TechPreConfigured | TagPreConfigured | SettingsPreConfigured
    Running = 0x40
    Finished = 0x80


class LeakTestLogStatus(IntEnum):
    Blank = 0x00
    TechPreConfigured = 0x01
    TagPreConfigured = 0x02
    AllPreConfigured = TechPreConfigured | TagPreConfigured
    Running = 0x40
    Finished = 0x80


class LeakTestRecipeStatus(IntEnum):
    Blank = 0x00
    Configured = 0x01
    Selected = 0x02


class PressureSensorType(IntEnum):
    Gauge = 0x00
    SealedGauge = 0x01
    Absolute = 0x02
    Auxiliary = 0x03
    Differential = 0x04


# TODO: Casas decimais e não classe de exatidão
class SensorPrecision(IntEnum):
    A4 = 0x00
    A5 = 0x01
    A6 = 0x02


class SensorAccuracyClass(IntEnumFmt):
    zero = 0x00, "1", 1.0
    one = 0x01, "0.1", 1.0
    two = 0x02, "0.01", 1.0
    three = 0x03, "0.001", 1.0
    four = 0x04, "0.0001", 1.0
    five = 0x05, "0.00001", 1.0


class AppLockMode(IntEnum):
    Off = 0x00
    Full = 0xFF


class AutoOffMode(IntEnum):
    Deactivated = 0x00
    Auto30Min = 0x01
    Auto60Min = 0x02
    Auto90Min = 0x03
    Auto120Min = 0x04


class MemoryResetLevel(IntEnum):
    Total = 0x00
    Factory = 0x01
    Sensor = 0x02


class ZeroProtectionMode(IntEnum):
    On = 0x00
    Off = 0x01


class PressureUnit(IntEnumFmt):
    bar = (0x00, "bar", 1.0)
    mbar = (0x01, "mbar", 1e3)
    psi = (0x02, "psi", 14.50377)
    MPa = (0x03, "MPa", 0.1)
    kPa = (0x04, "kPa", 100.0)
    hPa = (0x05, "hPa", 1e3)
    Pa = (0x06, "Pa", 1e5)
    kgf_m2 = (0x07, "kgf/m2", 1.0)
    kgf_cm2 = (0x08, "kgf/cm2", 1.0)
    gf_cm2 = (0x09, "gf/cm2", 1.0)
    psf = (0x0A, "psf", 1.0)
    mHg = (0x0B, "mHg", 1.0)
    inHg = (0x0C, "inHg", 1.0)
    cmHg = (0x0D, "cmHg", 1.0)
    mmHg = (0x0E, "mmHg", 1.0)
    mH2O_at_4_deg_Celsius = (0x0F, "mH2O@4C", 1.0)
    ftH2O_at_4_deg_Celsius = (0x10, "ftH2O@4C", 1.0)
    inH2O_at_4_deg_Celsius = (0x11, "inH2O@4C", 1.0)
    cmH2O_at_4_deg_Celsius = (0x12, "cmH2O@4C", 1.0)
    mmH2O_at_4_deg_Celsius = (0x13, "mmH2O@4C", 1.0)
    mH2O_at_20_deg_Celsius = (0x14, "mH2O@20C", 1.0)
    ftH2O_at_20_deg_Celsius = (0x15, "ftH2O@20C", 1.0)
    inH2O_at_20_deg_Celsius = (0x16, "inH2O@20C", 1.0)
    cmH2O_at_20_deg_Celsius = (0x17, "cmH2O@20C", 1.0)
    mmH2O_at_20_deg_Celsius = (0x18, "mmH2O@20C", 1.0)
    torr = (0x19, "torr", 1.0)
    atm = (0x1A, "atm", 1.0)


class TemperatureUnit(IntEnumFmt):
    deg_Celsius = 0x00, "°C"
    deg_Fahrenheit = 0x01, "°F"
    Kelvin = 0x02, "K"


class TimeUnit(IntEnumFmt):
    millisecond = 0x00, "ms", 1000.0
    second = 0x01, "s", 1.0
    minute = 0x02, "min", 1.0 / 60.0
    hour = 0x03, "h", 1.0 / 3600.0
    day = 0x04, "d", 1.0 / 86400.0


class ResponseCode(IntEnum):
    Ok = 0x00
    Error = 0x80
    CommandNotImplemented = Error | 0x01
    TooFewBytes = Error | 0x02
    TooManyBytes = Error | 0x04
    InvalidData = Error | 0x08
    InvalidState = Error | 0x10
    Busy = Error | 0x40
    Undefined = 0xFF


def get_symbol(enum, value):
    try:
        obj = enum(value)
        return getattr(obj, "symbol", obj.name)
    except ValueError:
        return f"0x{value:02X}"


def get_factor(enum, value):
    try:
        return enum(value).factor
    except (ValueError, AttributeError):
        return 1.0


class CommandCodec:
    """Base protocol packet encoder/decoder with a smart byte-packing fallback."""

    def encode(self, args: list) -> bytes:
        if args:
            try:
                return bytes(int(x, 0) for x in args)
            except ValueError:
                raise ValueError(
                    "Unregistered command requires integer/hex byte arguments."
                )
        return b""

    def decode(self, data: bytes) -> str:
        return data.hex(" ") if data else "OK"


class StructCodec(CommandCodec):
    """A generic decoder for C# numeric data structures."""

    def __init__(self, format_string, display_names, extra_format=None):
        self.format_string = format_string
        self.display_names = display_names
        self.size = struct.calcsize(format_string)
        self.extra_format = extra_format

    def encode(self, args):
        # Safety net: Read commands that map to structs take 0 arguments.
        # Ignore any accidental CLI arguments instead of sending garbage bytes.
        return b""

    def decode(self, data):
        if len(data) < self.size:
            return f"Raw: {data.hex(' ')}"

        unpacked = struct.unpack(self.format_string, data[: self.size])

        if self.extra_format is not None and len(unpacked) == 1:
            val = unpacked[0]
            translated_val = get_symbol(self.extra_format, val)
            return f"{self.display_names[0]}: {translated_val}"

        return " | ".join(
            f"{name}: {val}" for name, val in zip(self.display_names, unpacked)
        )


class StringCodec(CommandCodec):
    """Decodes null-terminated ASCII strings of a specific length."""

    def __init__(self, length):
        self.length = length

    def decode(self, data):
        if len(data) >= self.length:
            return read_ascii(data, 0, self.length)
        return f"Raw: {data.hex(' ')}"


class StringWithIndexCodec(CommandCodec):
    """Decodes a 1-byte index followed by a null-terminated string."""

    def __init__(self, str_length):
        self.str_length = str_length

    def encode(self, args):
        # Transmit the 1-byte log number index
        return struct.pack("<B", int(args[0], 0)) if args else b"\x00"

    def decode(self, data):
        if len(data) >= self.str_length + 1:
            idx = data[0]
            text = read_ascii(data, 1, self.str_length)
            return f"Index {idx}: '{text}'"
        return f"Raw: {data.hex(' ')}"


class EmptyCodec(CommandCodec):
    """For commands that return 0 data bytes upon success."""

    def decode(self, data):
        return "Success (0 bytes)"


class ReadLogHeaderCodec(CommandCodec):
    """
    Decodes the 81-byte ReadLogHeader response.
    Format: 1-byte log index + 80-byte header structure.
    """

    def encode(self, args):
        # Transmit the 1-byte log number index (e.g., --read-log-header 1)
        return struct.pack("<B", int(args[0], 0)) if args else b"\x00"

    def decode(self, data):
        if len(data) >= 81:
            fmt = "<B B 3x I I B B B x B B B x I f f f i B B B B B 3x 16s 16s"
            unpacked = struct.unpack_from(fmt, data, 0)

            log_idx, status, num_records, mem_offset = unpacked[0:4]
            y_off, mo, d, h, m, s, ms = unpacked[4:11]
            interval, t_set, t_reset, max_samp = unpacked[11:15]
            mode, p_meas, t_meas, p_unit, t_unit = unpacked[15:20]
            tech = read_ascii(unpacked[20], 0, 16)
            tag = read_ascii(unpacked[21], 0, 16)

            # Map raw hex to human-readable strings
            status_string = get_symbol(LogStatus, status)
            mode_str = get_symbol(LogMode, mode)
            p_meas_str = get_symbol(LogMeasurement, p_meas)
            t_meas_str = get_symbol(LogMeasurement, t_meas)
            p_unit_str = get_symbol(PressureUnit, p_unit)
            t_unit_str = get_symbol(TemperatureUnit, t_unit)

            return (
                f"--- Log Header {log_idx} ---\n"
                f"Status: {status_string} | Records: {num_records} | Mem Offset: {mem_offset}\n"
                f"Start Date: 20{y_off:02d}-{mo:02d}-{d:02d} {h:02d}:{m:02d}:{s:02d}.{ms:03d}\n"
                f"Settings -> Interval: {interval}s | Max Samples: {max_samp}\n"
                f"Trip Set: {t_set:.2f} | Trip Reset: {t_reset:.2f}\n"
                f"Mode: {mode_str} | Measurement(P/T): {p_meas_str}/{t_meas_str} | Units(P/T): {p_unit_str}/{t_unit_str}\n"
                f"Technician: '{tech}' | Tag: '{tag}'"
            )
        return f"Raw: {data.hex(' ')}"


class LoggerSettingsCodec(CommandCodec):
    """Decodes the 24-byte Logger Settings structure."""

    def encode(self, args):
        return b""

    def decode(self, data):
        if len(data) >= 24:
            # Format: 3 floats (12), 1 int (4), 5 bytes (5), 3 pad bytes (3) = 24 bytes
            interval, t_set, t_reset, max_samp, mode, p_meas, t_meas, p_unit, t_unit = (
                struct.unpack_from("<fff i BBBBB 3x", data, 0)
            )

            # Map raw hex to human-readable strings
            mode_str = get_symbol(LogMode, mode)
            p_meas_str = get_symbol(LogMeasurement, p_meas)
            t_meas_str = get_symbol(LogMeasurement, t_meas)
            p_unit_str = get_symbol(PressureUnit, p_unit)
            t_unit_str = get_symbol(TemperatureUnit, t_unit)

            return (
                f"Interval: {interval}s | Max Samples: {max_samp} | Mode: {mode_str}\n"
                f"Trip Setpoint: {t_set:.2f} | Trip Reset: {t_reset:.2f}\n"
                f"Units         -> Pressure: {p_unit_str} | Temperature: {t_unit_str}\n"
                f"Measurement   -> Pressure: {p_meas_str} | Temperature: {t_meas_str}"
            )
        return f"Raw: {data.hex(' ')}"


class ReadLeakTestLogCodec(CommandCodec):
    """
    Decodes the 69-byte ReadLeakTestLog response.
    Format: 1-byte log index + 68-byte leak test log structure.
    """

    def encode(self, args):
        # Transmit the 1-byte log number index (e.g., --read-leak-test-log 1)
        if not args:
            raise ValueError("ReadLeakTestLog requires 1 argument: <LogNumber>")
        return struct.pack("<B", int(args[0], 0))

    def decode(self, data):
        if len(data) >= 69:
            log_idx = data[0]

            # Format breakdown for the 68-byte struct:
            # B B b x       -> status (1), base_p_unit (1), base_p_prec (1), padding (1)
            # B B B x       -> year_offset, month, day (3), padding (1)
            # B B B x       -> hour, minute, second (3), padding (1)
            # I             -> ms (4)
            # f f           -> base_p_start (4), base_p_end (4)
            # i             -> run_time_s (4)
            # B B B x       -> set_p_unit (1), set_t_unit (1), leak_prec (1), padding (1)
            # f             -> max_leak_rate (4)
            # 16s 16s       -> technician (16), tag (16)

            fmt = "<B B b x B B B x B B B x I f f i B B B x f 16s 16s"
            unpacked = struct.unpack_from(fmt, data, 1)

            status, p_unit, precision = unpacked[0:3]
            y_off, mo, d, h, m, s, ms = unpacked[3:10]
            p_start, p_end, run_time = unpacked[10:13]
            s_p_unit, s_t_unit, leak_precision, max_leak = unpacked[13:17]

            # Decode strings, stripping trailing null terminators
            tech = read_ascii(unpacked[17], 0, 16)
            tag = read_ascii(unpacked[18], 0, 16)

            p_unit_str = get_symbol(PressureUnit, p_unit)
            s_p_unit_str = get_symbol(PressureUnit, s_p_unit)
            s_t_unit_str = get_symbol(TimeUnit, s_t_unit)
            status_string = get_symbol(LogStatus, status)
            precision_str = get_symbol(SensorAccuracyClass, precision)
            leak_precision_str = get_symbol(SensorAccuracyClass, leak_precision)

            return (
                f"--- Leak Test Log {log_idx} ---\n"
                f"Status: {status_string} | Date: 20{y_off:02d}-{mo:02d}-{d:02d} {h:02d}:{m:02d}:{s:02d}.{ms:03d}\n"
                f"Test Pressures -> Start: {p_start:.4f} | End: {p_end:.4f} (Unit: {p_unit_str}, Precision: {precision_str})\n"
                f"Settings       -> Runtime: {run_time}s | Max Leak Rate: {max_leak:.4f}\n"
                f"Rate Units     -> Press: {s_p_unit_str} | Time: {s_t_unit_str} | Precision: {leak_precision_str}\n"
                f"Technician: '{tech}' | Tag: '{tag}'"
            )
        return f"Raw: {data.hex(' ')}"


class ReadLeakTestRecipeCodec(CommandCodec):
    """
    Decodes the 49-byte ReadLeakTestRecipe response.
    Format: 1-byte recipe index + 48-byte leak test recipe structure.
    """

    def encode(self, args):
        # Transmit the 1-byte recipe number index (e.g., --read-leak-test-recipe 1)
        if not args:
            raise ValueError("ReadLeakTestRecipe requires 1 argument: <RecipeNumber>")
        return struct.pack("<B", int(args[0], 0))

    def decode(self, data):
        if len(data) >= 49:
            fmt = "<B B 3x i B B B x f 8s 24s"

            unpacked = struct.unpack_from(fmt, data, 0)

            # Map the unpacked tuple to variables
            recipe_idx = unpacked[0]
            status = unpacked[1]
            run_time = unpacked[2]
            p_unit = unpacked[3]
            t_unit = unpacked[4]
            precision = unpacked[5]
            max_leak = unpacked[6]

            tag = read_ascii(unpacked[7], 0, 8)
            observation = read_ascii(unpacked[8], 0, 24)

            status_str = get_symbol(LogStatus, status)
            p_unit_str = get_symbol(PressureUnit, p_unit)
            t_unit_str = get_symbol(TimeUnit, t_unit)
            precion_str = get_symbol(SensorAccuracyClass, precision)

            return (
                f"--- Leak Test Recipe {recipe_idx} ---\n"
                f"Status: {status_str} | Runtime: {run_time}s\n"
                f"Units   -> Pressure: {p_unit_str} | Time Unit: {t_unit_str} | Precision: {precion_str}\n"
                f"Max Leak Rate: {max_leak:.4f}\n"
                f"Tag     : '{tag}'\n"
                f"Obs     : '{observation}'"
            )
        return f"Raw: {data.hex(' ')}"


class LeakTestSettingsCodec(CommandCodec):
    """Decodes the 12-byte Leak Test Settings structure."""

    def encode(self, args):
        return b""

    def decode(self, data):
        if len(data) >= 12:
            fmt = "<i B B b B f"
            unpack = struct.unpack_from(fmt, data, 0)

            # '_' is padding
            run_time, p_unit, t_unit, precision, _, max_rate = unpack

            p_unit_str = get_symbol(PressureUnit, p_unit)
            t_unit_str = get_symbol(TimeUnit, t_unit)
            precision_str = get_symbol(SensorAccuracyClass, precision)

            return (
                f"Run Time: {run_time}s | Max Rate: {max_rate:.3f}\n"
                f"Units -> Pressure: {p_unit_str} | Time: {t_unit_str} | Precision: {precision_str}"
            )
        return ""


class DeviceInfoCodec(CommandCodec):
    """Maps strictly to ReadDeviceInfoResponse (46 bytes)."""

    def decode(self, data):
        if len(data) >= 46:
            model = (
                data[0:16].replace(b"\x00", b"").decode("ascii", "ignore").strip()
            )  # TODO: read_ascii
            serial = (
                data[16:32].replace(b"\x00", b"").decode("ascii", "ignore").strip()
            )  # TODO: read_ascii
            fw_maj, fw_min, fw_pat = data[32], data[33], data[34]
            low, up = struct.unpack("<ff", data[35:43])
            type_id, unit, prec = struct.unpack("<BBb", data[43:46])

            precision_str = get_symbol(SensorAccuracyClass, prec)
            unit_str = get_symbol(PressureUnit, unit)

            return (
                f"Model: {model} | Serial Number: {serial} | "
                f"Firmware: {fw_maj}.{fw_min}.{fw_pat} | "
                f"Limits: {low:.2f} to {up:.2f} | "
                f"Base Unit: {unit_str} | Precision: {precision_str} | "
            )
        return ""


class ReadLogDataCodec(CommandCodec):
    """
    Packs a ReadLogData request and formats raw memory into a diagnostic table.
    CLI Arguments: <LogNumber> <Offset> <BytesToRead> [Optional: BytesPerSampleStride]
    """

    def __init__(self):
        self.stride = 0

    def encode(self, args):
        if not args or len(args) < 3:
            raise ValueError(
                "ReadLogData requires: <LogNumber> <Offset> <BytesToRead> [SampleStride]"
            )

        log_num = int(args[0], 0)
        offset = int(args[1], 0)
        bytes_to_read = int(args[2], 0)

        # Optional 4th arg to draw visual boundaries between samples
        if len(args) >= 4:
            self.stride = int(args[3], 0)
        else:
            self.stride = 0

        return struct.pack("<BIH", log_num, offset, bytes_to_read)

    def decode(self, data):
        if not data:
            return "ERR: NO DATA RECEIVED"

        out = [
            "=============================================================================",
            f" DATA MEMORY INSPECTOR                            TOTAL BYTES: {len(data):<4}",
            "=============================================================================",
            f" {'OFFSET':<8} | {'HEX (LE)':<11} | {'FLOAT32':<12} | {'UINT32':<10} | {'HEURISTIC'}",
            "----------+-------------+--------------+------------+------------------------",
        ]

        for i in range(0, len(data), 4):
            # Draw a horizontal line between samples if a stride was provided
            if self.stride > 0 and i > 0 and i % self.stride == 0:
                out.append(
                    "----------+-------------+--------------+------------+------------------------"
                )

            chunk = data[i : i + 4]
            hex_str = chunk.hex(" ")

            if len(chunk) == 4:
                val_f = struct.unpack("<f", chunk)[0]
                val_u = struct.unpack("<I", chunk)[0]

                # Heuristics for typical pneumatic/hydraulic sensor readings
                if val_u == 0:
                    heur = "ZERO / PADDING"
                    f_str = "0.0000"
                elif val_u < 10_000_000 and abs(val_f) < 1e-10:
                    heur = "TRIP INDEX"
                    f_str = f"{val_f:.2e}"
                elif -100.0 <= val_f <= 10000.0:
                    # Realistic limits for industrial pressure/temp sensors
                    heur = "SENSOR FLOAT"
                    f_str = f"{val_f:.4f}"
                else:
                    heur = "NOISE / RAW"
                    f_str = f"{val_f:.2e}"

                out.append(
                    f" +{i:<7} | {hex_str:<11} | {f_str:<12} | {val_u:<10} | {heur}"
                )
            else:
                out.append(
                    f" +{i:<7} | {hex_str:<11} | {'-':<12} | {'-':<10} | INCOMPLETE"
                )

        out.append(
            "============================================================================="
        )
        return "\n".join(out)


class PressureLinearizationCodec(CommandCodec):
    """
    Decodes the 248-byte Pressure Linearization Table.
    Format: 1 int32 + 20 float32 (gains) + 20 float32 (zeros) + 21 float32 (measured).
    """

    def encode(self, args):
        # 0-argument request
        return b""

    def decode(self, data):
        if len(data) >= 248:
            # <i (1 int) + 20f (20 floats) + 20f (20 floats) + 21f (21 floats) = 248 bytes
            fmt = "<i 20f 20f 21f"
            unpacked = struct.unpack(fmt, data[:248])

            num_points = unpacked[0]
            gains = unpacked[1:21]
            zeros = unpacked[21:41]
            measured_values = unpacked[41:62]

            out = [
                "===================================================================",
                f" PRESSURE LINEARIZATION TABLE                  VALID POINTS: {num_points:<2}",
                "===================================================================",
                " IDX | MEASURED VALUE   | GAIN             | ZERO OFFSET",
                "-----+------------------+------------------+-----------------------",
            ]

            # Loop through the 21 measured points (which have 20 interstitial gains/zeros)
            for i in range(21):
                meas = f"{measured_values[i]:.6f}"

                # The final measured value (index 20) bounds the curve and has no subsequent gain/zero
                if i < 20:
                    gain = f"{gains[i]:.6f}"
                    zero = f"{zeros[i]:.6f}"
                    out.append(f" {i:02d}  | {meas:<16} | {gain:<16} | {zero}")
                else:
                    out.append(f" {i:02d}  | {meas:<16} | {'-':<16} | -")

            out.append(
                "==================================================================="
            )
            return "\n".join(out)

        return f"Raw: {data.hex(' ')}"


class CalibrationCertificateCodec(CommandCodec):
    """Decodes the 52-byte Calibration Certificate info."""

    def encode(self, args):
        return b""

    def decode(self, data):
        if len(data) >= 52:
            code = data[:16].replace(b"\x00", b"").decode("ascii", "ignore").strip()
            link = data[16:48].replace(b"\x00", b"").decode("ascii", "ignore").strip()
            y_offset, m, d, _ = struct.unpack("<BBBB", data[48:52])
            return f"Code: '{code}'\nLink: '{link}'\nDate: 20{y_offset:02d}-{m:02d}-{d:02d}"
        return ""


COMMAND_REGISTRY = {
    # --- HARDWARE & SYSTEM INFO ---
    CommandNumber.ReadDeviceInfo: DeviceInfoCodec(),
    CommandNumber.ReadCalibrationCertificateInfo: CalibrationCertificateCodec(),
    CommandNumber.ReadBatteryInfo: StructCodec("<ff", ["SOC (%)", "SOC (Volts)"]),
    CommandNumber.ReadSerialNumber: StructCodec(
        "<BBHI", ["Year Offset", "Month", "Order", "Sequence"]
    ),
    # --- LOGGER INFO ---
    CommandNumber.ReadLoggerInfo: StructCodec(
        "<BBII", ["Saved Logs", "Free Logs", "Saved Records", "Free Records"]
    ),
    CommandNumber.ReadLeakTestLoggerInfo: StructCodec(
        "<BB", ["Saved Logs", "Free Logs"]
    ),
    CommandNumber.ReadLeakTestLog: ReadLeakTestLogCodec(),
    CommandNumber.ReadLogData: ReadLogDataCodec(),
    # --- SENSORS & MEASUREMENTS ---
    # CommandNumber.ReadPressureAndTemperature: StructCodec(
    #     "<ff", ["Pressure", "Temperature"]
    # ),
    CommandNumber.ReadTrueSensorLimits: StructCodec(
        "<ff", ["True Lower Limit", "True Upper Limit"]
    ),
    # CommandNumber.ReadUnlinearizedPressure: StructCodec(
    #     "<f", ["Unlinearized Pressure"]
    # ),
    # CommandNumber.ReadUntrimmedPressure: StructCodec("<f", ["Untrimmed Pressure"]),
    CommandNumber.ReadPressureUnit: StructCodec("<B", ["Pressure Unit"], PressureUnit),
    CommandNumber.ReadTemperatureUnit: StructCodec(
        "<B", ["Temperature Unit"], TemperatureUnit
    ),
    CommandNumber.ReadTrueSensorType: StructCodec(
        "<B", ["Sensor Type"], PressureSensorType
    ),
    CommandNumber.ReadSensorAccuracyClass: StructCodec(
        "<B", ["Accuracy Class"], SensorPrecision
    ),
    # --- REAL-TIME CLOCK (RTC) ---
    CommandNumber.ReadClockDate: StructCodec("<BBBx", ["Year Offset", "Month", "Day"]),
    CommandNumber.ReadClockTime: StructCodec(
        "<BBBxI", ["Hour", "Minute", "Second", "Millisecond"]
    ),
    # --- CONFIGURATION & SETTINGS ---
    CommandNumber.ReadLoggerSettings: LoggerSettingsCodec(),
    CommandNumber.ReadLeakTestSettings: LeakTestSettingsCodec(),
    CommandNumber.ReadLeakTestRecipe: ReadLeakTestRecipeCodec(),
    CommandNumber.ReadAutoOffMode: StructCodec("<B", ["Auto-Off Mode"], AutoOffMode),
    CommandNumber.ReadBrightness: StructCodec("<B", ["Brightness Level (%)"]),
    CommandNumber.ReadLockMode: StructCodec("<B", ["App Lock Mode"], AppLockMode),
    CommandNumber.ReadZeroProtectionMode: StructCodec(
        "<B", ["Zero Protection Mode"], ZeroProtectionMode
    ),
    CommandNumber.ReadSelectedLeakTestRecipeNumber: StructCodec(
        "<B", ["Selected Recipe Number"]
    ),
    # --- STRINGS & TAGS ---
    CommandNumber.ReadPressureTrimParameters: StructCodec("<ff", ["Gain", "Offset"]),
    CommandNumber.ReadLogTag: StringWithIndexCodec(16),
    CommandNumber.ReadLogHeader: ReadLogHeaderCodec(),
    CommandNumber.ReadLogTechnician: StringWithIndexCodec(16),
    CommandNumber.ReadLeakTestLogTag: StringWithIndexCodec(16),
    CommandNumber.ReadLeakTestLogTechnician: StringWithIndexCodec(16),
    # --- EMPTY RESPONSES (Actions & Clears) ---
    CommandNumber.ClearAllLeakTestRecipes: EmptyCodec(),
    CommandNumber.ClearLeakTestLogs: EmptyCodec(),
    CommandNumber.ClearLogs: EmptyCodec(),
    # CommandNumber.ResetMemory: EmptyCodec(),
    CommandNumber.ResetPressureZero: EmptyCodec(),
    CommandNumber.ResetTemperatureReference: EmptyCodec(),
    CommandNumber.SetPressureZero: EmptyCodec(),
    CommandNumber.SetTemperatureReference: EmptyCodec(),
    CommandNumber.UnselectLeakTestRecipe: EmptyCodec(),
    # --- Not implemented / factory only API ---
    # CommandNumber.ReadPressureLinearization: PressureLinearizationCodec(),
    # CommandNumber.SelectLeakTestRecipe: SingleByteCodec(),
    # CommandNumber.WriteLockMode: SingleByteCodec(),
    # CommandNumber.WriteZeroProtectionMode: SingleByteCodec(),
    # CommandNumber.WriteSensorType: SingleByteCodec(),
    # CommandNumber.WriteSensorAccuracyClass: SingleByteCodec(),
    # CommandNumber.ResetMemory: SingleByteCodec(),
    # CommandNumber.WriteSerialNumber: SerialNumberCodec(),
    # CommandNumber.WriteClockDate: RtcDateCodec(),
    # CommandNumber.WriteLogTechnician: LogStringCodec(),
    # CommandNumber.WriteLogTag: LogStringCodec(),
    # CommandNumber.WriteLeakTestLogTechnician: LogStringCodec(),
    # CommandNumber.WriteLeakTestLogTag: LogStringCodec(),
    # CommandNumber.WriteSensorLimits: DualFloatCodec(),
    # CommandNumber.WriteAutoOffMode: SingleByteCodec(),
    # CommandNumber.WriteBrightness: SingleByteCodec(),
    # CommandNumber.WritePressureUnit: SingleByteCodec(),
    # CommandNumber.WriteTemperatureUnit: SingleByteCodec(),
    # CommandNumber.ClearLeakTestRecipe: SingleByteCodec(),
}

# ==========================================
# 1.1. LOAD LOG CONFIG AND HELPERS
# ==========================================


def pack_led_state(blue: int, red: int) -> bytes:
    return struct.pack("<2B", blue, red)  # 2 bytes: blue, red


def set_led(ser, blue, red, retries=2):
    for _ in range(retries + 1):
        if ser.send_write(CommandNumber.SetLEDState, pack_led_state(blue, red)):
            return True
        time.sleep(0.2)
    return False


def blink_red(ser, times: int = 3, on_s: float = 0.3, off_s: float = 0.3) -> bool:
    """Blocking: blink the red LED, then guarantee it ends OFF. Returns success."""
    ok = True
    try:
        for _ in range(times):
            if not ser.send_write(CommandNumber.SetLEDState, pack_led_state(0, 1)):
                ok = False
                break
            time.sleep(on_s)
            if not ser.send_write(CommandNumber.SetLEDState, pack_led_state(0, 0)):
                ok = False
                break
            time.sleep(off_s)
    finally:
        # Always leave the LED off, even on exception
        ser.send_write(CommandNumber.SetLEDState, pack_led_state(0, 0))
    return ok


def load_config(path=CONFIG_PATH):
    try:
        with open(path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
    except FileNotFoundError:
        print(f"Config {path} not found, using defaults")
        return None, dict(DEFAULT_CONFIG)
    except json.JSONDecodeError as e:
        print(f"Config parse error: {e}, keeping defaults")
        return None, dict(DEFAULT_CONFIG)
    # merge over defaults so missing keys don't KeyError
    merged = {**DEFAULT_CONFIG, **loaded}
    return loaded, merged  # `loaded` non-None = "file changed, push to device"


def pack_indexed_string(log_index, text, length):
    """1-byte index + fixed-length null-terminated string field."""
    raw = text.encode("ascii", "ignore")[:length].ljust(length, b"\x00")
    return struct.pack("<B", int(log_index)) + raw


def pack_log_technician(cfg):
    return pack_indexed_string(cfg["log_index"], cfg["technician"], 16)


def pack_log_tag(cfg):
    return pack_indexed_string(cfg["log_index"], cfg["tag"], 16)


def pack_leak_test_log_technician(cfg):
    return pack_indexed_string(cfg["log_index"], cfg["technician"], 16)


def pack_leak_test_log_tag(cfg):
    return pack_indexed_string(cfg["log_index"], cfg["tag"], 16)


DATALOG_SETTINGS_FMT = "<3fi5B3x"


def pack_logger_settings(cfg):
    return struct.pack(
        DATALOG_SETTINGS_FMT,
        float(cfg["interval_s"]),
        float(cfg["trip_set"]),
        float(cfg["trip_reset"]),
        int(cfg["max_samples"]),
        int(cfg["log_mode"]),
        int(cfg["pressure_measurement"]),
        int(cfg["temperature_measurement"]),
        int(cfg["pressure_unit"]),
        int(cfg["temperature_unit"]),
    )


# leak_test_settings_t: int32, 3 enums, pad, float = 12 bytes
LEAK_SETTINGS_FMT = "<i3Bx f"


def pack_leak_test_settings(cfg):
    return struct.pack(
        LEAK_SETTINGS_FMT,
        int(cfg["run_time_in_s"]),
        int(cfg["pressure_unit"]),
        int(cfg["time_unit"]),
        int(cfg["leak_rate_precision"]),
        float(cfg["max_leak_rate"]),
    )


# leak_test_recipe_t: status, pad3, nested settings(12), tag 8s, obs 24s = 48 bytes
LEAK_RECIPE_FMT = "<B3x i3Bx f 8s 24s"


def pack_leak_test_recipe(cfg):
    s = cfg["settings"]
    return struct.pack(
        LEAK_RECIPE_FMT,
        int(cfg["status"]),
        int(s["run_time_in_s"]),
        int(s["pressure_unit"]),
        int(s["time_unit"]),
        int(s["leak_rate_precision"]),
        float(s["max_leak_rate"]),
        cfg["tag"].encode("ascii", "ignore")[:8],
        cfg["observation"].encode("ascii", "ignore")[:24],
    )


def push_config(ser, cfg) -> bool:
    ok = True

    # --- Erase phase ---
    if ser.send_write(CommandNumber.ClearLogs):
        log("Config: datalogs erased", prefix="CONFIG")
    else:
        log("Config: ClearLogs FAILED", prefix="CONFIG")
        ok = False
    if ser.send_write(CommandNumber.ClearLeakTestLogs):
        log("Config: leak test logs erased", prefix="CONFIG")
    else:
        log("Config: ClearLeakTestLogs FAILED", prefix="CONFIG")
        ok = False
    if ser.send_write(CommandNumber.ClearAllLeakTestRecipes):
        log("Config: leak test recipes erased", prefix="CONFIG")
    else:
        log("Config: ClearAllLeakTestRecipes FAILED", prefix="CONFIG")
        ok = False

    # --- Write phase ---
    steps = [
        (
            CommandNumber.WriteLoggerSettings,
            pack_logger_settings(cfg["logger_settings"]),
            "logger settings",
        ),
        (
            CommandNumber.WriteLogTechnician,
            pack_log_technician(cfg["data_log_identity"]),
            "log technician",
        ),
        (CommandNumber.WriteLogTag, pack_log_tag(cfg["data_log_identity"]), "log tag"),
        (
            CommandNumber.WriteLeakTestSettings,
            pack_leak_test_settings(cfg["leak_test_settings"]),
            "leak settings",
        ),
        (
            CommandNumber.WriteLeakTestLogTechnician,
            pack_leak_test_log_technician(cfg["leak_test_identity"]),
            "leak technician",
        ),
        (
            CommandNumber.WriteLeakTestLogTag,
            pack_leak_test_log_tag(cfg["leak_test_identity"]),
            "leak tag",
        ),
    ]
    for cmd, payload, label in steps:
        if ser.send_write(cmd, payload):
            log(f"Config: {label} applied", prefix="CONFIG")
        else:
            ok = False
    if ok:
        log("Config: all settings applied to device", prefix="CONFIG")
    return ok


def get_layout_info(mode, p_meas, t_meas):
    """
    Returns (layout_id, records_per_sample) using a strict, collision-free lookup table.
    """
    is_trip = mode in (LogMode.TripHigh, LogMode.TripLow)
    p_is_mmm = p_meas == LogMeasurement.MaxMinMean

    # Compress Temperature measurement into 3 explicit states: 0 (Blank), 2 (MaxMinMean), 1 (Single)
    t_state = (
        0
        if t_meas == LogMeasurement.Blank
        else (2 if t_meas == LogMeasurement.MaxMinMean else 1)
    )

    # Key: (is_trip, p_is_mmm, t_state) -> Value: (layout_id, records_per_sample)
    layout_table = {
        (False, False, 0): (1, 1),
        (False, True, 0): (2, 3),
        (False, False, 1): (3, 2),
        (False, True, 1): (4, 4),
        (False, False, 2): (5, 4),
        (False, True, 2): (6, 6),
        (True, False, 0): (7, 2),
        (True, True, 0): (8, 4),
        (True, False, 1): (9, 3),
        (True, True, 1): (10, 5),
        (True, False, 2): (11, 5),
        (True, True, 2): (12, 7),
    }

    return layout_table.get((is_trip, p_is_mmm, t_state), (None, None))


# ==========================================
# 2. NORMALIZED DATABASE MANAGEMENT
# ==========================================

SQLITE_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS devices (
        serial_number TEXT PRIMARY KEY,
        model TEXT,
        firmware_version TEXT,
        lower_pressure_range REAL,
        upper_pressure_range REAL,
        pressure_range_unit TEXT,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        serial_number TEXT,
        log_type TEXT, 
        log_number INTEGER,
        tag TEXT,
        technician TEXT,
        start_date TEXT,
        end_date TEXT,
        
        -- Standard Log Specific (NULL for Leak Tests)
        log_layout_id INTEGER,
        number_of_samples INTEGER,
        log_mode TEXT,
        interval_seconds REAL,
        max_samples INTEGER,
        pressure_unit TEXT,
        temperature_unit TEXT,
        trip_setpoint REAL,
        trip_reset REAL,
        
        -- Leak Test Specific (NULL for Standard Logs)
        run_time_seconds INTEGER,
        start_pressure REAL,
        end_pressure REAL,
        pressure_change REAL,
        base_pressure_unit TEXT,
        average_leak_rate REAL,
        max_accepted_leak_rate REAL,
        leak_rate_unit TEXT,
        test_result TEXT,
        
        FOREIGN KEY(serial_number) REFERENCES devices(serial_number),
        UNIQUE(serial_number, log_type, log_number, start_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS log_samples (
        id INTEGER PRIMARY KEY,
        log_id INTEGER,
        timestamp TEXT,
        end_pressure REAL,
        maximum_pressure REAL,
        mean_pressure REAL,
        minimum_pressure REAL,
        end_temperature REAL,
        maximum_temperature REAL,
        mean_temperature REAL,
        minimum_temperature REAL,
        FOREIGN KEY(log_id) REFERENCES logs(id) ON DELETE CASCADE
    )
    """,
]


def init_db():
    """Provisions a relational schema strictly mapping the hardware memory layout."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(SQLITE_SCHEMA[0])

    # Unified table for BOTH Standard Logs and Leak Tests
    cursor.execute(SQLITE_SCHEMA[1])
    cursor.execute(SQLITE_SCHEMA[2])

    conn.commit()
    conn.close()


def db_connect():
    """Opens the DB and guarantees the schema exists (self-heals after deletion)."""
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SQLITE_SCHEMA[0])
    conn.executescript(SQLITE_SCHEMA[1])
    conn.executescript(SQLITE_SCHEMA[2])
    conn.commit()
    return conn


def parse_rtc_datetime(buffer, offset):
    """Parses 12-byte RtcDateTime (RtcDate[4] + RtcTime[8])."""
    yr = buffer[offset] + 2000
    mo = buffer[offset + 1]
    da = buffer[offset + 2]
    hr = buffer[offset + 4]
    mi = buffer[offset + 5]
    se = buffer[offset + 6]
    # Milliseconds at offset+8
    try:
        return datetime(yr, mo, da, hr, mi, se)
    except ValueError:
        return datetime(2000, 1, 1)


# ==========================================
# 3. PAYLOAD PARSERS
# ==========================================


def parse_and_store_leak_test(serial_number, log_number, leak_bytes):
    """Decodes 68-byte LeakTestLog and evaluates pass/fail criteria."""
    if len(leak_bytes) < 68:
        return

    status = leak_bytes[0]
    if status != LeakTestLogStatus.Finished:
        return  # Only save finished tests

    base_p_unit = leak_bytes[1]
    start_dt = parse_rtc_datetime(leak_bytes, 4)
    start_date_str = start_dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    base_start_p = read_float(leak_bytes, 16)
    base_end_p = read_float(leak_bytes, 20)

    # LeakTestSettings (Offset 24)
    run_time_sec = read_i32(leak_bytes, 24)
    leak_p_unit = leak_bytes[28]
    leak_t_unit = leak_bytes[29]
    max_leak_rate = read_float(leak_bytes, 32)

    tech = read_ascii(leak_bytes, 36, 16)
    tag = read_ascii(leak_bytes, 52, 16)

    # Convert base measurements (Bar) to Target Units
    p_factor = get_factor(PressureUnit, leak_p_unit)
    t_factor = get_factor(TimeUnit, leak_t_unit)

    start_p = base_start_p * p_factor
    end_p = base_end_p * p_factor
    p_change = end_p - start_p

    avg_leak_sec = (
        -(base_end_p - base_start_p) / run_time_sec if run_time_sec > 0 else 0
    )
    avg_leak_rate = (avg_leak_sec * p_factor) * t_factor

    result_str = "APPROVED" if avg_leak_rate <= max_leak_rate else "REPROVED"
    end_dt = start_dt + timedelta(seconds=run_time_sec)

    # Map the base pressure unit using the enum
    base_p_unit_str = get_symbol(PressureUnit, base_p_unit)

    base_p_unit_str = get_symbol(PressureUnit, base_p_unit)
    leak_p_unit_str = get_symbol(PressureUnit, leak_p_unit)
    leak_t_unit_str = get_symbol(TimeUnit, leak_t_unit)

    conn = db_connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT OR IGNORE INTO logs 
        (serial_number, log_type, log_number, tag, technician, start_date, end_date, run_time_seconds,
         start_pressure, end_pressure, pressure_change, pressure_unit, base_pressure_unit,
         average_leak_rate, max_accepted_leak_rate, leak_rate_unit, test_result)
        VALUES (?, 'Leak Test', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
        (
            serial_number,
            log_number,
            tag,
            tech,
            start_date_str,
            end_dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            run_time_sec,
            start_p,
            end_p,
            p_change,
            leak_p_unit_str,
            base_p_unit_str,
            avg_leak_rate,
            max_leak_rate,
            f"{leak_p_unit_str}/{leak_t_unit_str}",
            result_str,
        ),
    )

    if cursor.rowcount > 0:
        print(f"SUCCESS: Leak Test #{log_number} [{result_str}] stored.")
    conn.commit()
    conn.close()

    # in parse_and_store_leak_test
    log(
        f"Leak Test #{log_number}: {result_str} "
        f"(dP={p_change:+.4f} {leak_p_unit_str}, avg rate {avg_leak_rate:.5f} "
        f"{leak_p_unit_str}/{leak_t_unit_str}, limit {max_leak_rate})",
        prefix="STORE",
    )


def parse_and_store_standard_log(serial_number, log_number, header_bytes, data_bytes):
    """
    Dynamically parses LogHeader and processes samples based on the 12 Measurement layouts.
    """
    if len(header_bytes) < 80:
        return
    status = header_bytes[0]
    if status != LogStatus.Finished:
        return

    num_records = read_u32(header_bytes, 4)
    start_dt = parse_rtc_datetime(header_bytes, 12)
    start_date_str = start_dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    # Extract Settings (Offset 24)
    interval = read_float(header_bytes, 24)
    trip_set = read_float(header_bytes, 28)
    trip_reset = read_float(header_bytes, 32)
    max_samples = read_i32(header_bytes, 36)

    mode = header_bytes[40]
    p_meas = header_bytes[41]
    t_meas = header_bytes[42]
    p_unit = header_bytes[43]
    t_unit = header_bytes[44]

    tech = read_ascii(header_bytes, 48, 16)
    tag = read_ascii(header_bytes, 64, 16)

    # 1. RESOLVE EXACT LAYOUT ID AND EXPECTED SAMPLES
    layout_id, records_per_sample = get_layout_info(mode, p_meas, t_meas)

    if layout_id is None:
        print(
            f"Warning: Unknown log layout (mode={mode}, p={p_meas}, t={t_meas}). Skipped.",
            file=sys.stderr,
        )
        return

    # Define is_trip so the memory loop knows how to handle timestamps
    is_trip = mode in (LogMode.TripHigh, LogMode.TripLow)
    num_samples = num_records // records_per_sample
    end_dt = start_dt + timedelta(seconds=(interval * max(0, num_samples - 1)))

    conn = db_connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT OR IGNORE INTO logs 
        (serial_number, log_type, log_number, tag, technician, start_date, end_date, log_layout_id,
         number_of_samples, log_mode, interval_seconds, max_samples, pressure_unit, temperature_unit,
         trip_setpoint, trip_reset)
        VALUES (?, 'Standard', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
        (
            serial_number,
            log_number,
            tag,
            tech,
            start_date_str,
            end_dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            layout_id,
            num_samples,
            get_symbol(LogMode, mode),
            interval,
            max_samples,
            get_symbol(PressureUnit, p_unit),
            get_symbol(TemperatureUnit, t_unit),
            trip_set,
            trip_reset,
        ),
    )

    cursor.execute(
        "SELECT id FROM logs WHERE serial_number = ? AND log_type = 'Standard' AND log_number = ? AND start_date = ?",
        (serial_number, log_number, start_date_str),
    )
    row = cursor.fetchone()
    if not row:
        conn.close()
        return
    log_id = row[0]

    cursor.execute("DELETE FROM log_samples WHERE log_id = ?", (log_id,))

    offset = 0
    sample_idx = 0
    bytes_per_sample = 0
    if records_per_sample is not None:
        bytes_per_sample = records_per_sample * 4

    while offset + bytes_per_sample <= len(data_bytes):
        chunk_offset = offset

        # Initialize all variables to Python None (SQLite NULL)
        final_end_p = final_max_p = final_min_p = final_mean_p = None
        final_end_t = final_max_t = final_min_t = final_mean_t = None

        if is_trip:
            trip_idx = read_u32(data_bytes, chunk_offset)
            s_time = start_dt + timedelta(seconds=(interval * trip_idx))
            chunk_offset += 4
        else:
            s_time = start_dt + timedelta(seconds=(interval * sample_idx))

        # Pressure Layout Extraction
        if p_meas == LogMeasurement.MaxMinMean:
            final_max_p, final_min_p, final_mean_p = struct.unpack_from(
                "<3f", data_bytes, chunk_offset
            )
            chunk_offset += 12
        elif p_meas != LogMeasurement.Blank:
            val = read_float(data_bytes, chunk_offset)
            if p_meas == LogMeasurement.EndValue:
                final_end_p = val
            elif p_meas == LogMeasurement.Maximum:
                final_max_p = val
            elif p_meas == LogMeasurement.Minimum:
                final_min_p = val
            elif p_meas == LogMeasurement.Mean:
                final_mean_p = val
            chunk_offset += 4

        # Temperature Layout Extraction
        if t_meas == LogMeasurement.MaxMinMean:
            final_max_t, final_min_t, final_mean_t = struct.unpack_from(
                "<3f", data_bytes, chunk_offset
            )
            chunk_offset += 12
        elif t_meas != LogMeasurement.Blank:
            val = read_float(data_bytes, chunk_offset)
            if t_meas == LogMeasurement.EndValue:
                final_end_t = val
            elif t_meas == LogMeasurement.Maximum:
                final_max_t = val
            elif t_meas == LogMeasurement.Minimum:
                final_min_t = val
            elif t_meas == LogMeasurement.Mean:
                final_mean_t = val
            chunk_offset += 4

        s_time_str = s_time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

        cursor.execute(
            """
            INSERT INTO log_samples (
                log_id, timestamp, 
                end_pressure, maximum_pressure, mean_pressure, minimum_pressure, 
                end_temperature, maximum_temperature, mean_temperature, minimum_temperature
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                log_id,
                s_time_str,
                final_end_p,
                final_max_p,
                final_mean_p,
                final_min_p,
                final_end_t,
                final_max_t,
                final_mean_t,
                final_min_t,
            ),
        )

        offset += bytes_per_sample
        sample_idx += 1

    conn.commit()
    conn.close()

    # in parse_and_store_standard_log, after storing samples:
    log(
        f"Datalog #{log_number}: {num_samples} samples, layout {layout_id}, "
        f"interval {interval}s, mode {get_symbol(LogMode, mode)}",
        prefix="STORE",
    )


# ==========================================
# 4. PROTOCOL COMMUNICATION CORE
# ==========================================

PREAMBLE = b"\x55\x55\x55"
DELIM_REQ = 0x00
DELIM_RESP = 0x01
EOF = 0xAA


def build_request(cmd_number, data_bytes=b""):
    header = struct.pack("< B B H", DELIM_REQ, cmd_number, len(data_bytes))
    return PREAMBLE + header + data_bytes + bytes([EOF])


def read_response(ser):
    """Robustly reads a protocol frame and extracts the command, response code, and data payload."""
    PREAMBLE = b"\x55\x55\x55"
    EOF = 0xAA

    # 1. Sync to Preamble
    sync = b""
    while True:
        b = ser.read(1)
        if not b:
            return None
        sync = (sync + b)[-3:]
        if sync == PREAMBLE:
            break

    # 2. Read Delimiter (1) + CommandNumber (1) + DataLength (2) = 4 bytes
    header = ser.read(4)
    if len(header) < 4:
        return None

    delimiter, cmd_num, data_len = struct.unpack("< B B H", header)

    # Verify it's a response frame (DelimiterResponseByteValue = 0x01)
    if delimiter != 0x01:
        return None

    # 3. Read Response Code (1 byte)
    resp_code_byte = ser.read(1)
    if not resp_code_byte:
        return None
    resp_code = resp_code_byte[0]

    # 4. Read Data Payload (data_len bytes)
    data = ser.read(data_len) if data_len > 0 else b""

    # 5. Read End of Frame (1 byte)
    eof_byte = ser.read(1)
    if not eof_byte or eof_byte[0] != EOF:
        # Frame mismatch, drain slightly and continue
        pass

    return (cmd_num, resp_code, data)


def send_system_notification(title, message):
    """Fires a desktop notification from the background systemd daemon."""
    # Print to stderr for standard logging visibility
    print(f"NOTIFICATION: {title} - {message}", file=sys.stderr)

    # 1. Look for active desktop user GUI environment paths
    try:
        # Dynamically discover the active DBUS session address for X11/Wayland
        pgrep = subprocess.run(
            ["pgrep", "-u", "pi", "lxsession"], capture_output=True, text=True
        )
        if not pgrep.stdout.strip():
            # If default 'pi' user isn't found, try finding any active desktop session leader
            pgrep = subprocess.run(
                ["pgrep", "lxsession"], capture_output=True, text=True
            )

        if pgrep.stdout.strip():
            pid = pgrep.stdout.strip().split("\n")[0]
            # Read the environment variables of that desktop session
            with open(f"/proc/{pid}/environ", "rb") as f:
                env_bytes = f.read()

            env_vars = {}
            for item in env_bytes.split(b"\x00"):
                if b"=" in item:
                    k, v = item.split(b"=", 1)
                    env_vars[k.decode("utf-8", errors="ignore")] = v.decode(
                        "utf-8", errors="ignore"
                    )

            # Extract standard window manager and communication buses
            display = env_vars.get("DISPLAY", ":0")
            dbus = env_vars.get("DBUS_SESSION_BUS_ADDRESS", "")

            # Construct the execution context environment
            notify_env = os.environ.copy()
            notify_env["DISPLAY"] = display
            if dbus:
                notify_env["DBUS_SESSION_BUS_ADDRESS"] = dbus

            # Fire the pop-up notification alert using notify-send
            subprocess.run(
                [
                    "notify-send",
                    "-i",
                    "device_usb",  # Standard icon reference
                    "-t",
                    "5000",  # Stay visible for 5 seconds
                    title,
                    message,
                ],
                env=notify_env,
                check=False,
            )
    except Exception as e:
        print(f"Warning: Failed to render desktop notification: {e}", file=sys.stderr)


def handle_communication(ser, serial_number):
    """Processes both Leak Tests and Standard Datalogs in a single pass."""
    logs_downloaded = 0
    tests_downloaded = 0

    def get_log_count(info_command):
        data = ser.query(info_command)
        return data[0] if len(data) >= 1 else 0

    # 1. PROCESS LEAK TESTS
    for log_num in range(1, get_log_count(CommandNumber.ReadLeakTestLoggerInfo) + 1):
        data = ser.query(CommandNumber.ReadLeakTestLog, struct.pack("<B", log_num))
        if data:
            parse_and_store_leak_test(serial_number, log_num, data[1:])
            tests_downloaded += 1

    # 2. PROCESS STANDARD DATALOGS
    for log_num in range(1, get_log_count(CommandNumber.ReadLoggerInfo) + 1):
        header_data = ser.query(CommandNumber.ReadLogHeader, struct.pack("<B", log_num))
        if len(header_data) < 13:
            continue

        log_header_bytes = header_data[1:]
        if log_header_bytes[0] != LogStatus.Finished:
            continue

        num_records = struct.unpack("<I", log_header_bytes[4:8])[0]
        total_bytes = num_records * 4
        if total_bytes == 0:
            continue

        offset = 0
        log_data_payload = b""
        while offset < total_bytes:
            chunk_size = min(total_bytes - offset, 240)
            chunk = ser.query(
                CommandNumber.ReadLogData,
                struct.pack("<BIH", log_num, offset, chunk_size),
            )
            if not chunk:
                break
            log_data_payload += chunk
            offset += len(chunk)

        if len(log_data_payload) == total_bytes:
            parse_and_store_standard_log(
                serial_number, log_num, log_header_bytes, log_data_payload
            )
            logs_downloaded += 1

    # 3. PUSH CONFIG — once per pass, after downloads
    cfg_raw, cfg = load_config()
    if cfg_raw is not None:
        push_config(ser, cfg)
        # log("Config pushed to device")

    if logs_downloaded > 0 or tests_downloaded > 0:
        msg = f"DPI110-IS ({serial_number}) sync complete.\nSaved {logs_downloaded} Datalogs & {tests_downloaded} Leak Tests."
        print(msg)
        send_system_notification("Database Sync Complete", msg)

    # log("Sync failed — signalling with red LED", prefix="DAEMON")
    blink_red(ser, times=2)


# ==========================================
# 5. ENTRY POINT & BOOTSTRAP (UNIX CLI & DAEMON)
# ==========================================


def execute_cli_command(cmd_number, payload_args, force_port, out_raw, out_text):
    """Executes a single command, reporting exact hardware error response codes."""
    import serial

    codec = COMMAND_REGISTRY.get(cmd_number, CommandCodec())

    try:
        payload_bytes = codec.encode(payload_args)
    except (ValueError, TypeError, IndexError) as e:
        print(
            f"Error: Invalid arguments provided for this command type. ({e})",
            file=sys.stderr,
        )
        sys.exit(1)

    ports = (
        [force_port]
        if force_port
        else glob.glob("/dev/ttyUSB*")
        + glob.glob("/dev/ttyACM*")
        + glob.glob("/dev/serial0")
    )

    # Reverse lookup map for human-readable error reporting
    rc_map = {v: k for k, v in vars(ResponseCode).items() if not k.startswith("_")}

    for port in ports:
        try:
            DpiPort = make_dpi_port(serial)
            with DpiPort(port, 115200, timeout=2.0) as ser:
                ser.query(CommandNumber.ReadDeviceInfo)
                # with serial.Serial(port, 115200, timeout=2.0) as ser:
                print(f"Probing {port}...", file=sys.stderr)
                ser.reset_input_buffer()
                ser.reset_output_buffer()

                ser.write(build_request(cmd_number, payload_bytes))
                resp = read_response(ser)

                if resp:
                    cmd_val, resp_code, data = resp

                    if cmd_val == cmd_number:
                        if resp_code != ResponseCode.Ok:
                            err_name = rc_map.get(
                                resp_code, f"Unknown (0x{resp_code:02X})"
                            )
                            print(
                                f"Hardware Error: Device rejected command with code -> {err_name} (0x{resp_code:02X})",
                                file=sys.stderr,
                            )
                            sys.exit(1)

                        # Output successful data
                        if out_raw:
                            sys.stdout.buffer.write(data)
                        elif out_text:
                            decoded_str = codec.decode(data)
                            # print(decoded_str if decoded_str else "[Empty Response]")
                            print(
                                f"\033[92m{decoded_str if decoded_str else '[Empty Response]'}\033[0m"
                            )
                        else:
                            print(data.hex(" "))

                        sys.exit(0)
        except Exception as e:
            print(f"Debug [Port {port}]: {e}", file=sys.stderr)
            pass

    print("Error: DPI110-IS not found or timed out.", file=sys.stderr)
    sys.exit(1)


def setup_systemd():
    """Manages the systemd background daemon (launched via uv)."""

    if not systemd_available():
        print("=" * 60)
        print("WARNING: systemd not detected on this machine.")
        print("Skipping service installation (this is normal on")
        print("non systemd sistems/WSL/containers). The daemon can still be run")
        print("manually with:")
        print(f"    uv run {os.path.abspath(__file__)} --daemon")
        print("=" * 60)
        print("NOTE: uv was installed to ~/.local/bin.")
        print("      If 'uv' is not found in new terminals, add to PATH")
        print("=" * 60)
        return

    if os.path.exists(SERVICE_PATH):
        print(f"Service {SERVICE_NAME} is already installed.")
        print("Restarting daemon and attaching to logs...")
        subprocess.run(["systemctl", "restart", SERVICE_NAME])
        subprocess.run(["journalctl", "-u", SERVICE_NAME, "-f"])
        return

    print("Installing systemd service...")
    script_path = os.path.abspath(__file__)

    # Resolve uv's absolute path (systemd does NOT inherit the user's PATH)
    # uv_path = shutil.which("uv") or os.path.expanduser("~/.local/bin/uv")
    uv_path = find_uv()
    if not os.path.exists(uv_path):
        print("Fatal: uv binary not found; cannot install service.", file=sys.stderr)
        sys.exit(1)

    service_content = f"""[Unit]
Description=DPI110-IS USB Polling Daemon
After=network.target

[Service]
Type=simple
ExecStart={uv_path} run --quiet {script_path} --daemon
Restart=always
RestartSec=10
User=root
Environment=PATH=/usr/local/bin:/usr/bin:/bin:{os.path.expanduser("~/.local/bin")}
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""
    with open(SERVICE_PATH, "w") as f:
        f.write(service_content)

    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", SERVICE_NAME], check=True)
    subprocess.run(["systemctl", "start", SERVICE_NAME], check=True)
    print(f"Service installed and started. Logs: journalctl -u {SERVICE_NAME} -f\n")


def uninstall_daemon():
    """Safely stops, disables, and removes the systemd daemon service."""
    service_name = SERVICE_NAME
    service_path = SERVICE_PATH

    print("===================================================================")
    print(f" DAEMON UNINSTALLER           TARGET: {service_name}")
    print("===================================================================")

    if not systemd_available():
        sys.exit("No systemd in system")

    # 1. Stop the running service
    try:
        print(f" [+] Stopping service...")
        subprocess.run(
            ["systemctl", "stop", service_name], check=True, stderr=subprocess.DEVNULL
        )
    except subprocess.CalledProcessError:
        print(" [!] Service was not running.")

    # 2. Disable it from starting on boot
    try:
        print(f" [+] Disabling boot start...")
        subprocess.run(
            ["systemctl", "disable", service_name],
            check=True,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        print(" [!] Service was not enabled.")

    # 3. Remove the systemd service file
    if os.path.exists(service_path):
        try:
            os.remove(service_path)
            print(f" [+] Removed unit file: {service_path}")
        except Exception as e:
            print(f" [!] Error removing unit file: {e}")
    else:
        print(f" [!] Unit file not found at {service_path}")

    # 4. Reload the systemd daemon to clear the cached configuration
    try:
        print(f" [+] Reloading systemctl daemon...")
        subprocess.run(["systemctl", "daemon-reload"], check=True)
    except subprocess.CalledProcessError as e:
        print(f" [!] Error reloading systemd: {e}")

    print("===================================================================")
    print(" UNINSTALL COMPLETE. Your SQLite database was NOT deleted.")
    print("===================================================================")


def _install_uv():
    print("uv not found. Installing uv globally...")
    env = os.environ.copy()
    env["UV_INSTALL_DIR"] = "/usr/local/bin"
    subprocess.run(
        ["sh", "-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"],
        env=env,
        check=False,
    )


def find_uv() -> str:
    """Locate the uv binary, checking common install locations."""
    if path := shutil.which("uv"):
        return path
    if os.path.exists("/usr/local/bin/uv"):
        return "/usr/local/bin/uv"
    if os.path.exists(os.path.expanduser("~/.local/bin/uv")):
        return os.path.expanduser("~/.local/bin/uv")
    return ""


def systemd_available() -> bool:
    """True if this machine actually runs systemd (not just has the dir)."""
    if not os.path.isdir("/run/systemd/system"):
        return False
    return shutil.which("systemctl") is not None


def check_and_install_dependencies():
    """Bootstraps uv if missing. Package resolution is delegated entirely to uv."""
    uv = find_uv()
    if not uv:
        _install_uv()
        uv = find_uv()
        if not uv:
            sys.exit("uv installed but still not found — aborting.")

    r = subprocess.run(
        [uv, "run", "--quiet", os.path.abspath(__file__), "--version"],
        capture_output=True,
        text=True,
        check=False,
    )

    if r.returncode == 0:
        print(f"uv environment OK ({r.stdout.strip()}) — pyserial resolves correctly.")
    else:
        print("uv environment FAILED:", r.stderr, file=sys.stderr)
        sys.exit(1)


def make_dpi_port(serial):
    class DpiPort(serial.Serial):
        """Serial port with DPI110-IS protocol helpers baked in."""

        def send_write(self, cmd_number: CommandNumber, payload: bytes = b"") -> bool:
            """Sends a command frame and verifies the device ACK."""
            self.write(build_request(cmd_number, payload))
            resp = read_response(self)
            if resp and resp[0] == cmd_number and resp[1] == ResponseCode.Ok:
                return True
            code = resp[1] if resp else None
            name = ResponseCode(code).name if code is not None else "timeout"
            print(f"WARNING: {cmd_number.name} rejected: {name}", file=sys.stderr)
            return False

        def query(self, cmd_number: CommandNumber, payload: bytes = b"") -> bytes:
            """Sends a command and returns the OK payload, or b'' on any failure."""
            self.write(build_request(cmd_number, payload))
            resp = read_response(self)
            if resp and resp[0] == cmd_number and resp[1] == ResponseCode.Ok:
                return resp[2]
            return b""

    return DpiPort


def log(msg, prefix="INFO"):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [{prefix}] {msg}", flush=True)


def run_daemon():
    import serial

    print("Daemon started. Polling for hardware...")
    db_connect()

    while True:
        for port in glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*"):
            try:
                DpiPort = make_dpi_port(serial)
                with DpiPort(port, 115200, timeout=5.0) as ser:
                    ser.reset_input_buffer()
                    data = ser.query(CommandNumber.ReadDeviceInfo)

                    if len(data) >= 32:
                        serial_str = read_ascii(data, 16, 16)
                        # print(f"\n--- Connected to DPI110-IS ({serial_str}) ---")

                        model = read_ascii(data, 0, 16)
                        fw = f"{data[32]:02d}.{data[33]:02d}.{data[34]:02d}"
                        l_lim = read_float(data, 35)
                        u_lim = read_float(data, 39)
                        unit_str = get_symbol(PressureUnit, data[44])

                        log(
                            f"Connected: {model} S/N {serial_str} | FW {fw} | "
                            f"Range {l_lim:.2f}..{u_lim:.2f} {unit_str}",
                            prefix="DEVICE",
                        )

                        conn = db_connect()
                        conn.execute(
                            """INSERT OR REPLACE INTO devices
                            (serial_number, model, firmware_version, lower_pressure_range, upper_pressure_range, pressure_range_unit)
                            VALUES (?, ?, ?, ?, ?, ?)""",
                            (serial_str, model, fw, l_lim, u_lim, unit_str),
                        )
                        conn.commit()
                        conn.close()

                        handle_communication(ser, serial_str)
                        time.sleep(5)

            except serial.SerialException:
                # Normal disconnect behavior, ignore and keep polling
                pass
            except Exception as e:
                # Print real errors to the terminal so we can debug them!
                print(f"Exception on {port}: {e}", file=sys.stderr)

        time.sleep(5)


def main():
    import argparse
    import re

    parser = argparse.ArgumentParser(
        description=f"DPI110-IS Command-line Interface {SCRIPT_VERSION} (Unix CLI & Daemon)",
        epilog=f"""Examples:
  # Install/Uninstall the Daemon in background
  sudo python3 %(prog)s --install
  sudo python3 %(prog)s --uninstall

  # Stream daemon activity
  sudo python3 %(prog)s --daemon
  journalctl -u {SERVICE_NAME} -f
  sudo python3 %(prog)s --install
  
  # Path to database file
  "{DB_PATH}"

  # System & Diagnostic Operations
  sudo python3 %(prog)s --daemon
  uv run %(prog)s --daemon

  # Reading Information in human-readable text or Raw Binary (default)
  uv run %(prog)s --read-device-info --text > device.txt
  uv run %(prog)s --read-log-header 1 --raw > header.bin
  uv run %(prog)s --read-log-data 1 0 64 16 --text

  # Writing Byte/Enum Configurations
  uv run %(prog)s --select-leak-test-recipe 3
  uv run %(prog)s --write-auto-off-mode 1
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Daemon & Installation Flags
    sys_group = parser.add_argument_group("System & Daemon Control")
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {SCRIPT_VERSION}",
    )
    sys_group.add_argument(
        "--daemon",
        action="store_true",
        help="Run in continuous background daemon mode (Logs to SQLite)",
    )
    sys_group.add_argument(
        "--install", action="store_true", help="Install systemd daemon service"
    )
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="Safely stop, disable, and remove the background daemon.",
    )
    sys_group.add_argument(
        "--port", help="Force specific serial port (default: auto-detect)"
    )
    sys_group.add_argument(
        "--test-all",
        action="store_true",
        help="Run a diagnostic sweep testing all device commands",
    )

    # Output Formatting Flags
    fmt_group = parser.add_argument_group("CLI Output Formatting")
    fmt_group.add_argument(
        "--raw",
        action="store_true",
        help="Output exact binary payload to stdout (for piping to xxd, dd, etc.)",
    )
    fmt_group.add_argument(
        "--text",
        action="store_true",
        help="Attempt to decode output payload as ASCII text",
    )

    # Dynamically inject all CommandNumbers as CLI arguments
    cmd_group = parser.add_argument_group("Hardware Commands")
    cmd_map = {}
    for name, value in vars(CommandNumber).items():
        if (
            not name.startswith("_")
            and isinstance(value, int)
            and value != CommandNumber.Undefined
        ):
            flag = f"--{re.sub(r'(?<!^)(?=[A-Z])', '-', name).lower()}"
            cmd_group.add_argument(
                flag, nargs="*", metavar="VAL", help=f"Execute {name} (0x{value:02X})"
            )
            cmd_map[flag.strip("-").replace("-", "_")] = value

    args = parser.parse_args()

    # Handle system-level arguments
    if args.install:
        if os.geteuid() != 0:
            print(
                "Installation requires root. Please run: sudo python3",
                sys.argv[0],
                file=sys.stderr,
            )
            sys.exit(1)
        check_and_install_dependencies()
        init_db()
        setup_systemd()
        sys.exit(0)

    if args.uninstall:
        # Require sudo to interact with systemctl and /etc/systemd/
        if os.geteuid() != 0:
            print("Error: --uninstall requires root privileges. Run with sudo.")
            sys.exit(1)
        uninstall_daemon()
        sys.exit(0)

    if args.daemon:
        check_and_install_dependencies()  # ensures uv exists
        run_daemon()
        sys.exit(0)

    # Determine which hardware command flag the user passed
    executed_cmd = None
    payload_args = []

    for arg_name, cmd_val in cmd_map.items():
        val = getattr(args, arg_name)
        if val is not None:
            executed_cmd = cmd_val
            payload_args = val
            break

    # If no valid arguments were provided, show help
    if executed_cmd is None:
        parser.print_help()
        sys.exit(1)
    else:
        # Execute the requested CLI command
        execute_cli_command(executed_cmd, payload_args, args.port, args.raw, args.text)


if __name__ == "__main__":
    main()
