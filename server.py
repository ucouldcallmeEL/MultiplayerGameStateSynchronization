"""
GridClash Server (Prototype)
----------------------------
- Manages game state (grid ownership).
- Assigns players to slots on INIT.
- Broadcasts game state at 40Hz to *active* players.
- Detects win condition and resets game.
"""

import socket
import struct
import threading
import time
import csv
import os

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    print("[WARN] psutil not available. CPU monitoring disabled.")

from protocol import (
    parse_header,
    parse_event_payload,
    parse_snapshot_ack_payload,
    build_header,
    build_snapshot_message,
    build_join_response_message,
    MSG_EVENT,
MSG_SNAPSHOT_ACK,
    MSG_INIT,
    MSG_GAME_OVER,
    HEADER_SIZE,
    GRID_SIZE,
    TOTAL_CELLS
)

# === Configuration ===
SERVER_IP = "0.0.0.0"
SERVER_PORT = 9999
GAME_TICK_RATE = 1 / 40.0
# GRID_SIZE and TOTAL_CELLS are now imported from protocol

# === Game State ===
grid = [[0 for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]
snapshot_id = 0
seq_num = 0
# recent_snapshot_changes is no longer needed as we send the full grid
# in a much more efficient format.

# === Lobby State ===
player_assignments = { 1: None, 2: None, 3: None, 4: None }
all_clients = set()
game_clients = set()

# === CSV Logging Setup ===
csv_file = None
csv_writer = None
csv_lock = threading.Lock()

def init_csv_logging():
    """Initialize CSV logging for metrics."""
    global csv_file, csv_writer
    csv_filename = 'metrics.csv'
    csv_file = open(csv_filename, 'w', newline='')
    csv_writer = csv.writer(csv_file)
    # Write header
    csv_writer.writerow([
        'server_timestamp_ms',
        'client_id',
        'snapshot_id',
        'seq_num',
        'cpu_percent',
        'recv_time_ms',  # Will be filled when client sends ACK
        'latency_ms'     # Will be calculated when ACK received
    ])
    csv_file.flush()
    print(f"[METRICS] CSV logging initialized: {csv_filename}")

def log_snapshot_sent(client_id, snapshot_id, seq_num, server_timestamp_ms):
    """Log when a snapshot is sent to a client."""
    global csv_writer, csv_lock

    if csv_writer is None:
        return

    # Get CPU usage (non-blocking)
    cpu_percent = psutil.cpu_percent(interval=None) if PSUTIL_AVAILABLE else 0.0

    with csv_lock:
        csv_writer.writerow([
            server_timestamp_ms,
            client_id,
            snapshot_id,
            seq_num,
            cpu_percent,
            '',  # recv_time_ms - will be filled by ACK handler
            ''   # latency_ms - will be calculated by ACK handler
        ])
        csv_file.flush()

def log_snapshot_ack(client_id, snapshot_id, server_timestamp_ms, recv_time_ms):
    """Log when client acknowledges receiving a snapshot (for latency calculation)."""
    global csv_writer, csv_lock

    if csv_writer is None:
        return

    latency_ms = recv_time_ms - server_timestamp_ms if recv_time_ms and server_timestamp_ms else ''

    with csv_lock:
        # Write a separate row for ACK (or we could update the original row, but CSV is append-only)
        csv_writer.writerow([
            server_timestamp_ms,
            client_id,
            snapshot_id,
            '',  # seq_num not needed for ACK
            '',  # cpu_percent not needed for ACK
            recv_time_ms,
            latency_ms
        ])
        csv_file.flush()

# NEW: Threading lock to protect shared state
STATE_LOCK = threading.Lock()

# === UDP Setup ===
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((SERVER_IP, SERVER_PORT))
sock.setblocking(False)

print(f"[SERVER] Running on {SERVER_IP}:{SERVER_PORT}")


# ============================================================
# === Win Condition & Game Reset Logic ===
# ============================================================

def check_for_win_condition():
    # This function only *reads* from 'grid', which is only modified
    # by 'handle_event_message'. We'll put the lock there.
    # For now, this read-only access is *mostly* safe, but a lock
    # in handle_event_message is still needed.
    scores = {1: 0, 2: 0, 3: 0, 4: 0}
    filled_cells = 0

    for r in range(GRID_SIZE):
        for c in range(GRID_SIZE):
            owner = grid[r][c]
            if owner != 0:
                scores[owner] += 1
                filled_cells += 1

    if filled_cells == TOTAL_CELLS:
        # Find the player with the max score
        winner_id = max(scores, key=scores.get)
        max_score = scores[winner_id]
        print(f"[GAME OVER] Grid full. Winner is P{winner_id} with {max_score} tiles.")
        return winner_id

    return None

def broadcast_game_over(winner_id):
    global grid, player_assignments, game_clients

    print(f"[GAME OVER] Broadcasting win for P{winner_id} and resetting.")

    payload = struct.pack("!B", winner_id)
    header = build_header(MSG_GAME_OVER, payload=payload)
    packet = header + payload

    # We must lock here to safely copy the client list
    with STATE_LOCK:
        clients_to_notify = list(all_clients)

    for client in clients_to_notify:
        try:
            sock.sendto(packet, client)
        except Exception as e:
            print(f"[NETWORK] Error sending GAME_OVER to {client}: {e}")

    # Reset the server game state (grid)
    global grid
    grid = [[0 for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]

    # Reset assignments (kick everyone back to lobby)
    # This is a critical section
    with STATE_LOCK:
        player_assignments = {1: None, 2: None, 3: None, 4: None}
        game_clients.clear()


# ============================================================
# === Message Handlers ===
# ============================================================

# --- NEW: Helper function to get grid data as bytes ---
def get_flat_grid_data_unsafe():
    """
    Flattens the 2D grid into a 64-byte string.
    This avoids using struct.pack in the server logic.
    MUST be called inside a STATE_LOCK.
    """
    flat_bytes = bytearray(TOTAL_CELLS)
    idx = 0
    for r in range(GRID_SIZE):
        for c in range(GRID_SIZE):
            flat_bytes[idx] = grid[r][c]
            idx += 1
    return bytes(flat_bytes)


def handle_player_join(addr):
    global player_assignments, game_clients, all_clients

    # CRITICAL SECTION: Modifying shared state
    with STATE_LOCK:
        all_clients.add(addr)

        # Check if this client *already* has a player slot
        for pid, client_addr in player_assignments.items():
            if client_addr == addr:
                print(f"[NETWORK] Client {addr} (P{pid}) sent INIT again. Resending state.")

                # --- REFACTORED ---
                # Build and send the JOIN_RESPONSE using the new protocol function
                grid_data = get_flat_grid_data_unsafe() # We are in a lock
                packet = build_join_response_message(pid, grid_data)
                sock.sendto(packet, addr)
                # ---
                return

        # Find the first available player_id
        assigned_pid = None
        for pid, client_addr in player_assignments.items():
            if client_addr is None:
                assigned_pid = pid
                break

        if assigned_pid:
            player_assignments[assigned_pid] = addr
            game_clients.add(addr)
            print(f"[LOBBY] Assigned P{assigned_pid} to {addr}")

            # --- REFACTORED ---
            # Build and send the JOIN_RESPONSE using the new protocol function
            grid_data = get_flat_grid_data_unsafe() # We are in a lock
            packet = build_join_response_message(assigned_pid, grid_data)
            sock.sendto(packet, addr)
            # ---

        else:
            print(f"[LOBBY] Server is full. Ignoring INIT from {addr}")


def handle_snapshot_ack(addr, payload):
    """Handle client acknowledgment of snapshot receipt."""
    try:
        ack_data = parse_snapshot_ack_payload(payload)
        snapshot_id = ack_data["snapshot_id"]
        server_timestamp_ms = ack_data["server_timestamp_ms"]
        recv_time_ms = ack_data["recv_time_ms"]

        # Find client_id from player_assignments
        client_id = None
        for pid, client_addr in player_assignments.items():
            if client_addr == addr:
                client_id = pid
                break

        if client_id:
            log_snapshot_ack(client_id, snapshot_id, server_timestamp_ms, recv_time_ms)
        else:
            print(f"[WARN] Received ACK from unknown client {addr}")
    except struct.error:
        print(f"[WARN] Failed to parse SNAPSHOT_ACK from {addr}")
    except Exception as e:
        print(f"[ERROR] Error handling SNAPSHOT_ACK: {e}")


def handle_event_message(addr, header, payload):
    """Client sent an in-game event (e.g., click)."""
    global grid
    if not hasattr(handle_event_message, "cell_earliest_ts"):
        handle_event_message.cell_earliest_ts = {}

    # CRITICAL SECTION: Check player assignments
    with STATE_LOCK:
        if addr not in game_clients:
            print(f"[WARN] Event from non-game client {addr}. Ignoring.")
            return

        try:
            event = parse_event_payload(payload)
        except struct.error:
            print(f"[WARN] Failed to parse EVENT from {addr}")
            return

        player_id = event["player_id"]
        cell_id = event["cell_id"]
        event_ts = header["timestamp"] # Using header timestamp for event ordering

        if player_assignments.get(player_id) != addr:
            print(f"[WARN] Addr {addr} tried to send event as P{player_id}. Mismatch.")
            return

    # Non-critical section (no shared state modification)
    row = cell_id // GRID_SIZE
    col = cell_id % GRID_SIZE

    if 0 <= row < GRID_SIZE and 0 <= col < GRID_SIZE:
        cell_ts_map = handle_event_message.cell_earliest_ts
        prev_ts = cell_ts_map.get(cell_id)

        if prev_ts is None or event_ts < prev_ts:
            cell_ts_map[cell_id] = event_ts

            if grid[row][col] != player_id:
                grid[row][col] = player_id
                print(f"[EVENT] Player {player_id} claimed cell {cell_id} (ts={event_ts}) from {addr}")

                winner = check_for_win_condition()
                if winner:
                    broadcast_game_over(winner)
        else:
            owner_id = grid[row][col]
            if owner_id != 0:
                print(f"[EVENT] Ignored later claim from P{player_id} for cell {cell_id} (ts={event_ts} >= chosen {prev_ts}). Cell is already owned by Player {owner_id}.")
            else:
                print(f"[EVENT] Ignored later claim from P{player_id} for cell {cell_id} (ts={event_ts} >= chosen {prev_ts}). Cell is processing.")
    else:
        print(f"[WARN] Invalid cell_id {cell_id} from {addr}")

# --- REMOVED build_snapshot_payload_unsafe ---
# This is now handled by get_flat_grid_data_unsafe()
# and build_snapshot_message() in protocol.py

# ============================================================
# === Broadcast Loops ===
# ============================================================

def game_snapshot_loop():
    global snapshot_id, seq_num

    while True:
        # CRITICAL SECTION: Get a copy of the clients
        with STATE_LOCK:
            if not game_clients:
                # No one is in the game, just sleep
                time.sleep(GAME_TICK_RATE)
                continue

            # --- REFACTORED ---
            # Get the raw grid data
            grid_data = get_flat_grid_data_unsafe()
            # Get a copy of clients to message
            current_clients = list(game_clients)
            # ---

        # Now send packets *outside* the lock

        # --- REFACTORED ---
        # Build the full packet using the new protocol function
        packet = build_snapshot_message(grid_data, 4, snapshot_id, seq_num)
        parsed_header = parse_header(packet)
        server_timestamp_ms = parsed_header["timestamp"]
        # ---

        clients_to_remove = set()

        for client in current_clients:
            try:
                sock.sendto(packet, client)

                # Log the snapshot send
                # Find client_id from player_assignments
                client_id = None
                for pid, addr in player_assignments.items():
                    if addr == client:
                        client_id = pid
                        break

                if client_id:
                    log_snapshot_sent(client_id, snapshot_id, seq_num, server_timestamp_ms)

            except Exception as e:
                # This client is dead. Mark them for removal.
                print(f"[NETWORK] Error sending to {client}: {e}. Removing.")
                clients_to_remove.add(client)

        # CRITICAL SECTION: Remove all dead clients
        if clients_to_remove:
            with STATE_LOCK:
                for client in clients_to_remove:
                    all_clients.discard(client)
                    game_clients.discard(client)
                    for pid, addr in player_assignments.items():
                        if addr == client:
                            player_assignments[pid] = None

        snapshot_id += 1
        seq_num += 1
        time.sleep(GAME_TICK_RATE)


# ============================================================
# === Main Receive Loop ===
# ============================================================
def receive_loop():
    while True:
        try:
            data, addr = sock.recvfrom(2048)
            if len(data) < HEADER_SIZE:
                continue

            header = parse_header(data)
            payload = data[HEADER_SIZE:]

            if header["msg_type"] == MSG_INIT:
                handle_player_join(addr)

            elif header["msg_type"] == MSG_EVENT:
                handle_event_message(addr, header, payload)

            elif header["msg_type"] == MSG_SNAPSHOT_ACK:
                handle_snapshot_ack(addr, payload)

        except (BlockingIOError, ConnectionResetError, ConnectionRefusedError):
            # These are harmless UDP errors.
            time.sleep(0.001)
            continue

        except Exception as e:
            # A more serious error, but we still don't want to crash the loop
            print(f"[ERROR] Receive loop caught unhandled error: {e}")
            continue


# ============================================================
# === Entry Point ===
# ============================================================
if __name__ == "__main__":
    # Initialize CSV logging
    init_csv_logging()

    recv_thread = threading.Thread(target=receive_loop, daemon=True)
    recv_thread.start()
    print("[SERVER] Listening for client messages...")

    snapshot_thread = threading.Thread(target=game_snapshot_loop, daemon=True)
    snapshot_thread.start()
    print("[SERVER] Broadcasting game state...")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[SERVER] Shutting down.")

        if csv_file:
            csv_file.close()