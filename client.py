import tkinter as tk
import tkinter.messagebox as messagebox
import time
import socket
import struct
import os
import sys
import csv

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
    GRID_SIZE
)

# ==============================================================
# === Configuration ===
# ==============================================================
CELL_SIZE = 60
DEFAULT_IP = "127.0.0.1"
DEFAULT_PORT = 9999

PLAYER_COLORS = {
    1: "#4CAF50",  # Green
    2: "#F44336",  # Red
    3: "#2196F3",  # Blue
    4: "#FF9800",  # Orange
    0: "#FFFFFF",  # Empty
}

PLAYER_NAMES = {
    1: "Green", 2: "Red", 3: "Blue", 4: "Orange"
}


# ==============================================================
# === Smoothing Helpers (Linear Interpolation) ===
# ==============================================================

def hex_to_rgb(hex_col):
    """Converts '#RRGGBB' to (r, g, b) integers."""
    hex_col = hex_col.lstrip('#')
    return tuple(int(hex_col[i:i + 2], 16) for i in (0, 2, 4))


def rgb_to_hex(rgb):
    """Converts (r, g, b) to '#RRGGBB'."""
    return '#%02x%02x%02x' % tuple(int(c) for c in rgb)


def lerp_color(color1, color2, t):
    """
    Linearly interpolates between color1 and color2 by factor t (0.0 to 1.0).
    """
    c1 = hex_to_rgb(color1)
    c2 = hex_to_rgb(color2)
    new_rgb = (
        int(c1[0] + (c2[0] - c1[0]) * t),
        int(c1[1] + (c2[1] - c1[1]) * t),
        int(c1[2] + (c2[2] - c1[2]) * t)
    )
    return rgb_to_hex(new_rgb)


