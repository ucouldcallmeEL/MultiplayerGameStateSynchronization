"""
GridClash Server (Prototype)
----------------------------
Authoritative UDP server for GCP1.0.
- Receives EVENT messages from clients.
- Updates the game state (grid ownership).
- Broadcasts periodic SNAPSHOTs at 40Hz to all connected clients.
"""

import socket
import struct
import threading
import time

from protocol import (
    parse_header,
    parse_event_payload,
    build_header,
    build_grid_change,
    MSG_SNAPSHOT,
    MSG_EVENT,
    HEADER_SIZE,
)

# === Configuration ===
SERVER_IP = "0.0.0.0"
SERVER_PORT = 9999
TICK_RATE = 1 / 40.0  # 40 Hz -> 25 ms per tick
GRID_SIZE = 8

# === Game State ===
grid = [[0 for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]
clients = set()  # list of (ip, port)
snapshot_id = 0
seq_num = 0

# === UDP Setup ===
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((SERVER_IP, SERVER_PORT))
sock.setblocking(False)

print(f"[SERVER] Running on {SERVER_IP}:{SERVER_PORT}")


# ============================================================
# === Event Handling ===
# ============================================================
def handle_event_message(addr, header, payload):
    global grid
    event = parse_event_payload(payload)
    player_id = event["player_id"]
    cell_id = event["cell_id"]

    row = cell_id // GRID_SIZE
    col = cell_id % GRID_SIZE

    # Simple rule: if empty, claim the cell
    if grid[row][col] == 0:
        grid[row][col] = player_id
        print(f"[EVENT] Player {player_id} claimed cell {cell_id}")
    else:
        print(f"[EVENT] Cell {cell_id} already owned.")

    clients.add(addr)  # register client


# ============================================================
# === Snapshot Broadcast ===
# ============================================================
def build_snapshot_payload():
    """
    Builds a simple snapshot payload:
    [num_players (B)] [grid_changes...]
    For prototype: include all grid cells each tick.
    """
    flat_changes = b""
    for r in range(GRID_SIZE):
        for c in range(GRID_SIZE):
            cell_id = r * GRID_SIZE + c
            new_owner = grid[r][c]
            flat_changes += build_grid_change(cell_id, new_owner)

    num_players = 4
    payload = struct.pack("!B", num_players) + flat_changes
    return payload


def broadcast_snapshot():
    global snapshot_id, seq_num
    payload = build_snapshot_payload()
    header = build_header(MSG_SNAPSHOT, snapshot_id, seq_num, payload)
    packet = header + payload

    for client in clients:
        sock.sendto(packet, client)

    snapshot_id += 1
    seq_num += 1


# ============================================================
# === Networking Threads ===
# ============================================================
def receive_loop():
    """Continuously listen for incoming EVENT messages."""
    while True:
        try:
            data, addr = sock.recvfrom(2048)
            if len(data) < HEADER_SIZE:
                continue

            header = parse_header(data)
            payload = data[HEADER_SIZE:]

            if header["msg_type"] == MSG_EVENT:
                handle_event_message(addr, header, payload)
        except BlockingIOError:
            time.sleep(0.001)
            continue


def snapshot_loop():
    """Broadcast state updates 40 times per second."""
    while True:
        broadcast_snapshot()
        time.sleep(TICK_RATE)


# ============================================================
# === Entry Point ===
# ============================================================
if __name__ == "__main__":
    recv_thread = threading.Thread(target=receive_loop, daemon=True)
    recv_thread.start()

    print("[SERVER] Listening for client EVENT messages...")

    snapshot_thread = threading.Thread(target=snapshot_loop, daemon=True)
    snapshot_thread.start()

    # Keep running
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[SERVER] Shutting down.")
