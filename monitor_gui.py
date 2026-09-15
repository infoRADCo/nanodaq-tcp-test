#!/usr/bin/env python3
"""
Real-time channel pressure/temperature monitor for a Chell nanoDAQ-LT/LTS,
reproducing (a small subset of) what the device's embedded web server shows,
driven purely over the TCP command protocol validated against the real unit.

Architecture
------------
A single background thread (`Worker`) owns the TCP socket end-to-end: it
connects, auto-detects the device's channel count / full scale / units via
Get Status(Full), starts streaming, and then loops forever doing three
things in turn:

  1. draining any pending GUI requests (stream on/off, rezero, disconnect)
  2. periodically pausing the pressure stream to poll temperatures
     (Get Status "with temp"), since temperature is not part of the
     continuous data stream
  3. otherwise reading + decoding pressure packets

All socket reads/writes happen on this one thread only, to avoid two
threads racing on the same TCP socket. Results are pushed to a queue.Queue
that the Tkinter main loop drains via `root.after(...)` - Tkinter itself
must only ever be touched from the main thread.

Only Differential-type scanners are converted to engineering units here
(raw = 0..65535 maps linearly to -full_scale..+full_scale). Absolute-mode
scaling depends on model/full-scale specific lookup tables (see section
4.1.3 of the User Programming Guide) and is not implemented - raw counts
are shown in the pressure column and the Pa / airspeed columns stay "--".
The same "--" fallback applies when the device reports a pressure unit not
listed in UNITS_TO_PA, rather than silently treating it as psi.
"""

from __future__ import annotations

import csv
import datetime
import os
import queue
import socket
import threading
import time
import tkinter as tk
from tkinter import ttk

from nanodaq_client import (
    Channel,
    Command,
    DataRate,
    NanoDAQClient,
    PacketBuffer,
    Protocol,
    StatusLevel,
    scale_differential,
)

TEMP_POLL_INTERVAL = 60.0  # seconds between temperature polls
# Each poll costs ~2.5s of dead time (stream off -> drain the reply until the
# device goes quiet for COMMAND_TIMEOUT -> stream on), during which NO pressure
# data arrives. At the old 5s interval that was 52%% of the session lost, which
# a logged run measured directly. Temperature is display-only here (and this
# unit only returns uncalibrated raw counts anyway), so it is not worth paying
# for often - at 60s the loss is ~4%%.
# NOTE: at low pressure Rate settings (e.g. 1-5Hz), the device batches TCP
# packets and only flushes them every ~4s (Nagle/delayed-ACK style
# buffering - confirmed on real hardware, not a bug in this client). If
# TEMP_POLL_INTERVAL is close to that flush period, this pressure-stream's
# periodic Stream Off/On (needed to safely poll temperature - see module
# docstring) can repeatedly reset the device's pending buffer before it
# ever flushes, making pressure data appear to stop entirely. Keep this
# interval well above the flush period, especially if you lower the Rate.
STREAM_READ_TIMEOUT = 0.2  # socket timeout while polling for pressure packets
COMMAND_TIMEOUT = 2.0  # socket timeout while doing setup / temp-poll command round trips

PSI_TO_PA = 6894.757293168361  # exact by definition (1 psi = 6894.757293168361 Pa)

# Pa per one unit of the device's reported [Press. units]. Keys are lower
# case with spaces removed. Anything not listed is displayed unconverted.
UNITS_TO_PA = {
    "pa": 1.0, "hpa": 100.0, "kpa": 1000.0, "mbar": 100.0, "bar": 1e5,
    "psi": PSI_TO_PA, "psig": PSI_TO_PA, "psid": PSI_TO_PA,
    "inh2o": 249.089, "mmh2o": 9.80665, "mmhg": 133.322, "torr": 133.322,
}

# Sanity bound on the channel count parsed out of Get Status. The nanoDAQ-LT
# family tops out at 64 channels; a bigger number means the status text was
# mis-parsed (or the peer is not a nanoDAQ), and building thousands of Tk
# rows for it would freeze the GUI.
MAX_CHANNELS = 64

