import tkinter as tk
import time
import socket
import struct
import os
import sys
import csv
import subprocess  # <--- NEW: To run the server
import tkinter.messagebox as messagebox

from protocol import (
    build_event_message,
    build_init_message,
    parse_header,
    parse_join_response_payload,
    parse_snapshot_payload,
    build_snapshot_ack_message,
    HEADER_SIZE,
    MSG_SNAPSHOT,
    MSG_JOIN_RESPONSE,
    MSG_GAME_OVER,
    GRID_SIZE
)

# === Configuration ===
CELL_SIZE = 60
DEFAULT_SERVER_IP = "127.0.0.1"
SERVER_PORT = 9999
PLAYER_COLORS = {
    1: "#4CAF50",  # Green
    2: "#F44336",  # Red
    3: "#2196F3",  # Blue
    4: "#FF9800",  # Orange
    0: "#FFFFFF",  # Empty
}
PLAYER_NAMES = {
    1: "Green",
    2: "Red",
    3: "Blue",
    4: "Orange"
}


class GridClash:
    def __init__(self, root):
        self.root = root
        self.root.title("Grid Clash")

        # Handle Window Close (to kill server if hosting)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        # === Game State ===
        self.grid = [[0 for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]
        self.my_player_id = None
        self.latest_snapshot_id = 0
        self.server_process = None  # <--- NEW: Track local server process

        # === Networking ===
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setblocking(False)
        self.target_ip = DEFAULT_SERVER_IP
        self.server_addr = (self.target_ip, SERVER_PORT)

        # === CSV Logging ===
        self.csv_file = open(f"client_{os.getpid()}_metrics.csv", 'w', newline='')
        self.csv_writer = csv.writer(self.csv_file)
        self.csv_writer.writerow(['snapshot_id', 'server_timestamp_ms', 'recv_time_ms', 'latency_ms'])

        # === GUI Frames ===
        self.main_menu_frame = tk.Frame(root)
        self.game_frame = tk.Frame(root)

        self.main_menu_frame.grid(row=0, column=0, sticky="nsew")
        self.game_frame.grid(row=0, column=0, sticky="nsew")

        self.build_main_menu_ui()
        self.build_game_ui()

        # Auto-join support
        auto = ('--auto' in sys.argv) or (os.getenv('AUTO_JOIN', '').lower() in ('1', 'true', 'yes'))
        if auto:
            self.show_main_menu()
            self.on_join_game()  # Default to join in auto mode
        else:
            self.show_main_menu()

        # Start polling for network messages
        self.root.after(15, self.network_poll)

    # ============================================================
    # === GUI Building ===
    # ============================================================

    def build_main_menu_ui(self):
        tk.Label(
            self.main_menu_frame,
            text="Grid Clash",
            font=("Arial", 28, "bold")
        ).pack(pady=(40, 10))

        # --- IP Address Input ---
        ip_frame = tk.Frame(self.main_menu_frame)
        ip_frame.pack(pady=10)

        tk.Label(ip_frame, text="Server IP:", font=("Arial", 12)).pack(side=tk.LEFT, padx=5)

        self.ip_entry = tk.Entry(ip_frame, font=("Arial", 12), width=15)
        self.ip_entry.insert(0, DEFAULT_SERVER_IP)
        self.ip_entry.pack(side=tk.LEFT)

        # --- Buttons ---
        btn_frame = tk.Frame(self.main_menu_frame)
        btn_frame.pack(pady=20)

        # Host Button
        self.host_btn = tk.Button(
            btn_frame,
            text="Host & Play\n(Run Server)",
            width=15,
            height=2,
            bg="#e1f5fe",
            font=("Arial", 12),
            command=self.on_host_game
        )
        self.host_btn.pack(side=tk.LEFT, padx=10)

        # Join Button
        self.join_btn = tk.Button(
            btn_frame,
            text="Join Game",
            width=15,
            height=2,
            font=("Arial", 12),
            command=self.on_join_game
        )
        self.join_btn.pack(side=tk.LEFT, padx=10)

        self.main_status_label = tk.Label(
            self.main_menu_frame,
            text="",
            font=("Arial", 11),
            fg="gray"
        )
        self.main_status_label.pack(pady=10)

    def build_game_ui(self):
        self.canvas = tk.Canvas(
            self.game_frame,
            width=GRID_SIZE * CELL_SIZE,
            height=GRID_SIZE * CELL_SIZE,
            bg="white"
        )
        self.canvas.grid(row=0, column=0, padx=10, pady=10)
        self.canvas.bind("<Button-1>", self.on_click)

        self.game_status_label = tk.Label(
            self.game_frame,
            text="Waiting for server...",
            font=("Arial", 12)
        )
        self.game_status_label.grid(row=2, column=0, pady=5)

        # Add a disconnect button in game
        tk.Button(self.game_frame, text="Disconnect / Main Menu", command=self.on_disconnect).grid(row=3, column=0,
                                                                                                   pady=10)

        self.draw_grid_lines()

    def show_main_menu(self):
        self.my_player_id = None
        self.root.title("Grid Clash")
        self.game_frame.grid_remove()
        self.main_menu_frame.grid()
        self.main_status_label.config(text="")

        self.host_btn.config(state="normal")
        self.join_btn.config(state="normal")

        # Reset grid state
        self.grid = [[0 for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]

    def show_game(self):
        self.root.title(f"Grid Clash — Player {self.my_player_id}")
        self.main_menu_frame.grid_remove()
        self.game_frame.grid()
        self.game_status_label.config(
            text=f"You are {PLAYER_NAMES.get(self.my_player_id, 'Unknown')} (Player {self.my_player_id})",
            fg=PLAYER_COLORS[self.my_player_id]
        )
        self.redraw_full_grid()

    # ============================================================
    # === GUI Callbacks ===
    # ============================================================

    def on_host_game(self):
        """Starts server.py as a subprocess, then joins localhost."""
        self.host_btn.config(state="disabled")
        self.join_btn.config(state="disabled")

        try:
            # Check if server.py exists
            if not os.path.exists("server.py"):
                messagebox.showerror("Error", "server.py not found in this directory!")
                self.show_main_menu()
                return

            self.main_status_label.config(text="Starting Local Server...")

            # Start server process
            # uses sys.executable to ensure we use the same python interpreter
            self.server_process = subprocess.Popen([sys.executable, "server.py"])

            # Give server a moment to start
            self.root.after(1000, self.finish_hosting_setup)

        except Exception as e:
            messagebox.showerror("Error", f"Failed to start server: {e}")
            self.show_main_menu()

    def finish_hosting_setup(self):
        """Called after server process is launched."""
        # Force IP to localhost
        self.ip_entry.delete(0, tk.END)
        self.ip_entry.insert(0, "127.0.0.1")
        self.on_join_game()

    def on_join_game(self):
        """Connects to the IP in the entry field."""
        ip = self.ip_entry.get().strip()
        if not ip:
            messagebox.showwarning("Input Error", "Please enter a Server IP")
            return

        self.target_ip = ip
        self.server_addr = (self.target_ip, SERVER_PORT)

        self.main_status_label.config(text=f"Connecting to {self.target_ip}...")
        self.host_btn.config(state="disabled")
        self.join_btn.config(state="disabled")

        self.send_init_message()

    def on_disconnect(self):
        """Manually go back to menu (and kill server if we hosted it)."""
        # If we want to keep server alive when disconnecting, remove the stop_local_server call.
        # But usually "Disconnect" implies leaving the session.
        self.stop_local_server()
        self.show_main_menu()

    def on_close(self):
        """Cleanup handler when window X is pressed."""
        self.stop_local_server()
        try:
            self.csv_file.close()
        except:
            pass
        self.root.destroy()
        sys.exit(0)

    def stop_local_server(self):
        """Kills the subprocess if it exists."""
        if self.server_process:
            print("[SYSTEM] Stopping local server...")
            self.server_process.terminate()
            self.server_process = None

    def on_click(self, event):
        if self.my_player_id is None:
            return

        row = event.y // CELL_SIZE
        col = event.x // CELL_SIZE
        if not (0 <= row < GRID_SIZE and 0 <= col < GRID_SIZE):
            return

        self.send_acquire_request(row, col)

    # ============================================================
    # === Drawing ===
    # ============================================================

    def draw_grid_lines(self):
        for i in range(GRID_SIZE):
            for j in range(GRID_SIZE):
                x1, y1 = j * CELL_SIZE, i * CELL_SIZE
                x2, y2 = x1 + CELL_SIZE, y1 + CELL_SIZE
                self.canvas.create_rectangle(x1, y1, x2, y2, outline="gray")

    def draw_cell(self, row, col, color):
        x1, y1 = col * CELL_SIZE + 2, row * CELL_SIZE + 2
        x2, y2 = x1 + CELL_SIZE - 4, y1 + CELL_SIZE - 4
        tag = f"cell_{row}_{col}"
        self.canvas.delete(tag)
        self.canvas.create_rectangle(x1, y1, x2, y2, fill=color, outline="", tags=(tag))

    def redraw_full_grid(self):
        for r in range(GRID_SIZE):
            for c in range(GRID_SIZE):
                owner = self.grid[r][c]
                color = PLAYER_COLORS.get(owner, "#000000")
                self.draw_cell(r, c, color)

    # ============================================================
    # === Networking ===
    # ============================================================

    def send_init_message(self):
        print(f"[NETWORK] Sending INIT to {self.server_addr}...")
        msg = build_init_message()
        self.send_message(msg)

    def send_acquire_request(self, row, col):
        cell_id = row * GRID_SIZE + col
        timestamp = int(time.time() * 1000)
        msg = build_event_message(
            player_id=self.my_player_id,
            cell_id=cell_id,
            timestamp=timestamp,
            snapshot_id=self.latest_snapshot_id
        )
        self.send_message(msg)

    def send_message(self, msg):
        try:
            self.sock.sendto(msg, self.server_addr)
        except Exception as e:
            print(f"[ERROR] Failed to send: {e}")

    def network_poll(self):
        try:
            while True:
                data, _ = self.sock.recvfrom(4096)
                if len(data) < HEADER_SIZE:
                    break
                header = parse_header(data)
                payload = data[HEADER_SIZE:]

                if header["msg_type"] == MSG_JOIN_RESPONSE:
                    self.handle_join_response(payload)

                elif header["msg_type"] == MSG_SNAPSHOT:
                    self.handle_game_snapshot(header, payload)

                elif header["msg_type"] == MSG_GAME_OVER:
                    self.handle_game_over(payload)
        except OSError:
            pass
        except Exception as e:
            print(f"[NETWORK] Error: {e}")

        self.root.after(15, self.network_poll)

    # ============================================================
    # === Network Handlers ===
    # ============================================================

    def handle_snapshot_data(self, grid_owners):
        try:
            for cell_id in range(len(grid_owners)):
                owner = grid_owners[cell_id]
                r = cell_id // GRID_SIZE
                c = cell_id % GRID_SIZE

                if self.grid[r][c] != owner:
                    self.grid[r][c] = owner

                    # --- FIX START ---
                    # REMOVED: if self.game_frame.winfo_ismapped():
                    # We should always update the canvas, even if hidden.
                    color = PLAYER_COLORS.get(owner, "#000000")
                    self.draw_cell(r, c, color)
                    # --- FIX END ---

        except Exception as e:
            print(f"[ERROR] Failed to process grid data: {e}")
    def handle_join_response(self, payload):
        try:
            self.my_player_id, grid_owners = parse_join_response_payload(payload)
            print(f"[NETWORK] Joined as Player {self.my_player_id}")
            self.handle_snapshot_data(grid_owners)
            self.show_game()
        except Exception as e:
            print(f"[ERROR] JOIN failure: {e}")
            self.show_main_menu()
            self.main_status_label.config(text="Error joining.")

    def handle_game_snapshot(self, header, payload):
        try:
            num_players, grid_owners = parse_snapshot_payload(payload)

            snapshot_id = header['snapshot_id']
            server_ts = header['timestamp']
            recv_ts = int(time.time() * 1000)
            latency = recv_ts - server_ts
            self.csv_writer.writerow([snapshot_id, server_ts, recv_ts, latency])

            # Send ACK
            try:
                ack = build_snapshot_ack_message(snapshot_id, server_ts, recv_ts)
                self.send_message(ack)
            except:
                pass

            self.handle_snapshot_data(grid_owners)
        except Exception as e:
            print(f"[ERROR] SNAPSHOT failure: {e}")

    def handle_game_over(self, payload):
        try:
            winner_id = struct.unpack("!B", payload)[0]
            winner_name = PLAYER_NAMES.get(winner_id, f"Player {winner_id}")
            message = f"Game Over!\n\nWinner is {winner_name} (Player {winner_id})!"

            messagebox.showinfo("Game Over!", message)

            self.grid = [[0 for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]
            self.redraw_full_grid()
            self.show_main_menu()

            # If we hosted the game, stop the server on Game Over?
            # Usually better to keep it running for a rematch, but that depends on preference.
            # self.stop_local_server()

        except Exception as e:
            print(f"[ERROR] GAME_OVER failure: {e}")


if __name__ == "__main__":
    root = tk.Tk()
    app = GridClash(root)
    try:
        root.mainloop()
    finally:
        try:
            app.csv_file.close()
        except:
            pass
        # Ensure server is killed if script exits
        app.stop_local_server()