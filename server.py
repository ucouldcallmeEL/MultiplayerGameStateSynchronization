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
    build_event_ack_message,
    MSG_EVENT,
    MSG_INIT,
    MSG_GAME_OVER,
    MSG_SNAPSHOT_ACK,
    MSG_EVENT_ACK,
    HEADER_SIZE,
    GRID_SIZE,
    TOTAL_CELLS
)

# === Configuration ===
SERVER_IP = "0.0.0.0"
SERVER_PORT = 9999
GAME_TICK_RATE = 1 / 40.0
ACK_RETRY_DELAY_MS = 40
ACK_RETRY_COUNT = 1


class GridServer:
    def __init__(self, ip=SERVER_IP, port=SERVER_PORT):
        # print(f"[SERVER] Initializing on {ip}:{port}")
        print(f"[SERVER] Initializing on {ip}:{port}", flush=True)
        
        # === Networking ===
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((ip, port))
        self.sock.setblocking(False)
        self.running = True

        # === Game State ===
        self.grid = [[0 for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]
        self.snapshot_id = 0
        self.seq_num = 0
        self.cell_timestamps = {}  # Tracks arbitration timestamps

        # === Lobby State ===
        self.player_assignments = {1: None, 2: None, 3: None, 4: None}
        self.all_clients = set()
        self.game_clients = set()
        self.last_event_ids = {}
        
        # === Thread Safety ===
        self.state_lock = threading.Lock()

        # === Metrics / Logging (Preserved) ===
        self.csv_file = open('metrics.csv', 'w', newline='')
        self.csv_writer = csv.writer(self.csv_file)
        self.csv_writer.writerow([
            'server_timestamp_ms', 'client_id', 'snapshot_id', 
            'seq_num', 'cpu_percent', 'recv_time_ms', 'latency_ms'
        ])

    def send_ack_with_retry(self, ack, addr, retries=ACK_RETRY_COUNT, delay_ms=ACK_RETRY_DELAY_MS):
        """Send an ACK immediately and schedule limited retries to reduce loss impact."""
        try:
            self.sock.sendto(ack, addr)
        except Exception as e:
            print(f"[NETWORK] Error sending EVENT_ACK to {addr}: {e}")
            return

        if retries > 0:
            timer = threading.Timer(delay_ms / 1000.0, lambda: self.send_ack_with_retry(ack, addr, retries - 1, delay_ms))
            timer.daemon = True
            timer.start()

    def start(self):
        """Starts the receive and broadcast threads."""
        print("[SERVER] Starting threads...")
        
        recv_thread = threading.Thread(target=self.receive_loop, daemon=True)
        snapshot_thread = threading.Thread(target=self.game_snapshot_loop, daemon=True)
        
        recv_thread.start()
        snapshot_thread.start()

        print("[SERVER] Server is running. Press Ctrl+C to stop.")
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            self.shutdown()

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
        """
        Flattens the 2D grid into a 64-byte string.
        MUST be called inside a STATE_LOCK.
        """
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
        """Broadcasts game over message and resets the board."""
        for i in range(16500000):
            pass
        
        print(f"[GAME OVER] Broadcasting win for P{winner_id} and resetting.")

        payload = struct.pack("!B", winner_id)
        header = build_header(MSG_GAME_OVER, payload=payload)
        packet = header + payload

        with self.state_lock:
            clients_to_notify = list(self.all_clients)

        for client in clients_to_notify:
            try:
                self.sock.sendto(packet, client)
            except Exception as e:
                print(f"[NETWORK] Error sending GAME_OVER to {client}: {e}")

        # Reset Game State
        self.grid = [[0 for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]
        self.cell_timestamps.clear()

        # Reset Lobby and event tracking
        with self.state_lock:
            self.player_assignments = {1: None, 2: None, 3: None, 4: None}
            self.game_clients.clear()
            self.last_event_ids.clear()  # Reset event IDs for next game

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
        with self.state_lock:
            if addr not in self.game_clients:
                return

            try:
                event = parse_event_payload(payload)
            except struct.error:
                return

            player_id = event["player_id"]
            event_id = event["event_id"]
            cell_id = event["cell_id"]
            event_ts = int(time.time() * 1000)

            if self.player_assignments.get(player_id) != addr:
                print(f"[WARN] Addr {addr} tried to send event as P{player_id}. Mismatch.")
                return

            # Duplicate detection (per player)
            last_seen = self.last_event_ids.get(player_id)
            if last_seen is not None and event_id <= last_seen:
                # Resend ACK for duplicates
                ack = build_event_ack_message(event_id, int(time.time() * 1000), status=1)
                self.send_ack_with_retry(ack, addr)
                return

            # Logic / Arbitration inside lock for consistency
            row = cell_id // GRID_SIZE
            col = cell_id % GRID_SIZE
            status = 0
            winner = None

            if 0 <= row < GRID_SIZE and 0 <= col < GRID_SIZE:
                prev_ts = self.cell_timestamps.get(cell_id)

                if prev_ts is None or event_ts < prev_ts:
                    self.cell_timestamps[cell_id] = event_ts

                    if self.grid[row][col] != player_id:
                        self.grid[row][col] = player_id
                        
                        winner = self.check_for_win_condition()

            # Update last seen event id on valid processing attempt
            if status == 0:
                self.last_event_ids[player_id] = event_id

        # Send ACK BEFORE broadcasting game over to ensure client receives it
        ack = build_event_ack_message(event_id, int(time.time() * 1000), status=status)
        self.send_ack_with_retry(ack, addr)
        
        # Broadcast game over AFTER sending ACK (outside lock)
        if winner:
            self.broadcast_game_over(winner)

    # ============================================================
    # === Thread Loops (Logic + Logging) ===
    # ============================================================

    def game_snapshot_loop(self):
        """Broadcasts game state to active clients."""
        while self.running:
            with self.state_lock:
                if not self.game_clients:
                    time.sleep(GAME_TICK_RATE)
                    continue

                grid_data = self.get_flat_grid_data_unsafe()
                current_clients = list(self.game_clients)

            packet = build_snapshot_message(grid_data, 4, self.snapshot_id, self.seq_num)
            
            # Timestamp for logging
            header_info = parse_header(packet)
            server_ts = header_info['timestamp']

            clients_to_remove = set()

            for client in current_clients:
                try:
                    self.sock.sendto(packet, client)

                    # --- LOGGING (Preserved) ---
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
                    # ---------------------------

                except Exception as e:
                    print(f"[NETWORK] Error sending to {client}: {e}. Removing.")
                    clients_to_remove.add(client)

            if clients_to_remove:
                with self.state_lock:
                    for client in clients_to_remove:
                        self.all_clients.discard(client)
                        self.game_clients.discard(client)
                        for pid, addr in self.player_assignments.items():
                            if addr == client:
                                self.player_assignments[pid] = None

            self.snapshot_id += 1
            self.seq_num += 1
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

                if header["msg_type"] == MSG_INIT:
                    self.handle_player_join(addr)

                elif header["msg_type"] == MSG_EVENT:
                    self.handle_event_message(addr, header, payload)

                elif header["msg_type"] == MSG_SNAPSHOT_ACK:
                    try:
                        ack = parse_snapshot_ack_payload(payload)
                        
                        # --- LOGGING (Preserved) ---
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
                        # ---------------------------
                        
                    except Exception as e:
                        print(f"[ERROR] Failed to handle SNAPSHOT_ACK from {addr}: {e}")

            except (BlockingIOError, ConnectionResetError, ConnectionRefusedError):
                time.sleep(0.001)
                continue
            except Exception as e:
                print(f"[ERROR] Receive loop caught unhandled error: {e}")
                continue


if __name__ == "__main__":
    server = GridServer()
    server.start()