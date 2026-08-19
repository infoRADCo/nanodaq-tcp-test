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
are shown instead in that case.
"""

from __future__ import annotations

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

TEMP_POLL_INTERVAL = 5.0  # seconds between temperature polls
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
        try:
            full_scale = float(status.fields.get("Full scale", "1"))
        except ValueError:
            full_scale = 1.0
        units = status.fields.get("Press. units", "")
        press_type = status.fields.get("Press. type", "Differential")

        self.out.put((
            "config",
            {"channels": channels, "full_scale": full_scale, "units": units, "press_type": press_type},
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
                # 1) drain any pending GUI requests
                try:
                    while True:
                        cmd = self.commands.get_nowait()
                        if cmd[0] == "stop":
                            stopping = True
                            break
                        elif cmd[0] == "stream_on" and not streaming:
                            client.set_timeout(COMMAND_TIMEOUT)
                            client.stream_on(Channel.TCP_UDP)
                            streaming = True
                        elif cmd[0] == "stream_off" and streaming:
                            client.set_timeout(COMMAND_TIMEOUT)
                            client.stream_off(Channel.TCP_UDP)
                            streaming = False
                        elif cmd[0] == "rezero":
                            client.set_timeout(COMMAND_TIMEOUT)
                            client.rezero()
                except queue.Empty:
                    pass
                if stopping:
                    break

                # 2) periodic temperature poll (pauses streaming briefly -
                #    see module docstring for why)
                now = time.monotonic()
                if streaming and (now - last_temp_poll) >= TEMP_POLL_INTERVAL:
                    last_temp_poll = now
                    try:
                        client.set_timeout(COMMAND_TIMEOUT)
                        client.stream_off(Channel.TCP_UDP)
                        # Raw ADC counts (level 4) - the calibrated "With
                        # temp." level always reports 0.00 on this unit's
                        # firmware, so show the uncalibrated raw value
                        # instead (no conversion to degrees C).
                        raw_temps = client.get_raw_readings(StatusLevel.RAW_TEMP, read_timeout=COMMAND_TIMEOUT)
                        if raw_temps:
                            self.out.put(("temperature", raw_temps))
                        client.stream_on(Channel.TCP_UDP)
                    except Exception as exc:
                        self.out.put(("error", f"temperature poll failed: {exc}"))
                    finally:
                        client.set_timeout(STREAM_READ_TIMEOUT)
                    continue  # let the next loop iteration resume pressure reads

                # 3) pressure stream reads
                if streaming:
                    client.set_timeout(STREAM_READ_TIMEOUT)
                    try:
                        data = client.read_raw()
                    except socket.timeout:
                        continue
                    except OSError as exc:
                        self.out.put(("error", f"connection lost: {exc}"))
                        break
                    buf.feed(data)
                    packet = buf.pop_packet()
                    while packet is not None:
                        values = [scale_differential(v, full_scale) for v in packet.values]
                        self.out.put(("pressure", values))
                        packet = buf.pop_packet()
                else:
                    time.sleep(0.1)
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

        self.status_var = tk.StringVar(value="Disconnected")
        ttk.Label(top, textvariable=self.status_var).grid(row=0, column=7, padx=12)

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
        self.streaming = not self.streaming
        self.worker.request_stream(self.streaming)
        self.stream_btn.configure(text="Stream Off" if self.streaming else "Stream On")

    def on_rezero(self) -> None:
        if self.worker is not None:
            self.worker.request_rezero()

    def on_close(self) -> None:
        if self.worker is not None:
            self.worker.request_stop()
        self.root.after(200, self.root.destroy)

    # -- worker -> GUI updates -------------------------------------------

    def build_channel_grid(self, channels: int, units: str) -> None:
        for child in self.grid_frame.winfo_children():
            child.destroy()
        self.channel_rows = []
        self.units = units

        ttk.Label(self.grid_frame, text="CH", width=4, font=("", 10, "bold")).grid(row=0, column=0)
        ttk.Label(self.grid_frame, text=f"Pressure ({units})", width=16, font=("", 10, "bold")).grid(row=0, column=1)
        ttk.Label(self.grid_frame, text="Temp (raw)", width=10, font=("", 10, "bold")).grid(row=0, column=2)

        for ch in range(channels):
            row = ch + 1
            ttk.Label(self.grid_frame, text=str(ch + 1), width=4).grid(row=row, column=0)
            pressure_var = tk.StringVar(value="--")
            temp_var = tk.StringVar(value="--")
            ttk.Label(self.grid_frame, textvariable=pressure_var, width=16).grid(row=row, column=1)
            ttk.Label(self.grid_frame, textvariable=temp_var, width=10).grid(row=row, column=2)
            self.channel_rows.append({"pressure": pressure_var, "temp": temp_var})

    def poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self.out_queue.get_nowait()
                if kind == "config":
                    self.build_channel_grid(payload["channels"], payload["units"])
                    self.status_var.set(
                        f"Connected ({payload['channels']} ch, "
                        f"FS={payload['full_scale']} {payload['units']}, {payload['press_type']})"
                    )
                    self.connect_btn.configure(text="Disconnect", state="normal")
                    self.stream_btn.configure(state="normal", text="Stream Off")
                    self.rezero_btn.configure(state="normal")
                    self.streaming = True
                elif kind == "pressure":
                    for var_row, value in zip(self.channel_rows, payload):
                        var_row["pressure"].set(f"{value:.4f}")
                elif kind == "temperature":
                    for var_row, value in zip(self.channel_rows, payload):
                        var_row["temp"].set(f"{value:.0f}")
                elif kind == "error":
                    self.log_var.set(str(payload))
                elif kind == "disconnected":
                    self.worker = None
                    self.status_var.set("Disconnected")
                    self.connect_btn.configure(text="Connect", state="normal")
                    self.stream_btn.configure(state="disabled")
                    self.rezero_btn.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(100, self.poll_queue)


def main() -> None:
    root = tk.Tk()
    MonitorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
