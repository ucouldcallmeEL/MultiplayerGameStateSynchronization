import threading
import tkinter as tk
import tkinter.messagebox as messagebox
import time
import socket
import struct
import os
import sys
import csv

# === IMPORT SERVER FOR HOSTING ===
try:
    from server import GridServer
except ImportError:
    GridServer = None

from protocol import (
    build_event_message,
    build_init_message,
    parse_header,
    parse_join_response_payload,
    HEADER_SIZE,
    MSG_JOIN_RESPONSE,
    MSG_GAME_OVER,
    GRID_SIZE,
    MSG_GENERIC_ACK,
    build_ack_message,
    MSG_CELL_UPDATE,
    parse_ack_payload,
    parse_cell_update_payload,
    build_header,
    MSG_EVENT,
    MSG_HEARTBEAT
)

# ==============================================================
# === Configuration ===
# ==============================================================
CELL_SIZE = 60
DEFAULT_IP = "127.0.0.1"
DEFAULT_PORT = 9999

PLAYER_COLORS = {
    1: "#4CAF50", 2: "#F44336", 3: "#2196F3", 4: "#FF9800", 0: "#FFFFFF"
}
PLAYER_NAMES = {1: "Green", 2: "Red", 3: "Blue", 4: "Orange"}


class GridClient:
    def __init__(self, root):
        self.root = root
        self.root.title("Grid Clash")
        self.root.protocol("WM_DELETE_WINDOW", self.shutdown)

        # === Game State ===
        self.grid = [[0 for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]
        self.my_player_id = None
        self.last_heartbeat_time = time.time()
        self.is_game_over_processed = False  # <--- NEW FLAG

        # === Host State ===
        self.server_instance = None
        self.server_thread = None

        # === Networking ===
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('', 0))
        self.sock.setblocking(False)
        self.target_ip = DEFAULT_IP
        self.server_port = DEFAULT_PORT
        self.server_addr = (self.target_ip, self.server_port)

        # === Reliability ===
        self.seq_num = 0
        self.reliable_buffer = {}
        self.lock = threading.Lock()

        # === UI Setup ===
        self.menu_frame = tk.Frame(root)
        self.game_frame = tk.Frame(root)
        self.menu_frame.grid(row=0, column=0, sticky="nsew")
        self.game_frame.grid(row=0, column=0, sticky="nsew")

        self.setup_menu_ui()
        self.setup_game_ui()
        self.show_menu()

        # === Start Loops ===
        self.root.after(10, self.network_loop)
        threading.Thread(target=self.reliability_loop, daemon=True).start()

    # ==============================================================
    # === UI Construction ===
    # ==============================================================

    def setup_menu_ui(self):
        tk.Label(self.menu_frame, text="Grid Clash", font=("Arial", 28, "bold")).pack(pady=(40, 20))

        input_frame = tk.Frame(self.menu_frame)
        input_frame.pack(pady=10)

        tk.Label(input_frame, text="IP Address:").grid(row=0, column=0, sticky="e")
        self.ip_entry = tk.Entry(input_frame)
        self.ip_entry.insert(0, DEFAULT_IP)
        self.ip_entry.grid(row=0, column=1)

        tk.Label(input_frame, text="Port:").grid(row=1, column=0, sticky="e")
        self.port_entry = tk.Entry(input_frame)
        self.port_entry.insert(0, str(DEFAULT_PORT))
        self.port_entry.grid(row=1, column=1)

        btn_frame = tk.Frame(self.menu_frame)
        btn_frame.pack(pady=30)

        self.btn_join = tk.Button(btn_frame, text="Join Game", command=self.on_join, width=15)
        self.btn_join.pack(side=tk.LEFT, padx=10)

        if GridServer:
            self.btn_host = tk.Button(btn_frame, text="Host & Play", command=self.on_host, width=15)
            self.btn_host.pack(side=tk.LEFT, padx=10)

        self.lbl_status = tk.Label(self.menu_frame, text="", fg="gray")
        self.lbl_status.pack(pady=5)

    def setup_game_ui(self):
        self.canvas = tk.Canvas(self.game_frame, width=GRID_SIZE * CELL_SIZE, height=GRID_SIZE * CELL_SIZE, bg="white")
        self.canvas.grid(row=0, column=0, padx=10, pady=10)
        self.canvas.bind("<Button-1>", self.on_canvas_click)

        self.lbl_game_status = tk.Label(self.game_frame, text="Waiting...", font=("Arial", 12))
        self.lbl_game_status.grid(row=2, column=0)

        tk.Button(self.game_frame, text="Disconnect", command=self.on_disconnect).grid(row=3, column=0)

    def show_menu(self):
        self.my_player_id = None
        self.game_frame.grid_remove()
        self.menu_frame.grid()
        self.lbl_status.config(text="")
        self.btn_join.config(state="normal")
        if hasattr(self, 'btn_host'): self.btn_host.config(state="normal")
        # Reset flag
        self.is_game_over_processed = False

    def show_game(self):
        self.menu_frame.grid_remove()
        self.game_frame.grid()
        name = PLAYER_NAMES.get(self.my_player_id, 'Unknown')
        color = PLAYER_COLORS.get(self.my_player_id, 'black')
        self.lbl_game_status.config(text=f"You are {name} (Player {self.my_player_id})", fg=color)
        self.draw_grid()
        # Reset flag
        self.is_game_over_processed = False

    # ==============================================================
    # === Actions ===
    # ==============================================================

    def on_host(self):
        try:
            port = int(self.port_entry.get())
            self.server_instance = GridServer("0.0.0.0", port)
            self.server_thread = threading.Thread(target=self.server_instance.start, daemon=True)
            self.server_thread.start()
            time.sleep(0.5)
            self.ip_entry.delete(0, tk.END)
            self.ip_entry.insert(0, "127.0.0.1")
            self.on_join()
        except Exception as e:
            self.lbl_status.config(text=f"Error: {e}")

    def on_join(self):
        try:
            self.target_ip = self.ip_entry.get().strip()
            self.server_port = int(self.port_entry.get().strip())
            self.server_addr = (self.target_ip, self.server_port)

            self.seq_num = 0
            with self.lock:
                self.reliable_buffer.clear()
            self.grid = [[0] * GRID_SIZE for _ in range(GRID_SIZE)]
            self.is_game_over_processed = False

            self.send_message(build_init_message())
            self.lbl_status.config(text="Connecting...")
            self.btn_join.config(state="disabled")
        except ValueError:
            self.lbl_status.config(text="Invalid Port")

    def on_disconnect(self):
        if self.server_instance:
            self.server_instance.shutdown()
            self.server_instance = None
        self.show_menu()

    def on_canvas_click(self, event):
        if self.my_player_id is None: return
        r = event.y // CELL_SIZE
        c = event.x // CELL_SIZE
        if 0 <= r < GRID_SIZE and 0 <= c < GRID_SIZE:
            ts = int(time.time() * 1000)
            cell_id = r * GRID_SIZE + c

            self.seq_num += 1
            curr_seq = self.seq_num

            # Build Event Message (Critical)
            msg = build_event_message(self.my_player_id, cell_id, ts, curr_seq)
            self.send_reliable(msg, curr_seq)

    # ==============================================================
    # === Network & Reliability ===
    # ==============================================================

    def send_message(self, msg):
        self.sock.sendto(msg, self.server_addr)

    def send_reliable(self, packet, seq_num):
        self.send_message(packet)
        with self.lock:
            self.reliable_buffer[seq_num] = {
                'packet': packet,
                'last_sent': time.time(),
                'retries': 0
            }

    def reliability_loop(self):
        while True:
            time.sleep(0.05)
            now = time.time()
            with self.lock:
                for seq in list(self.reliable_buffer.keys()):
                    data = self.reliable_buffer[seq]
                    if now - data['last_sent'] > 0.3:
                        if data['retries'] < 10:
                            self.send_message(data['packet'])
                            data['last_sent'] = now
                            data['retries'] += 1
                        else:
                            del self.reliable_buffer[seq]

    def network_loop(self):
        try:
            while True:
                data, _ = self.sock.recvfrom(4096)
                if len(data) < HEADER_SIZE: break

                header = parse_header(data)
                payload = data[HEADER_SIZE:]
                msg_type = header["msg_type"]

                # 1. ACK Handling
                if msg_type == MSG_GENERIC_ACK:
                    acked_seq = parse_ack_payload(payload)
                    with self.lock:
                        if acked_seq in self.reliable_buffer:
                            del self.reliable_buffer[acked_seq]

                # 2. Critical Game Events (Cell Updates)
                elif msg_type == MSG_CELL_UPDATE:
                    seq_in = header['seq_num']
                    self.send_message(build_ack_message(seq_in))

                    r, c, owner = parse_cell_update_payload(payload)
                    self.grid[r][c] = owner
                    self.draw_cell(r, c, PLAYER_COLORS.get(owner, "white"))

                # 3. Heartbeats
                elif msg_type == MSG_HEARTBEAT:
                    self.last_heartbeat_time = time.time()

                # 4. Join / Game Over
                elif msg_type == MSG_JOIN_RESPONSE:
                    self.my_player_id, grid_owners = parse_join_response_payload(payload)
                    self.update_full_grid(grid_owners)
                    self.show_game()

                elif msg_type == MSG_GAME_OVER:
                    # A. Always ACK the server so it stops sending retries
                    self.send_message(build_ack_message(header['seq_num']))

                    # B. Only show the popup once
                    if not self.is_game_over_processed:
                        self.is_game_over_processed = True
                        winner_id = struct.unpack("!B", payload)[0]
                        messagebox.showinfo("Game Over!", f"Winner is Player {winner_id}!")
                        self.on_disconnect()

        except Exception:
            pass
        self.root.after(10, self.network_loop)

    # ==============================================================
    # === Drawing ===
    # ==============================================================

    def draw_grid(self):
        self.canvas.delete("all")
        for i in range(GRID_SIZE):
            for j in range(GRID_SIZE):
                x1, y1 = j * CELL_SIZE, i * CELL_SIZE
                self.canvas.create_rectangle(x1, y1, x1 + CELL_SIZE, y1 + CELL_SIZE, outline="gray")

        for r in range(GRID_SIZE):
            for c in range(GRID_SIZE):
                if self.grid[r][c] != 0:
                    self.draw_cell(r, c, PLAYER_COLORS[self.grid[r][c]])

    def draw_cell(self, row, col, color):
        x1, y1 = col * CELL_SIZE + 2, row * CELL_SIZE + 2
        x2, y2 = x1 + CELL_SIZE - 4, y1 + CELL_SIZE - 4
        tag = f"cell_{row}_{col}"

        if self.canvas.find_withtag(tag):
            self.canvas.itemconfig(tag, fill=color)
        else:
            self.canvas.create_rectangle(x1, y1, x2, y2, fill=color, outline="", tags=tag)

    def update_full_grid(self, grid_owners):
        for cell_id, owner in enumerate(grid_owners):
            r, c = cell_id // GRID_SIZE, cell_id % GRID_SIZE
            self.grid[r][c] = owner
        self.draw_grid()

    def shutdown(self):
        if self.server_instance: self.server_instance.shutdown()
        self.root.destroy()
        sys.exit(0)


if __name__ == "__main__":
    root = tk.Tk()
    app = GridClient(root)
    root.mainloop()