# ISA sea-level air density, used for the pitot airspeed column. This is a
# fixed constant, NOT compensated for the actual ambient temperature or
# barometric pressure - at 20 C / 1013 hPa the true density is ~1.204, so
# the displayed speed reads ~0.9% low there. Adjust if you need better than
# ~1% airspeed accuracy.
AIR_DENSITY = 1.225  # kg/m^3

# Displayed decimals. One 16-bit LSB at FS=1 psi is 2/65535 = 3.05e-5 psi
# (0.21 Pa), so 5 decimals in psi / 2 in Pa is the last digit that still
# carries device resolution - anything beyond that is pure quantisation.
PSI_DECIMALS = 5
PA_DECIMALS = 2

LOG_DIR = "logs"
# The stream runs at 100 Hz; logging every packet is ~100 rows/s (~100 MB/h).
# Log every Nth packet instead - at N=10 that is ~10 Hz, still far faster
# than anything mechanical being measured here, so nothing real is lost.
# Set to 1 if you genuinely need every sample.
LOG_DECIMATION = 10


def airspeed_kmh(q_pa: float) -> float:
    """Incompressible pitot relation: v = sqrt(2q/rho), returned in km/h.

    The sign of the differential pressure is carried through (a negative q
    means the reference port is at the higher pressure, i.e. reversed flow),
    so the magnitude uses abs(q) and the sign is re-applied afterwards.
    Valid well below Mach 0.3; at +/-1 psi FS the top of range is ~34 m/s,
    so compressibility is never a concern on this unit.
    """
    speed = (2.0 * abs(q_pa) / AIR_DENSITY) ** 0.5
    return (speed if q_pa >= 0 else -speed) * 3.6


