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
    parse_header,
    parse_join_response_payload,
    parse_snapshot_payload,
    HEADER_SIZE,
    MSG_SNAPSHOT,
    MSG_JOIN_RESPONSE,
    MSG_GAME_OVER,
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


class GridClient:
    def __init__(self, root):
        self.root = root
        self.root.title("Grid Clash")
        self.root.protocol("WM_DELETE_WINDOW", self.shutdown)

        # === Game State ===
        self.grid = [[0 for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]
        self.my_player_id = None
        self.latest_snapshot_id = 0

        # === Networking ===
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setblocking(False)
        self.target_ip = DEFAULT_IP
        self.server_port = DEFAULT_PORT
        self.server_addr = (self.target_ip, self.server_port)

        # === Logging (Preserved) ===
        self.csv_file = open(f"client_{os.getpid()}_metrics.csv", 'w', newline='')
        self.csv_writer = csv.writer(self.csv_file)
        self.csv_writer.writerow(['snapshot_id', 'server_timestamp_ms', 'recv_time_ms', 'latency_ms'])

        # === UI Setup ===
        self.menu_frame = tk.Frame(root)
        self.game_frame = tk.Frame(root)

        self.menu_frame.grid(row=0, column=0, sticky="nsew")
        self.game_frame.grid(row=0, column=0, sticky="nsew")

        self.setup_menu_ui()
        self.setup_game_ui()
        self.show_menu()

        # === Start Network Loop ===
        self.root.after(15, self.network_loop)

        # Auto-join support (Optional CLI arg)
        if '--auto' in sys.argv or os.getenv('AUTO_JOIN', '').lower() in ('1', 'true', 'yes'):
            self.on_join()

    # ==============================================================
    # === UI Construction ===
    # ==============================================================

    def setup_menu_ui(self):
        # Title
        tk.Label(self.menu_frame, text="Grid Clash", font=("Arial", 28, "bold")).pack(pady=(40, 10))

        # IP Entry Frame
        ip_frame = tk.Frame(self.menu_frame)
        ip_frame.pack(pady=10)

        tk.Label(ip_frame, text="Server Address:", font=("Arial", 12)).pack(side=tk.LEFT, padx=5)

        self.ip_entry = tk.Entry(ip_frame, font=("Arial", 12), width=20)
        self.ip_entry.insert(0, f"{DEFAULT_IP}:{DEFAULT_PORT}")
        self.ip_entry.pack(side=tk.LEFT)

        # Join Button
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
        self.redraw_full_grid()

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

        # Send Init Packet
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
            msg = build_event_message(self.my_player_id, cell_id, ts)
            self.send_message(msg)

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
        self.canvas.delete(tag)
        self.canvas.create_rectangle(x1, y1, x2, y2, fill=color, outline="", tags=tag)

    def redraw_full_grid(self):
        for r in range(GRID_SIZE):
            for c in range(GRID_SIZE):
                owner = self.grid[r][c]
                color = PLAYER_COLORS.get(owner, "#000000")
                self.draw_cell(r, c, color)

    # ==============================================================
    # === Network Loop & Handlers ===
    # ==============================================================

    def send_message(self, msg):
        try:
            self.sock.sendto(msg, self.server_addr)
        except Exception as e:
            print(f"[ERROR] Send failed: {e}")

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

        except (BlockingIOError, ConnectionResetError):
            pass
        except Exception as e:
            print(f"[NETWORK] Loop error: {e}")

        self.root.after(15, self.network_loop)

    def handle_join(self, payload):
        try:
            self.my_player_id, grid_owners = parse_join_response_payload(payload)
            print(f"[NETWORK] Joined as Player {self.my_player_id}")
            self.update_grid_state(grid_owners)
            self.show_game()
        except Exception as e:
            print(f"[ERROR] Join parse failed: {e}")
            self.show_menu()
            self.lbl_status.config(text="Join failed.")

    def handle_snapshot(self, header, payload):
        try:
            _, grid_owners = parse_snapshot_payload(payload)

            snapshot_id = header['snapshot_id']

            ##Discard outdated snapshots###
            if snapshot_id <= self.latest_snapshot_id:
                # This packet is older than or equal to the state we already have.
                # Discard it to prevent the game state from reversing (jitter).
                return
            #===============================#

            server_ts = header['timestamp']
            recv_ts = int(time.time() * 1000)
            latency = recv_ts - server_ts

            self.csv_writer.writerow([snapshot_id, server_ts, recv_ts, latency])

            # Send Ack
            self.send_message(build_snapshot_ack_message(snapshot_id, server_ts, recv_ts))

            # Update Grid
            self.latest_snapshot_id = snapshot_id
            self.update_grid_state(grid_owners)

        except Exception as e:
            print(f"[ERROR] Snapshot parse failed: {e}")

    def update_grid_state(self, grid_owners):
        """Diffs the new state against local state and only redraws changes."""
        for cell_id, owner in enumerate(grid_owners):
            r, c = cell_id // GRID_SIZE, cell_id % GRID_SIZE
            if self.grid[r][c] != owner:
                self.grid[r][c] = owner
                color = PLAYER_COLORS.get(owner, "black")
                self.draw_cell(r, c, color)

    def handle_game_over(self, payload):
        winner_id = struct.unpack("!B", payload)[0]
        winner_name = PLAYER_NAMES.get(winner_id, f"Player {winner_id}")

        messagebox.showinfo("Game Over!", f"Winner is {winner_name}!")

        self.grid = [[0] * GRID_SIZE for _ in range(GRID_SIZE)]
        self.redraw_full_grid()
        self.show_menu()

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