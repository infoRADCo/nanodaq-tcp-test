#!/usr/bin/env python3
"""
Connection test for a Chell nanoDAQ-LT / nanoDAQ-LTS unit over TCP.

Usage:
    python test_connection.py --ip 192.168.1.190 --channels 16 --duration 5

The device's IP address is printed on a label on the unit itself (or can
be read from a previously saved *.cdx setup file / the embedded web
server). The PC's NIC must be on the same subnet as the device
(e.g. device 192.168.1.190/255.255.255.0 <-> PC 192.168.1.x).
"""

import argparse
import sys
import time

from nanodaq_client import Channel, NanoDAQClient, Protocol


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ip", required=True, help="nanoDAQ IP address, e.g. 192.168.1.190")
    parser.add_argument("--port", type=int, default=101, help="TCP port (fixed at 101 on the device)")
    parser.add_argument("--channels", type=int, default=16, help="number of active scanner channels")
    parser.add_argument("--duration", type=float, default=5.0, help="seconds to stream data before stopping")
    parser.add_argument("--max-packets", type=int, default=5, help="number of decoded packets to print")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    print(f"Connecting to nanoDAQ at {args.ip}:{args.port} ...")
    with NanoDAQClient(args.ip, args.port) as client:
        print("Connected.")

        print("Setting protocol to 16-bit little-endian binary ...")
        ok = client.set_protocol(Protocol.BINARY_LE, Channel.TCP_UDP)
        print(f"  -> {'ACK' if ok else 'NACK'}")

        print("Sending Stream ON (TCP/UDP channel) ...")
        ok = client.stream_on(Channel.TCP_UDP)
        print(f"  -> {'ACK' if ok else 'NACK'}")
        if not ok:
            print("Device did not acknowledge Stream ON, aborting.", file=sys.stderr)
            return 1

        print(f"Reading up to {args.max_packets} packets for {args.duration:.1f}s ...")
        start = time.monotonic()
        printed = 0
        for packet in client.iter_binary_packets(args.channels):
            print(f"  packet {printed + 1}: {packet.values}")
            printed += 1
            if printed >= args.max_packets or (time.monotonic() - start) >= args.duration:
                break

        print("Sending Stream OFF ...")
        ok = client.stream_off(Channel.TCP_UDP)
        print(f"  -> {'ACK' if ok else 'NACK'}")

    print("Disconnected. Test complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
