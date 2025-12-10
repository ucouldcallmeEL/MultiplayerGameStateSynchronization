import socket
import struct
import threading
import time
import csv

# PSUTIL Import (Preserved for logging)
try:
    import psutil

    _PSUTIL = True
except Exception:
    _PSUTIL = False

from protocol import (
    parse_header,
    parse_event_payload,
    parse_snapshot_ack_payload,
    build_header,
    build_snapshot_message,
    build_join_response_message,
    MSG_EVENT,
    MSG_INIT,
    MSG_GAME_OVER,
    MSG_SNAPSHOT_ACK,
    HEADER_SIZE,
    GRID_SIZE,
    TOTAL_CELLS,
    MSG_GENERIC_ACK,
    build_ack_message,
    parse_ack_payload,
    MSG_CELL_UPDATE,
    build_cell_update_message
)

# === Configuration ===
SERVER_IP = "0.0.0.0"
SERVER_PORT = 9999
GAME_TICK_RATE = 1 / 40.0


class GridServer:
    def __init__(self, ip=SERVER_IP, port=SERVER_PORT):
        print(f"[SERVER] Initializing on {ip}:{port}")

        # === Networking ===
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((ip, port))
        self.sock.setblocking(False)
        self.running = True

        # === Game State ===
        self.grid = [[0 for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]
        self.snapshot_id = 0
        self.seq_num = 0  # Server's global sequence number
        self.cell_timestamps = {}  # Tracks arbitration timestamps

        # === Reliability State ===
        self.reliable_buffer = {}  # { seq_num: {packet, addr, last_sent, retries} }
        self.client_last_processed_seq = {}  # { (ip, port): last_seq_num } -> For De-duplication

        # === Lobby State ===
        self.player_assignments = {1: None, 2: None, 3: None, 4: None}
        self.all_clients = set()
        self.game_clients = set()

        # === Thread Safety ===
        self.state_lock = threading.RLock()

        # === Metrics / Logging (Preserved) ===
        self.csv_file = open('metrics.csv', 'w', newline='')
        self.csv_writer = csv.writer(self.csv_file)
        self.csv_writer.writerow([
            'server_timestamp_ms', 'client_id', 'snapshot_id',
            'seq_num', 'cpu_percent', 'recv_time_ms', 'latency_ms'
        ])

    def start(self):
        """Starts the receive and broadcast threads."""
        print("[SERVER] Starting threads...")

        recv_thread = threading.Thread(target=self.receive_loop, daemon=True)
        snapshot_thread = threading.Thread(target=self.game_snapshot_loop, daemon=True)

        ### Reliability thread ###
        retry_thread = threading.Thread(target=self.reliability_loop, daemon=True)
        retry_thread.start()
        # ______________________#

        recv_thread.start()
        snapshot_thread.start()

        print("[SERVER] Server is running. Press Ctrl+C to stop.")
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            self.shutdown()

    # ============================================================
    # === Reliability Logic (ARQ) ===
    # ============================================================

    def send_reliable(self, packet, addr, seq_num):
        """Sends a packet and adds it to the retry buffer."""
        # 1. Send immediately
        self.sock.sendto(packet, addr)

        # 2. Add to buffer for retries
        with self.state_lock:
            self.reliable_buffer[seq_num] = {
                'packet': packet,
                'addr': addr,
                'last_sent': time.time(),
                'retries': 0
            }

    def reliability_loop(self):
        """Checks buffer every 40ms and resends if no ACK received."""
        while self.running:
            time.sleep(0.04)  # Check frequency
            now = time.time()

            with self.state_lock:
                # Iterate over a copy of keys to avoid modification errors
                for seq in list(self.reliable_buffer.keys()):
                    data = self.reliable_buffer[seq]

                    # If 300ms passed since last send (Retry Timeout)
                    if now - data['last_sent'] > 0.3:
                        if data['retries'] < 5:
                            # print(f"[RELIABLE] Resending Seq {seq} to {data['addr']}")
                            self.sock.sendto(data['packet'], data['addr'])
                            data['last_sent'] = now
                            data['retries'] += 1
                        else:
                            # print(f"[RELIABLE] Gave up on Seq {seq} to {data['addr']}")
                            del self.reliable_buffer[seq]

    def handle_ack(self, payload):
        """Remove packet from buffer if acknowledged."""
        acked_seq = parse_ack_payload(payload)
        with self.state_lock:
            if acked_seq in self.reliable_buffer:
                # print(f"[RELIABLE] Confirmed delivery of {acked_seq}")
                del self.reliable_buffer[acked_seq]

    def shutdown(self):
        """Cleanly shuts down resources."""
        print("\n[SERVER] Shutting down.")
        self.running = False
        try:
            self.csv_file.close()
        except Exception:
            pass
        self.sock.close()

    # ============================================================
    # === Core Logic Helpers ===
    # ============================================================

    def get_flat_grid_data_unsafe(self):
        """Flattens the 2D grid into a 64-byte string. MUST be called inside STATE_LOCK."""
        flat_bytes = bytearray(TOTAL_CELLS)
        idx = 0
        for r in range(GRID_SIZE):
            for c in range(GRID_SIZE):
                flat_bytes[idx] = self.grid[r][c]
                idx += 1
        return bytes(flat_bytes)

    def check_for_win_condition(self):
        """Checks if grid is full and returns winner ID."""
        scores = {1: 0, 2: 0, 3: 0, 4: 0}
        filled_cells = 0

        for r in range(GRID_SIZE):
            for c in range(GRID_SIZE):
                owner = self.grid[r][c]
                if owner != 0:
                    scores[owner] += 1
                    filled_cells += 1

        if filled_cells == TOTAL_CELLS:
            winner_id = max(scores, key=scores.get)
            max_score = scores[winner_id]
            print(f"[GAME OVER] Grid full. Winner is P{winner_id} with {max_score} tiles.")
            return winner_id

        return None

    def broadcast_game_over(self, winner_id):
        print(f"[GAME OVER] Broadcasting RELIABLE win for P{winner_id}")

        payload = struct.pack("!B", winner_id)

        # We need unique sequence numbers for the reliable map
        current_seq = self.seq_num
        self.seq_num += 1

        header = build_header(MSG_GAME_OVER, seq_num=current_seq, payload=payload)
        packet = header + payload

        with self.state_lock:
            clients = list(self.all_clients)

        for client in clients:
            self.send_reliable(packet, client, current_seq)

        # Reset Game State
        self.grid = [[0 for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]
        self.cell_timestamps.clear()

        # Reset Logic State
        with self.state_lock:
            self.player_assignments = {1: None, 2: None, 3: None, 4: None}
            self.game_clients.clear()
            self.client_last_processed_seq.clear()  # Clear deduplication history

    def broadcast_cell_update(self, row, col, owner_id):
        """Sends reliable update to ALL players."""

        # 1. Prepare Packet
        self.seq_num += 1
        curr_seq = self.seq_num
        packet = build_cell_update_message(curr_seq, row, col, owner_id)

        # 2. Send to all Game Clients
        with self.state_lock:
            clients = list(self.game_clients)

        for client_addr in clients:
            # We use the SAME sequence number for everyone for this specific update event
            # Note: In a highly complex system we might want per-client sequence numbers,
            # but for this scale, a global server sequence number works fine.
            self.send_reliable(packet, client_addr, curr_seq)

    # ============================================================
    # === Message Handlers ===
    # ============================================================

    def handle_player_join(self, addr):
        with self.state_lock:
            self.all_clients.add(addr)

            # Check re-join
            for pid, client_addr in self.player_assignments.items():
                if client_addr == addr:
                    print(f"[NETWORK] Client {addr} (P{pid}) sent INIT again. Resending state.")
                    grid_data = self.get_flat_grid_data_unsafe()
                    packet = build_join_response_message(pid, grid_data)
                    self.sock.sendto(packet, addr)
                    return

            # Assign new slot
            assigned_pid = None
            for pid, client_addr in self.player_assignments.items():
                if client_addr is None:
                    assigned_pid = pid
                    break

            if assigned_pid:
                self.player_assignments[assigned_pid] = addr
                self.game_clients.add(addr)
                print(f"[LOBBY] Assigned P{assigned_pid} to {addr}")

                grid_data = self.get_flat_grid_data_unsafe()
                packet = build_join_response_message(assigned_pid, grid_data)
                self.sock.sendto(packet, addr)
            else:
                print(f"[LOBBY] Server is full. Ignoring INIT from {addr}")

    def handle_event_message(self, addr, header, payload):
        """Handles reliable inputs from clients."""

        # 1. EXTRACT SEQ & ACK IMMEDIATELY
        # We must ACK the client's sequence number so they stop retrying.
        client_seq = header['seq_num']
        ack_msg = build_ack_message(client_seq)
        self.sock.sendto(ack_msg, addr)

        with self.state_lock:
            if addr not in self.game_clients:
                return

            # 2. DEDUPLICATION (Traffic Protection)
            # If we already processed this sequence number from this client, ignore the logic.
            last_seq = self.client_last_processed_seq.get(addr, -1)
            if client_seq <= last_seq:
                # print(f"[Server] Ignoring duplicate event {client_seq} from {addr}")
                return

            # Mark as processed
            self.client_last_processed_seq[addr] = client_seq

            # 3. GAME LOGIC
            try:
                event = parse_event_payload(payload)
                player_id = event["player_id"]
                cell_id = event["cell_id"]
                event_ts = header["timestamp"]

                if self.player_assignments.get(player_id) != addr:
                    print(f"[WARN] Addr {addr} tried to send event as P{player_id}. Mismatch.")
                    return

                row = cell_id // GRID_SIZE
                col = cell_id % GRID_SIZE

                if 0 <= row < GRID_SIZE and 0 <= col < GRID_SIZE:
                    prev_ts = self.cell_timestamps.get(cell_id)

                    # Arbitration: Only allow if newer or unclaimed
                    if prev_ts is None or event_ts < prev_ts:
                        self.cell_timestamps[cell_id] = event_ts

                        # 4. STATE UPDATE & BROADCAST
                        if self.grid[row][col] != player_id:
                            self.grid[row][col] = player_id

                            # === Broadcast to all clients reliably ===
                            self.broadcast_cell_update(row, col, player_id)

                            winner = self.check_for_win_condition()
                            if winner:
                                self.broadcast_game_over(winner)
            except Exception as e:
                print(f"[ERROR] Logic failed: {e}")

    # ============================================================
    # === Thread Loops (Logic + Logging) ===
    # ============================================================

    def game_snapshot_loop(self):
        """Broadcasts full game state (redundancy/late joiners)."""
        while self.running:
            with self.state_lock:
                if not self.game_clients:
                    time.sleep(GAME_TICK_RATE)
                    continue

                grid_data = self.get_flat_grid_data_unsafe()
                current_clients = list(self.game_clients)

            # Note: Snapshots are Unreliable (Fire-and-forget)
            packet = build_snapshot_message(grid_data, 4, self.snapshot_id, self.seq_num)

            header_info = parse_header(packet)
            server_ts = header_info['timestamp']

            clients_to_remove = set()

            for client in current_clients:
                try:
                    self.sock.sendto(packet, client)

                    # --- LOGGING ---
                    client_id = None
                    with self.state_lock:
                        for pid, addr in self.player_assignments.items():
                            if addr == client:
                                client_id = pid
                                break

                    cpu_percent = psutil.cpu_percent(interval=None) if _PSUTIL else ''
                    self.csv_writer.writerow([
                        server_ts, client_id or '', self.snapshot_id,
                        self.seq_num, cpu_percent, '', ''
                    ])
                    self.csv_file.flush()
                    # ---------------

                except Exception as e:
                    print(f"[NETWORK] Error sending to {client}: {e}. Removing.")
                    clients_to_remove.add(client)

            if clients_to_remove:
                with self.state_lock:
                    for client in clients_to_remove:
                        self.all_clients.discard(client)
                        self.game_clients.discard(client)
                        self.client_last_processed_seq.pop(client, None)  # Clean up history
                        for pid, addr in self.player_assignments.items():
                            if addr == client:
                                self.player_assignments[pid] = None

            self.snapshot_id += 1
            # We don't increment seq_num here for snapshots to avoid confusing the ARQ system
            # or we can use a separate sequence counter for snapshots if needed.
            time.sleep(GAME_TICK_RATE)

    def receive_loop(self):
        """Handles incoming UDP packets."""
        while self.running:
            try:
                data, addr = self.sock.recvfrom(2048)
                if len(data) < HEADER_SIZE:
                    continue

                header = parse_header(data)
                payload = data[HEADER_SIZE:]
                msg_type = header["msg_type"]

                if msg_type == MSG_INIT:
                    self.handle_player_join(addr)

                elif msg_type == MSG_EVENT:
                    self.handle_event_message(addr, header, payload)

                elif msg_type == MSG_GENERIC_ACK:
                    self.handle_ack(payload)

                elif msg_type == MSG_SNAPSHOT_ACK:
                    try:
                        ack = parse_snapshot_ack_payload(payload)
                        # Metrics logging (latency)
                        client_id = None
                        with self.state_lock:
                            for pid, a in self.player_assignments.items():
                                if a == addr:
                                    client_id = pid
                                    break
                        latency = ack['recv_time_ms'] - ack['server_timestamp_ms']
                        self.csv_writer.writerow([
                            ack['server_timestamp_ms'], client_id or '', ack['snapshot_id'],
                            '', '', ack['recv_time_ms'], latency
                        ])
                        self.csv_file.flush()
                    except Exception:
                        pass

            except (BlockingIOError, ConnectionResetError, ConnectionRefusedError):
                time.sleep(0.001)
                continue
            except Exception as e:
                print(f"[ERROR] Receive loop caught unhandled error: {e}")
                continue


if __name__ == "__main__":
    server = GridServer()
    server.start()