class Worker(threading.Thread):
    """Owns the nanoDAQ TCP connection. All socket I/O happens here only."""

    def __init__(self, ip: str, port: int, out_queue: "queue.Queue[tuple]"):
        super().__init__(daemon=True)
        self.ip = ip
        self.port = port
        self.out = out_queue
        self.commands: "queue.Queue[tuple]" = queue.Queue()

    def request_stop(self) -> None:
        self.commands.put(("stop",))

    def request_stream(self, on: bool) -> None:
        self.commands.put(("stream_on" if on else "stream_off",))

    def request_rezero(self) -> None:
        self.commands.put(("rezero",))

    def _send(self, client: NanoDAQClient, label: str, fn, fail_msg: str = "rejected by device (!!)") -> bool:
        """One command round trip with the outcome actually checked.

        Returns True only on success. A negative ack (`!!`), a timeout or
        malformed ack bytes are reported to the GUI and return False, so the
        caller can keep its notion of the device state in sync instead of
        assuming the command worked. Connection-level OSErrors are NOT
        swallowed here - those mean the session is gone and are handled by
        the main loop.
        """
        client.set_timeout(COMMAND_TIMEOUT)
        try:
            ok = fn()
        except socket.timeout:
            self.out.put(("error", f"{label}: no ack from device (timeout)"))
            return False
        except RuntimeError as exc:
            self.out.put(("error", f"{label}: {exc}"))
            return False
        if ok is False:
            self.out.put(("error", f"{label}: {fail_msg}"))
            return False
        return True

    def _stop_stream(self, client: NanoDAQClient, label: str) -> bool:
        """Stream Off for a stream that is currently running. The ack is
        buried in the in-flight packets, so success = the line going quiet
        (see NanoDAQClient.stream_off_quiesce)."""
        return self._send(client, label, lambda: client.stream_off_quiesce(Channel.TCP_UDP),
                          fail_msg="device kept streaming after Stream Off")

    def run(self) -> None:
        client = NanoDAQClient(self.ip, self.port, timeout=COMMAND_TIMEOUT)
        try:
            client.connect()
            # The device may already have been streaming (e.g. left on from
            # a previous session) and starts sending data the instant a
            # client connects. Quiesce it before sending any command so we
            # don't mistake stray stream bytes for a command's ack.
            client.flush_input()
            client.send_command(Command.STANDBY, read_ack=False)
            client.flush_input()
        except OSError as exc:
            self.out.put(("error", f"connect failed: {exc}"))
            self.out.put(("disconnected", None))
            return

        try:
            status = client.get_status(StatusLevel.FULL)
        except Exception as exc:
            self.out.put(("error", f"status query failed: {exc}"))
            client.close()
            self.out.put(("disconnected", None))
            return

        channels_raw = status.fields.get("Active channels") or status.fields.get("TCP channels") or "16"
        try:
            channels = int(channels_raw)
        except ValueError:
            channels = 16
        if not 1 <= channels <= MAX_CHANNELS:
            self.out.put(("error", f"device reported {channels} channels (expected 1..{MAX_CHANNELS}) - assuming 16"))
            channels = 16
        try:
            full_scale = float(status.fields.get("Full scale", "1"))
        except ValueError:
            full_scale = 1.0
        # This unit's firmware (2.2.2) truncates the 'full' status before the
        # [Press. units] / [Press. type] fields the manual documents, so the
        # get() below comes back empty on real hardware. Fall back to psi:
        # confirmed against the datasheet (+/-1 psi FS) and by polling a live
        # packet - raw sat at mid-scale (~32900) with all ports at ambient,
        # which only matches the differential +/-FS scaling, not absolute.
        units = status.fields.get("Press. units", "").strip() or "psi"
        press_type = status.fields.get("Press. type", "Differential").strip() or "Differential"

        # Only Differential counts have a known linear scaling (see module
        # docstring). Absolute-mode devices get raw counts in the pressure
        # column; unknown units are shown in the device's own unit but not
        # converted to Pa. Both cases leave to_pa = None so the GUI and the
        # CSV writer print "--" for Pa / airspeed instead of a wrong number.
        scaled = press_type.lower().startswith("diff")
        to_pa = UNITS_TO_PA.get(units.lower().replace(" ", "")) if scaled else None
        if not scaled:
            self.out.put(("error", f"pressure type '{press_type}': showing raw counts, no Pa/airspeed"))
        elif to_pa is None:
            self.out.put(("error", f"unknown pressure unit '{units}': values shown in {units}, no Pa/airspeed"))

        self.out.put((
            "config",
            {
                "channels": channels, "full_scale": full_scale, "units": units,
                "press_type": press_type, "scaled": scaled, "to_pa": to_pa,
            },
        ))

        try:
            client.set_protocol(Protocol.BINARY_LE, Channel.TCP_UDP)
            client.set_rate(DataRate.HZ_100, Channel.TCP_UDP)
            client.stream_on(Channel.TCP_UDP)
        except Exception as exc:
            self.out.put(("error", f"failed to start streaming: {exc}"))
            client.close()
            self.out.put(("disconnected", None))
            return

        streaming = True
        buf = PacketBuffer(channels)
        last_temp_poll = 0.0
        stopping = False

        try:
            while not stopping:
                # 1) drain any pending GUI requests. Every command's outcome
                #    is checked (see _send) and the resulting stream state is
                #    echoed back so the GUI never shows a state the device
                #    refused.
                try:
                    while True:
                        cmd = self.commands.get_nowait()
                        if cmd[0] == "stop":
                            stopping = True
                            break
                        elif cmd[0] == "stream_on" and not streaming:
                            if self._send(client, "Stream On", lambda: client.stream_on(Channel.TCP_UDP)):
                                streaming = True
                                buf = PacketBuffer(channels)
                            self.out.put(("stream_state", streaming))
                        elif cmd[0] == "stream_off" and streaming:
                            if self._stop_stream(client, "Stream Off"):
                                streaming = False
                            self.out.put(("stream_state", streaming))
                        elif cmd[0] == "rezero":
                            # Rezero's ack would be lost in the stream just
                            # like Stream Off's, so pause the stream around it
                            # (same sequence the workbench uses).
                            if streaming:
                                if not self._stop_stream(client, "Stream Off (rezero)"):
                                    continue
                                if self._send(client, "Rezero", client.rezero):
                                    self.out.put(("info", "Rezero acknowledged by device"))
                                if self._send(client, "Stream On (after rezero)", lambda: client.stream_on(Channel.TCP_UDP)):
                                    buf = PacketBuffer(channels)
                                else:
                                    streaming = False
                                    self.out.put(("stream_state", False))
                            elif self._send(client, "Rezero", client.rezero):
                                self.out.put(("info", "Rezero acknowledged by device"))
                except queue.Empty:
                    pass
                if stopping:
                    break

                # 2) periodic temperature poll (pauses streaming briefly -
                #    see module docstring for why)
                now = time.monotonic()
                if streaming and (now - last_temp_poll) >= TEMP_POLL_INTERVAL:
                    last_temp_poll = now
                    if not self._stop_stream(client, "Stream Off (temperature poll)"):
                        continue  # still streaming; try again next interval
                    try:
                        # Raw ADC counts (level 4) - the calibrated "With
                        # temp." level always reports 0.00 on this unit's
                        # firmware, so show the uncalibrated raw value
                        # instead (no conversion to degrees C).
                        raw_temps = client.get_raw_readings(StatusLevel.RAW_TEMP, read_timeout=COMMAND_TIMEOUT)
                        if raw_temps:
                            self.out.put(("temperature", raw_temps))
                    except (socket.timeout, ValueError, RuntimeError) as exc:
                        self.out.put(("error", f"temperature poll failed: {exc}"))
                    # The stream is off at this point no matter how the read
                    # went - always try to bring it back, and if the device
                    # refuses, say so and flip the state instead of leaving
                    # the GUI claiming "streaming" over a silent line.
                    if self._send(client, "Stream On (after temperature poll)", lambda: client.stream_on(Channel.TCP_UDP)):
                        buf = PacketBuffer(channels)
                    else:
                        streaming = False
                        self.out.put(("stream_state", False))
                    continue  # let the next loop iteration resume pressure reads

                # 3) pressure stream reads
                if streaming:
                    client.set_timeout(STREAM_READ_TIMEOUT)
                    try:
                        data = client.read_raw()
                    except socket.timeout:
                        continue
                    if not data:
                        self.out.put(("error", "connection closed by device"))
                        break
                    buf.feed(data)
                    packet = buf.pop_packet()
                    while packet is not None:
                        if scaled:
                            values = [scale_differential(v, full_scale) for v in packet.values]
                        else:
                            values = [float(v) for v in packet.values]
                        # Raw counts ride along so the log stays lossless -
                        # scaling is reversible, but keeping the integers
                        # means a log can be re-scaled later if the full
                        # scale or pressure type turns out to be different.
                        self.out.put(("pressure", (values, packet.values)))
                        packet = buf.pop_packet()
                else:
                    time.sleep(0.1)
        except OSError as exc:
            # Anything socket-level that is not a plain timeout (reset,
            # unplugged cable, ...) ends the session; the GUI gets a message
            # rather than a dead thread with a traceback on stderr.
            self.out.put(("error", f"connection lost: {exc}"))
        finally:
            try:
                client.set_timeout(COMMAND_TIMEOUT)
                client.stream_off(Channel.TCP_UDP)
            except Exception:
                pass
            client.close()
            self.out.put(("disconnected", None))


class MonitorApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("nanoDAQ-LT/LTS Monitor")

        self.worker: Worker | None = None
        self.out_queue: "queue.Queue[tuple]" = queue.Queue()
        self.channel_rows: list[dict[str, tk.Widget]] = []
        self.units = ""
        self.unit_label = ""  # column header / CSV suffix for the pressure value
        self.to_pa: float | None = None  # None = no Pa / airspeed conversion available
        self.channel_count = 0
        self.log_fh = None
        self.log_writer = None
        self.log_path: str | None = None
        self.log_packet_index = 0
        self.log_rows_written = 0
        self.log_started_at = 0.0

        top = ttk.Frame(root, padding=8)
        top.pack(fill="x")

        ttk.Label(top, text="IP").grid(row=0, column=0, padx=4)
        self.ip_entry = ttk.Entry(top, width=16)
        self.ip_entry.insert(0, "192.168.1.190")
        self.ip_entry.grid(row=0, column=1, padx=4)

        ttk.Label(top, text="Port").grid(row=0, column=2, padx=4)
        self.port_entry = ttk.Entry(top, width=6)
        self.port_entry.insert(0, "101")
        self.port_entry.grid(row=0, column=3, padx=4)

        self.connect_btn = ttk.Button(top, text="Connect", command=self.on_connect_clicked)
        self.connect_btn.grid(row=0, column=4, padx=8)

        self.stream_btn = ttk.Button(top, text="Stream Off", command=self.on_stream_toggle, state="disabled")
        self.stream_btn.grid(row=0, column=5, padx=4)
        self.streaming = False

        self.rezero_btn = ttk.Button(top, text="Rezero", command=self.on_rezero, state="disabled")
        self.rezero_btn.grid(row=0, column=6, padx=4)

        self.log_btn = ttk.Button(top, text="Log Start", command=self.on_log_toggle, state="disabled")
        self.log_btn.grid(row=0, column=7, padx=4)

        self.status_var = tk.StringVar(value="Disconnected")
        ttk.Label(top, textvariable=self.status_var).grid(row=0, column=8, padx=12)

        self.grid_frame = ttk.Frame(root, padding=8)
        self.grid_frame.pack(fill="both", expand=True)

        self.log_var = tk.StringVar(value="")
        ttk.Label(root, textvariable=self.log_var, foreground="firebrick").pack(fill="x", padx=8, pady=(0, 8))

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self.poll_queue)

    # -- GUI event handlers --------------------------------------------

    def on_connect_clicked(self) -> None:
        if self.worker is not None:
            self.worker.request_stop()
            self.connect_btn.configure(state="disabled")
            self.status_var.set("Disconnecting...")
            return

        ip = self.ip_entry.get().strip()
        try:
            port = int(self.port_entry.get().strip())
        except ValueError:
            self.log_var.set("Port must be a number")
            return

        self.status_var.set("Connecting...")
        self.log_var.set("")
        self.connect_btn.configure(state="disabled")
        self.worker = Worker(ip, port, self.out_queue)
        self.worker.start()

    def on_stream_toggle(self) -> None:
        if self.worker is None:
            return
        # Ask the worker and wait for its "stream_state" echo before
        # changing the button - the device may NACK or time out.
        self.worker.request_stream(not self.streaming)
        self.stream_btn.configure(state="disabled")

    def apply_stream_state(self, streaming: bool) -> None:
        self.streaming = streaming
        self.stream_btn.configure(text="Stream Off" if streaming else "Stream On", state="normal")

    def on_rezero(self) -> None:
        if self.worker is not None:
            self.worker.request_rezero()

    def on_log_toggle(self) -> None:
        if self.log_writer is None:
            self.start_logging()
        else:
            self.stop_logging()

    def start_logging(self) -> None:
        if self.channel_count <= 0:
            self.log_var.set("Connect first - channel count is not known yet")
            return
        os.makedirs(LOG_DIR, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(LOG_DIR, "nanodaq_" + stamp + ".csv")
        try:
            # newline="" is required by the csv module on Windows, otherwise
            # every row gets a stray blank line between it and the next.
            fh = open(path, "w", newline="", encoding="utf-8")
        except OSError as exc:
            self.log_var.set(f"Could not open log file: {exc}")
            return

        writer = csv.writer(fh)
        # Metadata as a leading '#' comment line: pandas reads it back with
        # comment="#", Excel just shows it as a first row.
        writer.writerow([
            f"# nanoDAQ log  start={datetime.datetime.now().isoformat(timespec='seconds')}"
            f"  channels={self.channel_count}  units={self.units}"
            f"  air_density={AIR_DENSITY}  decimation={LOG_DECIMATION}"
        ])
        header = ["iso_time", "elapsed_s", "packet_index"]
        header += [f"ch{i}_raw" for i in range(1, self.channel_count + 1)]
        header += [f"ch{i}_{self.unit_label}" for i in range(1, self.channel_count + 1)]
        header += [f"ch{i}_pa" for i in range(1, self.channel_count + 1)]
        header += [f"ch{i}_kmh" for i in range(1, self.channel_count + 1)]
        writer.writerow(header)

        self.log_fh = fh
        self.log_writer = writer
        self.log_path = path
        self.log_packet_index = 0
        self.log_rows_written = 0
        self.log_started_at = time.time()
        self.log_btn.configure(text="Log Stop")
        self.log_var.set(f"Logging to {os.path.abspath(path)}")

    def stop_logging(self) -> None:
        if self.log_fh is not None:
            try:
                self.log_fh.close()
            except OSError:
                pass
        path, rows = self.log_path, self.log_rows_written
        self.log_fh = None
        self.log_writer = None
        self.log_path = None
        self.log_btn.configure(text="Log Start")
        if path is not None:
            self.log_var.set(f"Log saved: {os.path.abspath(path)} ({rows} rows)")

    def write_log_row(self, values: list[float], raws: list[int]) -> None:
        self.log_packet_index += 1
        if (self.log_packet_index - 1) % LOG_DECIMATION:
            return
        now = time.time()
        row = [
            datetime.datetime.now().isoformat(timespec="milliseconds"),
            f"{now - self.log_started_at:.3f}",
            self.log_packet_index,
        ]
        row += list(raws)
        row += [f"{v:.{PSI_DECIMALS}f}" for v in values]
        if self.to_pa is not None:
            pascals = [v * self.to_pa for v in values]
            row += [f"{pa:.{PA_DECIMALS}f}" for pa in pascals]
            row += [f"{airspeed_kmh(pa):.2f}" for pa in pascals]
        else:
            row += ["" for _ in values]
            row += ["" for _ in values]
        try:
            self.log_writer.writerow(row)
            self.log_rows_written += 1
            # Flush so an external reader (or a crash) never loses the tail.
            self.log_fh.flush()
        except (OSError, ValueError) as exc:
            self.log_var.set(f"Log write failed, logging stopped: {exc}")
            self.stop_logging()

    def on_close(self) -> None:
        self.stop_logging()
        if self.worker is not None:
            self.worker.request_stop()
        self.root.after(200, self.root.destroy)

    # -- worker -> GUI updates -------------------------------------------

    def build_channel_grid(self, channels: int, units: str, scaled: bool = True) -> None:
        for child in self.grid_frame.winfo_children():
            child.destroy()
        self.channel_rows = []
        self.units = units if scaled else "raw"
        units = self.units
        self.channel_count = channels

        # "psig" rather than "psid": the scanner only has Absolute/Differential
        # modes (no gauge mode exists in the command set), and gauge readings
        # are made by leaving the common reference port open to atmosphere -
        # which is how this rig is plumbed. If you ever pipe the reference
        # port to something other than ambient, this label is a lie.
        gauge_label = f"{units}g" if units == "psi" else units
        self.unit_label = gauge_label
        ttk.Label(self.grid_frame, text="CH", width=4, font=("", 10, "bold")).grid(row=0, column=0)
        ttk.Label(self.grid_frame, text=f"Pressure ({gauge_label})", width=14, font=("", 10, "bold")).grid(row=0, column=1)
        ttk.Label(self.grid_frame, text="Pressure (Pa)", width=14, font=("", 10, "bold")).grid(row=0, column=2)
        ttk.Label(self.grid_frame, text="Airspeed (km/h)", width=14, font=("", 10, "bold")).grid(row=0, column=3)
        ttk.Label(self.grid_frame, text="Temp (raw)", width=10, font=("", 10, "bold")).grid(row=0, column=4)

        for ch in range(channels):
            row = ch + 1
            ttk.Label(self.grid_frame, text=str(ch + 1), width=4).grid(row=row, column=0)
            pressure_var = tk.StringVar(value="--")
            pascal_var = tk.StringVar(value="--")
            speed_var = tk.StringVar(value="--")
            temp_var = tk.StringVar(value="--")
            ttk.Label(self.grid_frame, textvariable=pressure_var, width=14).grid(row=row, column=1)
            ttk.Label(self.grid_frame, textvariable=pascal_var, width=14).grid(row=row, column=2)
            ttk.Label(self.grid_frame, textvariable=speed_var, width=14).grid(row=row, column=3)
            ttk.Label(self.grid_frame, textvariable=temp_var, width=10).grid(row=row, column=4)
            self.channel_rows.append({
                "pressure": pressure_var,
                "pascal": pascal_var,
                "speed": speed_var,
                "temp": temp_var,
            })

    def poll_queue(self) -> None:
        # Every packet is logged, but only the newest one per tick is drawn:
        # at 100 Hz there are ~10 packets per 100 ms tick and 48 widget
        # updates each, and redrawing all of them would let a backlog
        # monopolise the Tk main thread. The eye cannot tell the difference.
        latest_pressure = None
        try:
            while True:
                kind, payload = self.out_queue.get_nowait()
                if kind == "config":
                    self.to_pa = payload["to_pa"]
                    self.build_channel_grid(payload["channels"], payload["units"], payload["scaled"])
                    self.status_var.set(
                        f"Connected ({payload['channels']} ch, "
                        f"FS={payload['full_scale']} {payload['units']}, {payload['press_type']})"
                    )
                    self.connect_btn.configure(text="Disconnect", state="normal")
                    self.rezero_btn.configure(state="normal")
                    self.log_btn.configure(state="normal")
                    self.apply_stream_state(True)
                elif kind == "stream_state":
                    self.apply_stream_state(bool(payload))
                elif kind == "pressure":
                    values, raws = payload
                    if self.log_writer is not None:
                        self.write_log_row(values, raws)
                    latest_pressure = values
                elif kind == "info":
                    self.log_var.set(str(payload))
                elif kind == "temperature":
                    for var_row, value in zip(self.channel_rows, payload):
                        var_row["temp"].set(f"{value:.0f}")
                elif kind == "error":
                    self.log_var.set(str(payload))
                elif kind == "disconnected":
                    self.worker = None
                    self.status_var.set("Disconnected")
                    self.stop_logging()
                    self.connect_btn.configure(text="Connect", state="normal")
                    self.stream_btn.configure(state="disabled")
                    self.rezero_btn.configure(state="disabled")
                    self.log_btn.configure(state="disabled")
        except queue.Empty:
            pass
        if latest_pressure is not None and self.channel_rows:
            decimals = 0 if self.units == "raw" else PSI_DECIMALS
            for var_row, value in zip(self.channel_rows, latest_pressure):
                var_row["pressure"].set(f"{value:.{decimals}f}")
                if self.to_pa is not None:
                    pascal = value * self.to_pa
                    var_row["pascal"].set(f"{pascal:.{PA_DECIMALS}f}")
                    var_row["speed"].set(f"{airspeed_kmh(pascal):.2f}")
                else:
                    var_row["pascal"].set("--")
                    var_row["speed"].set("--")
        self.root.after(100, self.poll_queue)


def main() -> None:
    root = tk.Tk()
    MonitorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