class GridClient:
    def __init__(self, root):
        self.root = root
        self.root.title("Grid Clash")
        self.root.protocol("WM_DELETE_WINDOW", self.shutdown)

        # === Game State ===
        # 1. Logical Grid: Authoritative state from server (Integers)
        self.grid = [[0 for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]

        # 2. Visual Grid: Current displayed colors (Hex Strings)
        self.visual_grid = [["#FFFFFF" for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]

        self.my_player_id = None
        self.latest_snapshot_id = 0

        # === Networking ===
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('', 0))  # Bind to any available local port
        self.sock.setblocking(False)
        self.target_ip = DEFAULT_IP
        self.server_port = DEFAULT_PORT
        self.server_addr = (self.target_ip, self.server_port)

        # === Logging ===
        self.csv_file = open(f"client_{os.getpid()}_metrics.csv", 'w', newline='')
        self.csv_writer = csv.writer(self.csv_file)
        self.csv_writer.writerow(['snapshot_id', 'server_timestamp_ms', 'recv_time_ms', 'latency_ms'])

        # === Event Tracking ===
        self.event_seq = 0
        self.pending_events = {}
        self.retry_interval_ms = 150
        self.max_event_retries = 10

        # === UI Setup ===
        self.menu_frame = tk.Frame(root)
        self.game_frame = tk.Frame(root)

        self.menu_frame.grid(row=0, column=0, sticky="nsew")
        self.game_frame.grid(row=0, column=0, sticky="nsew")

        self.setup_menu_ui()
        self.setup_game_ui()
        self.show_menu()

        # === Start Loops ===
        # 1. Network Loop: Polls for packets (Logic)
        self.root.after(10, self.network_loop)

        # 2. Render Loop: Handles smoothing/interpolation (Visuals)
        self.root.after(16, self.render_loop)

        # 3. Event retry loop
        self.root.after(self.retry_interval_ms, self.event_retry_loop)

        # Auto-join support
        if '--auto' in sys.argv or os.getenv('AUTO_JOIN', '').lower() in ('1', 'true', 'yes'):
            self.on_join()

    # ==============================================================
    # === UI Construction ===
    # ==============================================================

    def setup_menu_ui(self):
        tk.Label(self.menu_frame, text="Grid Clash", font=("Arial", 28, "bold")).pack(pady=(40, 10))
        ip_frame = tk.Frame(self.menu_frame)
        ip_frame.pack(pady=10)
        tk.Label(ip_frame, text="Server Address:", font=("Arial", 12)).pack(side=tk.LEFT, padx=5)
        self.ip_entry = tk.Entry(ip_frame, font=("Arial", 12), width=20)
        self.ip_entry.insert(0, f"{DEFAULT_IP}:{DEFAULT_PORT}")
        self.ip_entry.pack(side=tk.LEFT)
        self.btn_join = tk.Button(
            self.menu_frame, text="Join Game", width=20, height=2,
            font=("Arial", 12), bg="#e1f5fe", command=self.on_join
        )
        self.btn_join.pack(pady=20)
        self.lbl_status = tk.Label(self.menu_frame, text="", font=("Arial", 11), fg="gray")
        self.lbl_status.pack(pady=5)

    def setup_game_ui(self):
        self.canvas = tk.Canvas(
            self.game_frame, width=GRID_SIZE * CELL_SIZE, height=GRID_SIZE * CELL_SIZE, bg="white"
        )
        self.canvas.grid(row=0, column=0, padx=10, pady=10)
        self.canvas.bind("<Button-1>", self.on_canvas_click)
        self.lbl_game_status = tk.Label(self.game_frame, text="Waiting...", font=("Arial", 12))
        self.lbl_game_status.grid(row=2, column=0, pady=5)
        tk.Button(self.game_frame, text="Disconnect", command=self.on_disconnect).grid(row=3, column=0, pady=10)
        self.draw_grid_lines()

    def show_menu(self):
        self.my_player_id = None
        self.root.title("Grid Clash")
        self.game_frame.grid_remove()
        self.menu_frame.grid()
        self.lbl_status.config(text="")
        self.btn_join.config(state="normal")
        self.grid = [[0] * GRID_SIZE for _ in range(GRID_SIZE)]

    def show_game(self):
        self.root.title(f"Grid Clash — Player {self.my_player_id}")
        self.menu_frame.grid_remove()
        self.game_frame.grid()
        name = PLAYER_NAMES.get(self.my_player_id, 'Unknown')
        color = PLAYER_COLORS.get(self.my_player_id, 'black')
        self.lbl_game_status.config(text=f"You are {name} (Player {self.my_player_id})", fg=color)
        # No redraw_full_grid needed here, render_loop will handle it

    # ==============================================================
    # === User Actions ===
    # ==============================================================

    def on_join(self):
        ip_txt = self.ip_entry.get().strip()
        if ":" in ip_txt:
            ip_part, port_part = ip_txt.split(":")
            self.target_ip = ip_part
            self.server_port = int(port_part)
        else:
            self.target_ip = ip_txt
            self.server_port = DEFAULT_PORT
        self.server_addr = (self.target_ip, self.server_port)
        self.lbl_status.config(text=f"Connecting to {self.target_ip}:{self.server_port}...")
        self.btn_join.config(state="disabled")
        self.send_message(build_init_message())

    def on_disconnect(self):
        self.show_menu()

    def on_canvas_click(self, event):
        if self.my_player_id is None: return
        r = event.y // CELL_SIZE
        c = event.x // CELL_SIZE
        if 0 <= r < GRID_SIZE and 0 <= c < GRID_SIZE:
            cell_id = r * GRID_SIZE + c
            ts = int(time.time() * 1000)
            event_id = self.event_seq
            self.event_seq += 1
            msg = build_event_message(self.my_player_id, event_id, cell_id, ts)
            self.enqueue_event(event_id, msg)

    # ==============================================================
    # === Drawing Logic ===
    # ==============================================================

    def draw_grid_lines(self):
        for i in range(GRID_SIZE):
            for j in range(GRID_SIZE):
                x1, y1 = j * CELL_SIZE, i * CELL_SIZE
                self.canvas.create_rectangle(x1, y1, x1 + CELL_SIZE, y1 + CELL_SIZE, outline="gray")

    def draw_cell(self, row, col, color):
        x1, y1 = col * CELL_SIZE + 2, row * CELL_SIZE + 2
        x2, y2 = x1 + CELL_SIZE - 4, y1 + CELL_SIZE - 4
        tag = f"cell_{row}_{col}"
        # Update existing tag if possible to be faster
        if self.canvas.find_withtag(tag):
            self.canvas.itemconfig(tag, fill=color)
        else:
            self.canvas.create_rectangle(x1, y1, x2, y2, fill=color, outline="", tags=tag)

    def clear_all_cells(self):
        """Clear all cell drawings from the canvas."""
        for r in range(GRID_SIZE):
            for c in range(GRID_SIZE):
                tag = f"cell_{r}_{c}"
                self.canvas.delete(tag)

    # ==============================================================
    # === NEW: Render Loop (Visual Smoothing) ===
    # ==============================================================

    def render_loop(self):
        """
        Independent loop running at ~60FPS.
        Interpolates current visual color towards the logical target color.
        """
        if self.my_player_id is not None:
            self.smooth_and_draw()

        # Run again in ~16ms
        self.root.after(16, self.render_loop)

    def smooth_and_draw(self):
        # 0.2 means we move 20% closer to the target color every frame
        LERP_FACTOR = 0.2

        for r in range(GRID_SIZE):
            for c in range(GRID_SIZE):
                # 1. Get Target (Logical) Color
                owner_id = self.grid[r][c]
                target_hex = PLAYER_COLORS.get(owner_id, "#FFFFFF")

                # 2. Get Current (Visual) Color
                current_hex = self.visual_grid[r][c]

                # 3. Interpolate if different
                if current_hex != target_hex:
                    new_hex = lerp_color(current_hex, target_hex, LERP_FACTOR)

                    # Optimization: Snap to target if very close to avoid infinite calculation
                    if new_hex == current_hex:
                        new_hex = target_hex

                    self.visual_grid[r][c] = new_hex
                    self.draw_cell(r, c, new_hex)

    # ==============================================================
    # === Network Loop & Handlers ===
    # ==============================================================

    def send_message(self, msg):
        try:
            self.sock.sendto(msg, self.server_addr)
        except Exception as e:
            print(f"[ERROR] Send failed: {e}")

    def enqueue_event(self, event_id, msg):
        now_ms = int(time.time() * 1000)
        self.pending_events[event_id] = {
            "msg": msg,
            "last_sent_ms": now_ms,
            "retries": 0
        }
        self.send_message(msg)

    def event_retry_loop(self):
        now_ms = int(time.time() * 1000)
        to_delete = []
        for event_id, info in list(self.pending_events.items()):
            if now_ms - info["last_sent_ms"] >= self.retry_interval_ms:
                if info["retries"] >= self.max_event_retries:
                    print(f"[WARN] Dropping event {event_id} after retries")
                    to_delete.append(event_id)
                    continue

                self.send_message(info["msg"])
                info["retries"] += 1
                info["last_sent_ms"] = now_ms

        for event_id in to_delete:
            self.pending_events.pop(event_id, None)

        self.root.after(self.retry_interval_ms, self.event_retry_loop)

    def network_loop(self):
        try:
            while True:
                data, _ = self.sock.recvfrom(4096)
                if len(data) < HEADER_SIZE: break

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
            # Silently ignore common socket errors (including WinError 10022)
            pass
        except Exception as e:
            print(f"[NETWORK] Loop error: {e}")

        # Poll network frequently (10ms)
        self.root.after(10, self.network_loop)

    def handle_join(self, payload):
        try:
            self.my_player_id, grid_owners = parse_join_response_payload(payload)
            print(f"[NETWORK] Joined as Player {self.my_player_id}")
            self.update_logical_grid(grid_owners)
            self.show_game()
        except Exception as e:
            print(f"[ERROR] Join parse failed: {e}")
            self.show_menu()
            self.lbl_status.config(text="Join failed.")

    def handle_snapshot(self, header, payload):
        try:
            _, grid_owners = parse_snapshot_payload(payload)

            snapshot_id = header['snapshot_id']

            # Discard outdated snapshots
            if snapshot_id <= self.latest_snapshot_id:
                return

            server_ts = header['timestamp']
            recv_ts = int(time.time() * 1000)
            latency = recv_ts - server_ts

            self.csv_writer.writerow([snapshot_id, server_ts, recv_ts, latency])
            self.send_message(build_snapshot_ack_message(snapshot_id, server_ts, recv_ts))

            self.latest_snapshot_id = snapshot_id

            # Update LOGICAL state only (rendering is handled in render_loop)
            self.update_logical_grid(grid_owners)

        except Exception as e:
            print(f"[ERROR] Snapshot parse failed: {e}")

    def update_logical_grid(self, grid_owners):
        """Updates internal data model ONLY. No drawing."""
        for cell_id, owner in enumerate(grid_owners):
            r, c = cell_id // GRID_SIZE, cell_id % GRID_SIZE
            self.grid[r][c] = owner

    def handle_game_over(self, payload):
        winner_id = struct.unpack("!B", payload)[0]
        winner_name = PLAYER_NAMES.get(winner_id, f"Player {winner_id}")
        messagebox.showinfo("Game Over!", f"Winner is {winner_name}!")

        # Reset Logic
        self.grid = [[0] * GRID_SIZE for _ in range(GRID_SIZE)]
        self.visual_grid = [["#FFFFFF"] * GRID_SIZE for _ in range(GRID_SIZE)]
        self.clear_all_cells()  # Clear canvas visually
        self.show_menu()

    def handle_event_ack(self, payload):
        try:
            ack = parse_event_ack_payload(payload)
            event_id = ack["event_id"]
            status = ack.get("status", 0)
            self.pending_events.pop(event_id, None)
            if status != 0:
                print(f"[WARN] Event {event_id} acked with status {status}")
        except Exception as e:
            print(f"[ERROR] Event ACK parse failed: {e}")

    # ==============================================================
    # === System & Cleanup ===
    # ==============================================================

    def shutdown(self):
        try:
            self.csv_file.close()
        except:
            pass
        self.root.destroy()
        sys.exit(0)


if __name__ == "__main__":
    root = tk.Tk()
    app = GridClient(root)
    root.mainloop()