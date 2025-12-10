import socket
import struct
import threading
import time
from protocol import *

# === Configuration ===
SERVER_IP = "0.0.0.0"
SERVER_PORT = 9999
GAME_TICK_RATE = 0.025  # 25ms (approx 40 ticks/sec)


class GridServer:
    def __init__(self, ip=SERVER_IP, port=SERVER_PORT):
        print(f"[SERVER] Unreliable Snapshot (Heartbeat) Mode on {ip}:{port}")

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((ip, port))
        self.sock.setblocking(False)
        self.running = True

        # === Game State ===
        self.grid = [[0 for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]
        self.snapshot_id = 0
        self.seq_num = 1000
        self.cell_timestamps = {}

        # === Reliability ===
        self.reliable_buffer = {}
        self.client_last_processed_seq = {}
        self.state_lock = threading.RLock()

        # === Lobby ===
        self.player_assignments = {1: None, 2: None, 3: None, 4: None}
        self.game_clients = set()
        self.all_clients = set()

    def start(self):
        threading.Thread(target=self.receive_loop, daemon=True).start()
        threading.Thread(target=self.reliability_loop, daemon=True).start()

        # This loop sends the Unreliable Snapshot Heartbeat
        threading.Thread(target=self.game_snapshot_loop, daemon=True).start()

        print("[SERVER] Running. Press Ctrl+C to stop.")
        try:
            while self.running: time.sleep(1)
        except KeyboardInterrupt:
            self.shutdown()

    # ============================================================
    # === UNRELIABLE SNAPSHOT LOOP (HEARTBEAT) ===
    # ============================================================
    def game_snapshot_loop(self):
        """
        Sends the FULL grid state every 25ms.
        This acts as the HEARTBEAT.
        It is UNRELIABLE (uses sock.sendto, not send_reliable).
        """
        while self.running:
            time.sleep(GAME_TICK_RATE)

            with self.state_lock:
                if not self.game_clients: continue
                grid_data = self.get_flat_grid_data_unsafe()
                clients = list(self.game_clients)

            # Build Packet
            self.snapshot_id += 1
            packet = build_snapshot_message(grid_data, self.snapshot_id)

            # Broadcast Unreliably
            for client in clients:
                try:
                    self.sock.sendto(packet, client)
                except:
                    pass

    # ============================================================
    # === RELIABILITY ENGINE (For Critical Packets Only) ===
    # ============================================================
    def send_reliable(self, packet, addr, seq_num):
        """Used for Events and Cell Updates, NOT Snapshots."""
        self.sock.sendto(packet, addr)
        with self.state_lock:
            self.reliable_buffer[seq_num] = {
                'packet': packet,
                'addr': addr,
                'last_sent': time.time(),
                'retries': 0
            }

    def reliability_loop(self):
        while self.running:
            time.sleep(0.05)
            now = time.time()
            with self.state_lock:
                for seq in list(self.reliable_buffer.keys()):
                    data = self.reliable_buffer[seq]
                    if now - data['last_sent'] > 0.3:  # Retry timeout
                        if data['retries'] < 10:
                            self.sock.sendto(data['packet'], data['addr'])
                            data['last_sent'] = now
                            data['retries'] += 1
                        else:
                            del self.reliable_buffer[seq]

    def handle_ack(self, payload):
        acked_seq = parse_ack_payload(payload)
        with self.state_lock:
            if acked_seq in self.reliable_buffer:
                del self.reliable_buffer[acked_seq]

    # ============================================================
    # === Game Logic ===
    # ============================================================
    def handle_event(self, addr, header, payload):
        client_seq = header['seq_num']
        self.sock.sendto(build_ack_message(client_seq), addr)  # Immediate ACK

        with self.state_lock:
            if addr not in self.game_clients: return

            # Deduplicate
            if client_seq <= self.client_last_processed_seq.get(addr, -1): return
            self.client_last_processed_seq[addr] = client_seq

            # Process Move
            data = parse_event_payload(payload)
            r, c = data['cell_id'] // GRID_SIZE, data['cell_id'] % GRID_SIZE
            pid = data['player_id']
            ts = header['timestamp']

            prev_ts = self.cell_timestamps.get(data['cell_id'])
            if prev_ts is None or ts < prev_ts:  # Arbitration
                self.cell_timestamps[data['cell_id']] = ts

                if self.grid[r][c] != pid:
                    self.grid[r][c] = pid
                    # Critical: Broadcast Update Reliably
                    self.broadcast_cell_update(r, c, pid)

                    winner = self.check_win()
                    if winner: self.broadcast_win(winner)

    def broadcast_cell_update(self, r, c, pid):
        self.seq_num += 1
        base = self.seq_num
        packet = build_cell_update_message(base, r, c, pid)
        with self.state_lock:
            for i, client in enumerate(self.game_clients):
                self.send_reliable(packet, client, base + i)

    def broadcast_win(self, winner):
        print(f"[GAME OVER] Winner: {winner}")
        self.seq_num += 1
        packet = build_game_over_message(winner, self.seq_num)
        with self.state_lock:
            for i, client in enumerate(self.game_clients):
                self.send_reliable(packet, client, self.seq_num + i)

        # Reset
        self.grid = [[0] * GRID_SIZE for _ in range(GRID_SIZE)]
        self.cell_timestamps.clear()
        self.player_assignments = {k: None for k in self.player_assignments}
        self.game_clients.clear()

    def check_win(self):
        counts = {1: 0, 2: 0, 3: 0, 4: 0}
        total = 0
        for r in range(GRID_SIZE):
            for c in range(GRID_SIZE):
                if self.grid[r][c] != 0:
                    counts[self.grid[r][c]] += 1
                    total += 1
        if total == TOTAL_CELLS:
            return max(counts, key=counts.get)
        return None

    def get_flat_grid_data_unsafe(self):
        flat = bytearray(TOTAL_CELLS)
        idx = 0
        for r in range(GRID_SIZE):
            for c in range(GRID_SIZE):
                flat[idx] = self.grid[r][c]
                idx += 1
        return bytes(flat)

    # ============================================================
    # === Receiver ===
    # ============================================================
    def receive_loop(self):
        while self.running:
            try:
                data, addr = self.sock.recvfrom(2048)
                if len(data) < HEADER_SIZE: continue

                header = parse_header(data)
                payload = data[HEADER_SIZE:]
                mtype = header['msg_type']

                if mtype == MSG_INIT:
                    self.handle_join(addr)
                elif mtype == MSG_EVENT:
                    self.handle_event(addr, header, payload)
                elif mtype == MSG_GENERIC_ACK:
                    self.handle_ack(payload)
            except Exception:
                pass

    def handle_join(self, addr):
        with self.state_lock:
            self.all_clients.add(addr)
            for pid, paddr in self.player_assignments.items():
                if paddr == addr:
                    self.sock.sendto(build_join_response_message(pid, self.get_flat_grid_data_unsafe()), addr)
                    return

            for pid, paddr in self.player_assignments.items():
                if paddr is None:
                    self.player_assignments[pid] = addr
                    self.game_clients.add(addr)
                    print(f"[JOIN] P{pid} -> {addr}")
                    self.sock.sendto(build_join_response_message(pid, self.get_flat_grid_data_unsafe()), addr)
                    return

    def shutdown(self):
        self.running = False
        self.sock.close()


if __name__ == "__main__":
    GridServer().start()