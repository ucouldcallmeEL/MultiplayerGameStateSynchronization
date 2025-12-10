import threading
import tkinter as tk
import tkinter.messagebox as messagebox
import time
import socket
import struct
import os
import sys
import csv

# === IMPORT THE SERVER ===
try:
    from server import GridServer
except ImportError:
    print("[WARNING] Could not import GridServer. Hosting will be disabled.")
    GridServer = None

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
    GRID_SIZE,
    MSG_GENERIC_ACK,
    build_ack_message,
    MSG_CELL_UPDATE,
    parse_ack_payload,
    parse_cell_update_payload,
    build_header,
    MSG_EVENT
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
# === Smoothing Helpers ===
# ==============================================================

def hex_to_rgb(hex_col):
    hex_col = hex_col.lstrip('#')
    return tuple(int(hex_col[i:i + 2], 16) for i in (0, 2, 4))


def rgb_to_hex(rgb):
    return '#%02x%02x%02x' % tuple(int(c) for c in rgb)


def lerp_color(color1, color2, t):
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
        self.grid = [[0 for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]
        self.visual_grid = [["#FFFFFF" for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]
        self.my_player_id = None
        self.latest_snapshot_id = 0

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

        # === Logging ===
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

        # === Start Loops ===
        self.root.after(10, self.network_loop)
        self.root.after(16, self.render_loop)
        threading.Thread(target=self.reliability_loop, daemon=True).start()

        if '--auto' in sys.argv:
            self.on_join()

    # ==============================================================
    # === UI Construction ===
    # ==============================================================

    def setup_menu_ui(self):
        tk.Label(self.menu_frame, text="Grid Clash", font=("Arial", 28, "bold")).pack(pady=(40, 20))

        # === INPUT CONTAINER ===
        input_frame = tk.Frame(self.menu_frame)
        input_frame.pack(pady=10)

        # IP Address
        tk.Label(input_frame, text="IP Address:", font=("Arial", 12)).grid(row=0, column=0, padx=5, sticky="e")
        self.ip_entry = tk.Entry(input_frame, font=("Arial", 12), width=15)
        self.ip_entry.insert(0, DEFAULT_IP)
        self.ip_entry.grid(row=0, column=1, padx=5, pady=5)

        # Port
        tk.Label(input_frame, text="Port:", font=("Arial", 12)).grid(row=1, column=0, padx=5, sticky="e")
        self.port_entry = tk.Entry(input_frame, font=("Arial", 12), width=15)
        self.port_entry.insert(0, str(DEFAULT_PORT))
        self.port_entry.grid(row=1, column=1, padx=5, pady=5)

        # Buttons
        btn_frame = tk.Frame(self.menu_frame)
        btn_frame.pack(pady=30)

        self.btn_join = tk.Button(
            btn_frame, text="Join Game", width=15, height=2,
            font=("Arial", 12), bg="#e1f5fe", command=self.on_join
        )
        self.btn_join.pack(side=tk.LEFT, padx=10)

        if GridServer:
            self.btn_host = tk.Button(
                btn_frame, text="Host & Play", width=15, height=2,
                font=("Arial", 12), bg="#e8f5e9", command=self.on_host
            )
            self.btn_host.pack(side=tk.LEFT, padx=10)

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

        tk.Button(self.game_frame, text="Disconnect / Stop", command=self.on_disconnect).grid(row=3, column=0, pady=10)
        self.draw_grid_lines()

    def show_menu(self):
        self.my_player_id = None
        self.root.title("Grid Clash")
        self.game_frame.grid_remove()
        self.menu_frame.grid()
        self.lbl_status.config(text="")
        self.btn_join.config(state="normal")
        if hasattr(self, 'btn_host'):
            self.btn_host.config(state="normal")

        # Reset Logic State for Menu
        self.grid = [[0] * GRID_SIZE for _ in range(GRID_SIZE)]
        self.visual_grid = [["#FFFFFF"] * GRID_SIZE for _ in range(GRID_SIZE)]
        self.clear_all_cells()

        self.ip_entry.config(state="normal")
        self.port_entry.config(state="normal")

    def show_game(self):
        self.root.title(f"Grid Clash — Player {self.my_player_id}")
        self.menu_frame.grid_remove()
        self.game_frame.grid()
        name = PLAYER_NAMES.get(self.my_player_id, 'Unknown')
        color = PLAYER_COLORS.get(self.my_player_id, 'black')
        self.lbl_game_status.config(text=f"You are {name} (Player {self.my_player_id})", fg=color)

    # ==============================================================
    # === User Actions ===
    # ==============================================================

    def on_host(self):
        """Starts the server locally using the specified PORT."""
        if self.server_instance:
            self.lbl_status.config(text="Server already running.")
            return

        try:
            # Get port from UI
            port_txt = self.port_entry.get().strip()
            local_port = int(port_txt) if port_txt else DEFAULT_PORT

            self.lbl_status.config(text=f"Starting server on port {local_port}...")
            self.root.update()

            # Initialize Server
            self.server_instance = GridServer(ip="0.0.0.0", port=local_port)

            # Run Server in a daemon thread
            self.server_thread = threading.Thread(target=self.server_instance.start, daemon=True)
            self.server_thread.start()

            # Give it a moment to bind
            time.sleep(0.5)

            # Auto-fill IP as localhost and join
            self.ip_entry.delete(0, tk.END)
            self.ip_entry.insert(0, "127.0.0.1")
            # Trigger Join
            self.on_join()

        except OSError as e:
            self.lbl_status.config(text=f"Failed: Port {port_txt} in use.")
            print(f"[ERROR] Hosting failed: {e}")
            self.server_instance = None
        except ValueError:
            self.lbl_status.config(text="Invalid Port Number")

    def on_join(self):
        try:
            target_ip = self.ip_entry.get().strip()
            target_port = int(self.port_entry.get().strip())
        except ValueError:
            self.lbl_status.config(text="Invalid Port")
            return

        self.target_ip = target_ip
        self.server_port = target_port
        self.server_addr = (self.target_ip, self.server_port)

        self.lbl_status.config(text=f"Connecting to {self.target_ip}:{self.server_port}...")
        self.btn_join.config(state="disabled")
        self.ip_entry.config(state="disabled")
        self.port_entry.config(state="disabled")

        if hasattr(self, 'btn_host'):
            self.btn_host.config(state="disabled")

        # === CRITICAL FIX: RESET STATE ON JOIN ===
        self.seq_num = 0
        with self.lock:
            self.reliable_buffer.clear()
        self.grid = [[0] * GRID_SIZE for _ in range(GRID_SIZE)]
        self.visual_grid = [["#FFFFFF"] * GRID_SIZE for _ in range(GRID_SIZE)]
        self.clear_all_cells()
        # =========================================

        self.send_message(build_init_message())

    def on_disconnect(self):
        # Shutdown server if I am the host
        if self.server_instance:
            print("[HOST] Shutting down local server...")
            try:
                self.server_instance.shutdown()
            except Exception as e:
                print(f"Error shutting down: {e}")
            self.server_instance = None
            self.server_thread = None

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

            payload = struct.pack("!BHQ", self.my_player_id, cell_id, ts)
            header = build_header(MSG_EVENT, seq_num=curr_seq, payload=payload)
            msg = header + payload

            self.send_reliable(msg, curr_seq)

    # ==============================================================
    # === Network & Reliability ===
    # ==============================================================

    def send_message(self, msg):
        try:
            self.sock.sendto(msg, self.server_addr)
        except Exception as e:
            print(f"[ERROR] Send failed: {e}")

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
            time.sleep(0.1)
            now = time.time()
            with self.lock:
                for seq in list(self.reliable_buffer.keys()):
                    data = self.reliable_buffer[seq]
                    if now - data['last_sent'] > 0.1:  # 100ms Retry
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
                seq_in = header["seq_num"]

                if msg_type == MSG_GENERIC_ACK:
                    acked_seq = parse_ack_payload(payload)
                    with self.lock:
                        if acked_seq in self.reliable_buffer:
                            del self.reliable_buffer[acked_seq]

                elif msg_type == MSG_CELL_UPDATE:
                    self.send_message(build_ack_message(seq_in))
                    r, c, owner = parse_cell_update_payload(payload)
                    self.grid[r][c] = owner

                elif msg_type == MSG_JOIN_RESPONSE:
                    self.handle_join(payload)

                elif msg_type == MSG_SNAPSHOT:
                    self.handle_snapshot(header, payload)

                elif msg_type == MSG_GAME_OVER:
                    self.handle_game_over(payload)

        except Exception:
            pass
        self.root.after(10, self.network_loop)

    # ==============================================================
    # === Drawing & Logic ===
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
        if self.canvas.find_withtag(tag):
            self.canvas.itemconfig(tag, fill=color)
        else:
            self.canvas.create_rectangle(x1, y1, x2, y2, fill=color, outline="", tags=tag)

    def clear_all_cells(self):
        for r in range(GRID_SIZE):
            for c in range(GRID_SIZE):
                tag = f"cell_{r}_{c}"
                self.canvas.delete(tag)

    def render_loop(self):
        if self.my_player_id is not None:
            self.smooth_and_draw()
        self.root.after(16, self.render_loop)

    def smooth_and_draw(self):
        LERP_FACTOR = 0.2
        for r in range(GRID_SIZE):
            for c in range(GRID_SIZE):
                owner_id = self.grid[r][c]
                target_hex = PLAYER_COLORS.get(owner_id, "#FFFFFF")
                current_hex = self.visual_grid[r][c]
                if current_hex != target_hex:
                    new_hex = lerp_color(current_hex, target_hex, LERP_FACTOR)
                    if new_hex == current_hex: new_hex = target_hex
                    self.visual_grid[r][c] = new_hex
                    self.draw_cell(r, c, new_hex)

    # ==============================================================
    # === Handlers ===
    # ==============================================================

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
            if snapshot_id <= self.latest_snapshot_id: return

            server_ts = header['timestamp']
            recv_ts = int(time.time() * 1000)
            latency = recv_ts - server_ts
            self.csv_writer.writerow([snapshot_id, server_ts, recv_ts, latency])
            self.send_message(build_snapshot_ack_message(snapshot_id, server_ts, recv_ts))

            self.latest_snapshot_id = snapshot_id
            self.update_logical_grid(grid_owners)
        except Exception as e:
            print(f"[ERROR] Snapshot parse failed: {e}")

    def update_logical_grid(self, grid_owners):
        for cell_id, owner in enumerate(grid_owners):
            r, c = cell_id // GRID_SIZE, cell_id % GRID_SIZE
            self.grid[r][c] = owner

    def handle_game_over(self, payload):
        winner_id = struct.unpack("!B", payload)[0]
        winner_name = PLAYER_NAMES.get(winner_id, f"Player {winner_id}")
        messagebox.showinfo("Game Over!", f"Winner is {winner_name}!")

        # Explicit Clean Up
        self.grid = [[0] * GRID_SIZE for _ in range(GRID_SIZE)]
        self.visual_grid = [["#FFFFFF"] * GRID_SIZE for _ in range(GRID_SIZE)]
        self.clear_all_cells()

        self.on_disconnect()  # Re-use cleanup logic

    def shutdown(self):
        try:
            self.csv_file.close()
        except:
            pass
        if self.server_instance:
            try:
                self.server_instance.shutdown()
            except:
                pass
        self.root.destroy()
        sys.exit(0)


if __name__ == "__main__":
    root = tk.Tk()
    app = GridClient(root)
    root.mainloop()