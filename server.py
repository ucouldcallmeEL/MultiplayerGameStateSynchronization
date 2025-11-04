"""
GridClash Server (Prototype)
----------------------------
- Manages a lobby state (player color assignments).
- Manages the game state (grid ownership).
- Broadcasts lobby state at 2Hz.
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
    build_grid_change,
    build_claim_success_message, 
    MSG_SNAPSHOT,
    MSG_EVENT,
    MSG_INIT,
    MSG_LOBBY_STATE,
    MSG_CLAIM_COLOR,
    MSG_CLAIM_SUCCESS,
    MSG_GAME_OVER,
    MSG_SNAPSHOT_ACK,  # Client acknowledgment
    HEADER_SIZE,
)

# === Configuration ===
SERVER_IP = "0.0.0.0"
SERVER_PORT = 9999
GAME_TICK_RATE = 1 / 40.0   # 40 Hz
LOBBY_TICK_RATE = 1 / 2.0   # 2 Hz
GRID_SIZE = 8
TOTAL_CELLS = GRID_SIZE * GRID_SIZE # NEW: Constant for total cells

# === Game State ===
grid = [[0 for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]
snapshot_id = 0
seq_num = 0
# Keep last K=2 snapshot change blobs (flat grid changes) for redundancy in broadcasts
recent_snapshot_changes = []  # list of bytes, most-recent first

# === Lobby State ===
player_assignments = { 1: None, 2: None, 3: None, 4: None }
lobby_clients = set()
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


# === UDP Setup ===
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((SERVER_IP, SERVER_PORT))
sock.setblocking(False)

print(f"[SERVER] Running on {SERVER_IP}:{SERVER_PORT}")


# ============================================================
# === NEW: Win Condition & Game Reset Logic ===
# ============================================================

def check_for_win_condition():
    """
    Checks if the grid is full. If so, finds the winner.
    Returns: Winner's Player ID (int) or None if game is not over.
    """
    scores = {1: 0, 2: 0, 3: 0, 4: 0}
    filled_cells = 0
    
    for r in range(GRID_SIZE):
        for c in range(GRID_SIZE):
            owner = grid[r][c]
            if owner != 0:
                scores[owner] += 1
                filled_cells += 1
                
    # Check if grid is full
    if filled_cells == TOTAL_CELLS:
        # Find the player with the max score
        winner_id = max(scores, key=scores.get)
        max_score = scores[winner_id]
        print(f"[GAME OVER] Grid full. Winner is P{winner_id} with {max_score} tiles.")
        return winner_id
        
    return None # Game not over

def broadcast_game_over(winner_id):
    """
    Broadcasts the winner to all clients and resets the game state.
    """
    global grid, player_assignments, game_clients
    
    print(f"[GAME OVER] Broadcasting win for P{winner_id} and resetting.")
    
    # 1. Build the game over packet
    payload = struct.pack("!B", winner_id)
    header = build_header(MSG_GAME_OVER, payload=payload)
    packet = header + payload
    
    # 2. Send to ALL connected clients
    for client in list(lobby_clients):
        try:
            sock.sendto(packet, client)
        except Exception as e:
            print(f"[NETWORK] Error sending GAME_OVER to {client}: {e}")

    # 3. Reset the server game state
    grid = [[0 for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]
    
    # 4. Kick everyone back to the lobby
    player_assignments = {1: None, 2: None, 3: None, 4: None}
    game_clients.clear()


# ============================================================
# === Message Handlers ===
# ============================================================

def handle_claim_color(addr, payload):
    # ... (This function remains unchanged) ...
    try:
        player_id = struct.unpack("!B", payload)[0]
    except struct.error:
        print(f"[WARN] Invalid CLAIM_COLOR payload from {addr}")
        return

    if player_id not in player_assignments:
        print(f"[WARN] Invalid player_id {player_id} from {addr}")
        return

    if player_assignments[player_id] is None:
        for pid, client_addr in player_assignments.items():
            if client_addr == addr:
                player_assignments[pid] = None
                game_clients.discard(addr)
                print(f"[LOBBY] Client {addr} switched from P{pid}")

        player_assignments[player_id] = addr
        game_clients.add(addr) 
        print(f"[LOBBY] Assigned P{player_id} to {addr}")
        
        msg = build_claim_success_message(player_id)
        sock.sendto(msg, addr)
        
    else:
        print(f"[LOBBY] P{player_id} is already taken. Ignoring claim from {addr}")


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
    # Track the earliest claim timestamp per cell for fairness
    if not hasattr(handle_event_message, "cell_earliest_ts"):
        handle_event_message.cell_earliest_ts = {}
    
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
    event_ts = header["timestamp"]

    if player_assignments.get(player_id) != addr:
        print(f"[WARN] Addr {addr} tried to send event as P{player_id}. Mismatch.")
        return

    row = cell_id // GRID_SIZE
    col = cell_id % GRID_SIZE

    if 0 <= row < GRID_SIZE and 0 <= col < GRID_SIZE:
        cell_ts_map = handle_event_message.cell_earliest_ts
        prev_ts = cell_ts_map.get(cell_id)

        # Decide ownership by earliest header timestamp
        if prev_ts is None or event_ts < prev_ts:
            cell_ts_map[cell_id] = event_ts
            if grid[row][col] != player_id:
                grid[row][col] = player_id
                print(f"[EVENT] Player {player_id} claimed cell {cell_id} (ts={event_ts}) from {addr}")

                # NEW: Check for win condition after a successful move
                winner = check_for_win_condition()
                if winner:
                    broadcast_game_over(winner)
        else:
            print(f"[EVENT] Ignored later claim for cell {cell_id} (ts={event_ts} >= chosen {prev_ts})")
    else:
        print(f"[WARN] Invalid cell_id {cell_id} from {addr}")


# ============================================================
# === Broadcast Loops ===
# ============================================================
def build_snapshot_payload():
    # Build current snapshot flat grid changes
    flat_changes = b""
    for r in range(GRID_SIZE):
        for c in range(GRID_SIZE):
            cell_id = r * GRID_SIZE + c
            new_owner = grid[r][c]
            flat_changes += build_grid_change(cell_id, new_owner)

    # Snapshot redundancy: include last K=2 previous flat change blobs after current
    # Clients expecting only one snapshot will read the first GRID_SIZE*GRID_SIZE changes and ignore the rest
    global recent_snapshot_changes
    extras = b"".join(recent_snapshot_changes[:2])

    num_players = 4 
    payload = struct.pack("!B", num_players) + flat_changes + extras

    # Update recent buffer (store most-recent first)
    recent_snapshot_changes.insert(0, flat_changes)
    if len(recent_snapshot_changes) > 2:
        recent_snapshot_changes = recent_snapshot_changes[:2]

    return payload


def game_snapshot_loop():
    # ... (This function remains unchanged) ...
    global snapshot_id, seq_num
    
    while True:
        if not game_clients:
            time.sleep(GAME_TICK_RATE)
            continue

        payload = build_snapshot_payload()
        header = build_header(MSG_SNAPSHOT, snapshot_id, seq_num, payload)
        packet = header + payload
        
        # Parse header to get the server timestamp that was used
        parsed_header = parse_header(packet)
        server_timestamp_ms = parsed_header["timestamp"]

        for client in list(game_clients):
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
                print(f"[NETWORK] Error sending to {client}: {e}. Removing.")
                game_clients.discard(client)
                for pid, addr in player_assignments.items():
                    if addr == client:
                        player_assignments[pid] = None

        snapshot_id += 1
        seq_num += 1
        time.sleep(GAME_TICK_RATE)


def lobby_broadcast_loop():
    # ... (This function remains unchanged) ...
    while True:
        p1_taken = 1 if player_assignments[1] else 0
        p2_taken = 1 if player_assignments[2] else 0
        p3_taken = 1 if player_assignments[3] else 0
        p4_taken = 1 if player_assignments[4] else 0
        
        payload = struct.pack("!BBBB", p1_taken, p2_taken, p3_taken, p4_taken)
        header = build_header(MSG_LOBBY_STATE, payload=payload)
        packet = header + payload

        for client in list(lobby_clients):
            try:
                sock.sendto(packet, client)
            except Exception as e:
                print(f"[NETWORK] Error sending lobby to {client}: {e}. Removing.")
                lobby_clients.discard(client)
                
        time.sleep(LOBBY_TICK_RATE)

# ============================================================
# === Main Receive Loop ===
# ============================================================
def receive_loop():
    # ... (This function remains unchanged) ...
    while True:
        try:
            data, addr = sock.recvfrom(2048)
            if len(data) < HEADER_SIZE:
                continue

            header = parse_header(data)
            payload = data[HEADER_SIZE:]

            if header["msg_type"] == MSG_INIT:
                print(f"[NETWORK] Received INIT from {addr}. Adding to lobby.")
                lobby_clients.add(addr)

            elif header["msg_type"] == MSG_CLAIM_COLOR:
                handle_claim_color(addr, payload)
            
            elif header["msg_type"] == MSG_EVENT:
                handle_event_message(addr, header, payload)
            
            elif header["msg_type"] == MSG_SNAPSHOT_ACK:
                handle_snapshot_ack(addr, payload)

        except BlockingIOError:
            time.sleep(0.001)
            continue
        except ConnectionResetError:
            print(f"[NETWORK] Client at {addr} disconnected.")
            lobby_clients.discard(addr)
            game_clients.discard(addr)
            for pid, client_addr in player_assignments.items():
                if client_addr == addr:
                    player_assignments[pid] = None
        except Exception as e:
            print(f"[ERROR] Receive loop error: {e}")


# ============================================================
# === Entry Point ===
# ============================================================
if __name__ == "__main__":
    # Initialize CSV logging
    init_csv_logging()
    
    recv_thread = threading.Thread(target=receive_loop, daemon=True)
    recv_thread.start()
    print("[SERVER] Listening for client messages...")

    lobby_thread = threading.Thread(target=lobby_broadcast_loop, daemon=True)
    lobby_thread.start()
    print("[SERVER] Broadcasting lobby state...")

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