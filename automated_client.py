#!/usr/bin/env python3
"""
Automated Headless Client for Testing
--------------------------------------
This client automatically joins and plays the game by sending click events.
It's designed for automated testing scenarios.
"""

import socket
import struct
import time
import os
import sys
import csv
import random
import threading

from protocol import (
    build_event_message,
    build_init_message,
    build_snapshot_ack_message,
    parse_event_ack_payload,
    parse_header,
    parse_join_response_payload,
    parse_snapshot_payload,
    HEADER_SIZE,
    MSG_SNAPSHOT,
    MSG_JOIN_RESPONSE,
    MSG_GAME_OVER,
    MSG_EVENT_ACK,
    GRID_SIZE,
    TOTAL_CELLS
)

# ==============================================================
# === Configuration ===
# ==============================================================
DEFAULT_IP = "127.0.0.1"
DEFAULT_PORT = 9999
CLICK_INTERVAL = 0.5  # Send a click every 0.5 seconds
TEST_DURATION = 60  # Run for 60 seconds

class AutomatedClient:
    def __init__(self, server_ip=DEFAULT_IP, server_port=DEFAULT_PORT, client_id=None):
        self.server_addr = (server_ip, server_port)
        self.client_id = client_id or os.getpid()
        
        # === Game State ===
        self.grid = [[0 for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]
        self.my_player_id = None
        self.latest_snapshot_id = 0
        self.running = True
        self.event_seq = 0
        self.pending_events = {}
        self.retry_interval = 0.2  # seconds
        self.max_event_retries = 10
        
        # === Networking ===
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('', 0))
        self.sock.setblocking(False)
        
        # === Metrics & Logging ===
        self.csv_file = open(f"client_{self.client_id}_metrics.csv", 'w', newline='')
        self.csv_writer = csv.writer(self.csv_file)
        self.csv_writer.writerow([
            'snapshot_id', 'server_timestamp_ms', 'recv_time_ms', 'latency_ms',
            'position_error', 'cell_owner', 'expected_owner'
        ])
        
        # For position error tracking (loss scenarios)
        self.last_known_grid = [[0 for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]
        self.position_errors = []
        
        # === Threading ===
        self.network_thread = None
        self.gameplay_thread = None
        self.retry_thread = None
        
    def start(self, duration=TEST_DURATION):
        """Start the automated client."""
        print(f"[CLIENT {self.client_id}] Starting automated client...", flush=True)
        
        # Join the game
        self.send_message(build_init_message())
        time.sleep(1)  # Wait for join response
        
        # Start network receiving thread
        self.network_thread = threading.Thread(target=self.network_loop, daemon=True)
        self.network_thread.start()
        
        # Start retry thread
        self.retry_thread = threading.Thread(target=self.retry_loop, daemon=True)
        self.retry_thread.start()

        # Start gameplay thread (sends clicks)
        self.gameplay_thread = threading.Thread(target=self.gameplay_loop, daemon=True)
        self.gameplay_thread.start()
        
        # Wait for test duration
        print(f"[CLIENT {self.client_id}] Running for {duration} seconds...", flush=True)
        time.sleep(duration)
        
        # Stop
        print(f"[CLIENT {self.client_id}] Test duration complete, shutting down...", flush=True)
        self.running = False
        time.sleep(0.5)
        self.cleanup()
        
    def send_message(self, msg):
        """Send a message to the server."""
        try:
            self.sock.sendto(msg, self.server_addr)
        except Exception as e:
            print(f"[CLIENT {self.client_id}] Send failed: {e}", flush=True)

    def enqueue_event(self, event_id, msg):
        now_ms = int(time.time() * 1000)
        self.pending_events[event_id] = {
            "msg": msg,
            "last_sent_ms": now_ms,
            "retries": 0
        }
        self.send_message(msg)

    def retry_pending_events(self):
        now_ms = int(time.time() * 1000)
        to_delete = []
        for event_id, info in list(self.pending_events.items()):
            if now_ms - info["last_sent_ms"] >= int(self.retry_interval * 1000):
                if info["retries"] >= self.max_event_retries:
                    print(f"[CLIENT {self.client_id}] Dropping event {event_id} after retries", flush=True)
                    to_delete.append(event_id)
                    continue
                self.send_message(info["msg"])
                info["retries"] += 1
                info["last_sent_ms"] = now_ms

        for event_id in to_delete:
            self.pending_events.pop(event_id, None)

    def retry_loop(self):
        while self.running:
            self.retry_pending_events()
            time.sleep(self.retry_interval)
    
    def network_loop(self):
        """Continuously receive and process messages from server."""
        while self.running:
            try:
                while True:
                    data, _ = self.sock.recvfrom(4096)
                    if len(data) < HEADER_SIZE:
                        break
                    
                    header = parse_header(data)
                    payload = data[HEADER_SIZE:]
                    msg_type = header["msg_type"]
                    
                    if msg_type == MSG_JOIN_RESPONSE:
                        self.handle_join(payload)
                    elif msg_type == MSG_SNAPSHOT:
                        self.handle_snapshot(header, payload)
                    elif msg_type == MSG_GAME_OVER:
                        self.handle_game_over(payload)
                    elif msg_type == MSG_EVENT_ACK:
                        self.handle_event_ack(payload)
                        
            except (BlockingIOError, ConnectionResetError, OSError):
                time.sleep(0.01)
            except Exception as e:
                print(f"[CLIENT {self.client_id}] Network error: {e}", flush=True)
                time.sleep(0.01)
    
    def handle_join(self, payload):
        """Handle join response from server."""
        try:
            self.my_player_id, grid_owners = parse_join_response_payload(payload)
            print(f"[CLIENT {self.client_id}] Joined as Player {self.my_player_id}", flush=True)
            self.update_grid(grid_owners)
            self.last_known_grid = [row[:] for row in self.grid]
        except Exception as e:
            print(f"[CLIENT {self.client_id}] Join parse failed: {e}", flush=True)
    
    def handle_snapshot(self, header, payload):
        """Handle snapshot from server."""
        try:
            _, grid_owners = parse_snapshot_payload(payload)
            snapshot_id = header['snapshot_id']
            
            # Discard outdated snapshots
            if snapshot_id <= self.latest_snapshot_id:
                return
            
            server_ts = header['timestamp']
            recv_ts = int(time.time() * 1000)
            latency = recv_ts - server_ts
            
            # Calculate position error (difference between expected and actual)
            position_error = self.calculate_position_error(grid_owners)
            
            # Update grid
            self.update_grid(grid_owners)
            
            # Log metrics
            self.csv_writer.writerow([
                snapshot_id, server_ts, recv_ts, latency,
                position_error, '', ''
            ])
            self.csv_file.flush()
            
            # Send ACK
            self.send_message(build_snapshot_ack_message(snapshot_id, server_ts, recv_ts))
            
            self.latest_snapshot_id = snapshot_id
            
        except Exception as e:
            print(f"[CLIENT {self.client_id}] Snapshot parse failed: {e}", flush=True)
    
    def calculate_position_error(self, grid_owners):
        """
        Calculate position error - measures interpolation quality.
        For loss scenarios, this tracks how many cells differ from expected state.
        """
        if self.my_player_id is None:
            return 0.0
        
        error_sum = 0.0
        total_cells = 0
        
        # Compare current received state with last known state
        # Position error is the number of cells that changed unexpectedly
        for cell_id, owner in enumerate(grid_owners):
            r, c = cell_id // GRID_SIZE, cell_id % GRID_SIZE
            expected = self.last_known_grid[r][c]
            actual = owner
            
            # If a cell changed and it wasn't changed by this player, it's an error
            # (indicates we missed an update or interpolation wasn't perfect)
            if expected != actual:
                # This is a change we might have missed
                error_sum += 1.0
            total_cells += 1
        
        # Update last known grid for next comparison
        for cell_id, owner in enumerate(grid_owners):
            r, c = cell_id // GRID_SIZE, cell_id % GRID_SIZE
            self.last_known_grid[r][c] = owner
        
        # Return normalized error (0-1 scale, then multiply by grid size for units)
        return error_sum  # Total number of unexpected changes
    
    def update_grid(self, grid_owners):
        """Update internal grid state."""
        for cell_id, owner in enumerate(grid_owners):
            r, c = cell_id // GRID_SIZE, cell_id % GRID_SIZE
            self.grid[r][c] = owner
    
    def handle_game_over(self, payload):
        """Handle game over message."""
        winner_id = struct.unpack("!B", payload)[0]
        print(f"[CLIENT {self.client_id}] Game Over! Winner: Player {winner_id}", flush=True)
        
        # Clear all pending events since game is over
        self.pending_events.clear()
        self.event_seq = 0
        
        # Reset grid
        self.grid = [[0 for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]
        self.last_known_grid = [[0 for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]
        
        # Reset player ID to allow rejoining
        self.my_player_id = None
        
        # Rejoin the game automatically after a short delay
        time.sleep(0.5)
        if self.running:
            print(f"[CLIENT {self.client_id}] Rejoining game...", flush=True)
            self.send_message(build_init_message())

    def handle_event_ack(self, payload):
        try:
            ack = parse_event_ack_payload(payload)
            event_id = ack["event_id"]
            status = ack.get("status", 0)
            self.pending_events.pop(event_id, None)
            if status != 0:
                print(f"[CLIENT {self.client_id}] Event {event_id} acked with status {status}", flush=True)
        except Exception as e:
            print(f"[CLIENT {self.client_id}] Event ACK parse failed: {e}", flush=True)
    
    def gameplay_loop(self):
        """Automatically send click events to play the game."""
        print(f"[CLIENT {self.client_id}] Starting gameplay loop...", flush=True)
        
        while self.running:
            # Wait until we've joined (handles rejoin after game over)
            while self.my_player_id is None and self.running:
                time.sleep(0.1)
            
            if not self.running:
                break
            
            # Pick a random empty cell or cell owned by another player
            available_cells = []
            for r in range(GRID_SIZE):
                for c in range(GRID_SIZE):
                    if self.grid[r][c] != self.my_player_id:
                        available_cells.append(r * GRID_SIZE + c)
            
            if available_cells:
                # Pick a random cell
                cell_id = random.choice(available_cells)
                ts = int(time.time() * 1000)
                event_id = self.event_seq
                self.event_seq += 1
                msg = build_event_message(self.my_player_id, event_id, cell_id, ts)
                self.enqueue_event(event_id, msg)
            
            time.sleep(CLICK_INTERVAL)
    
    def cleanup(self):
        """Clean up resources."""
        try:
            self.csv_file.close()
        except:
            pass
        try:
            self.sock.close()
        except:
            pass
        print(f"[CLIENT {self.client_id}] Shutting down.", flush=True)


if __name__ == "__main__":
    # Parse command line arguments
    server_ip = DEFAULT_IP
    server_port = DEFAULT_PORT
    client_id = None
    duration = TEST_DURATION
    
    # Get duration from environment variable if set
    if os.getenv('TEST_DURATION'):
        try:
            duration = int(os.getenv('TEST_DURATION'))
        except:
            pass
    
    if len(sys.argv) > 1:
        if '--server' in sys.argv:
            idx = sys.argv.index('--server')
            if idx + 1 < len(sys.argv):
                addr = sys.argv[idx + 1]
                if ':' in addr:
                    server_ip, server_port = addr.split(':')
                    server_port = int(server_port)
                else:
                    server_ip = addr
    
    if len(sys.argv) > 1:
        if '--id' in sys.argv:
            idx = sys.argv.index('--id')
            if idx + 1 < len(sys.argv):
                client_id = int(sys.argv[idx + 1])
    
    client = AutomatedClient(server_ip, server_port, client_id)
    client.start(duration=duration)
