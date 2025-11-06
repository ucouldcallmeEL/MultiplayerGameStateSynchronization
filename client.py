import tkinter as tk
import time
import socket
import struct
import os
import sys
import csv
import tkinter.messagebox as messagebox

from protocol import (
    build_event_message,
    build_init_message,
    # --- UPDATED Imports ---
    parse_header,
    parse_join_response_payload,
    parse_snapshot_payload,
    build_snapshot_ack_message,
    # ---
    HEADER_SIZE,
    MSG_SNAPSHOT,
    MSG_JOIN_RESPONSE,  # NEW
    MSG_GAME_OVER,
    GRID_SIZE  # Import grid size
)

# === Configuration ===
# GRID_SIZE is now imported
CELL_SIZE = 60
SERVER_IP = "127.0.0.1"
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

        # === Game State ===
        self.grid = [[0 for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]
        self.my_player_id = None
        self.latest_snapshot_id = 0

        # === Networking ===
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setblocking(False)
        self.server_addr = (SERVER_IP, SERVER_PORT)

        # === CSV Logging ===
        self.csv_file = open(f"client_{os.getpid()}_metrics.csv", 'w', newline='')
        self.csv_writer = csv.writer(self.csv_file)
        self.csv_writer.writerow(['snapshot_id','server_timestamp_ms','recv_time_ms','latency_ms'])

        # === GUI Frames ===
        self.main_menu_frame = tk.Frame(root)  # NEW: Main menu frame
        self.game_frame = tk.Frame(root)

        self.main_menu_frame.grid(row=0, column=0, sticky="nsew")
        self.game_frame.grid(row=0, column=0, sticky="nsew")

        self.build_main_menu_ui()  # NEW
        self.build_game_ui()

        # Auto-join support: if '--auto' or env AUTO_JOIN is set, immediately send INIT
        auto = ('--auto' in sys.argv) or (os.getenv('AUTO_JOIN', '').lower() in ('1','true','yes'))
        if auto:
            self.show_main_menu()
            self.on_find_game()
        else:
            self.show_main_menu()  # Start on the main menu

        # Start polling for network messages
        self.root.after(15, self.network_poll)

    # ============================================================
    # === GUI Building ===
    # ============================================================

    def build_main_menu_ui(self):
        """NEW: Builds the simple 'Find Game' button UI."""
        tk.Label(
            self.main_menu_frame,
            text="Grid Clash",
            font=("Arial", 28)
        ).pack(pady=(50, 20))

        self.find_game_btn = tk.Button(
            self.main_menu_frame,
            text="Find Game",
            width=20,
            height=3,
            font=("Arial", 16),
            command=self.on_find_game
        )
        self.find_game_btn.pack(pady=20)

        self.main_status_label = tk.Label(
            self.main_menu_frame,
            text="",
            font=("Arial", 12)
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
        self.draw_grid_lines()

    def show_main_menu(self):
        """NEW: Shows the main menu and resets state."""
        self.my_player_id = None
        self.root.title("Grid Clash")
        self.game_frame.grid_remove()
        self.main_menu_frame.grid()
        self.main_status_label.config(text="")
        self.find_game_btn.config(state="normal")
        # Reset grid state for when re-joining
        self.grid = [[0 for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]

    def show_game(self):
        self.root.title(f"Grid Clash — Player {self.my_player_id}")
        self.main_menu_frame.grid_remove()  # Hide main menu
        self.game_frame.grid()
        self.game_status_label.config(
            text=f"You are {PLAYER_NAMES.get(self.my_player_id, 'Unknown')} (Player {self.my_player_id})",
            fg=PLAYER_COLORS[self.my_player_id]
        )
        self.redraw_full_grid()

    # ============================================================
    # === GUI Callbacks ===
    # ============================================================

    def on_find_game(self):
        """NEW: Called when 'Find Game' button is clicked."""
        self.main_status_label.config(text="Connecting...")
        self.find_game_btn.config(state="disabled")
        self.send_init_message()

    def on_click(self, event):
        if self.my_player_id is None:
            print("[WARN] Clicked grid, but have no player ID.")
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
        """Sends the single INIT message to join."""
        print("[NETWORK] Sending INIT message...")
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
        """Poll for all incoming messages from the server."""
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
            print(f"[NETWORK] Unhandled application error: {e}")

        self.root.after(15, self.network_poll)


    # ============================================================
    # === Network Handlers ===
    # ============================================================

    # --- NEW: Helper function to process snapshot data ---
    def handle_snapshot_data(self, grid_owners):
        """
        Processes a flat list/bytes object of 64 grid owners.
        This is called by both handle_join_response and handle_game_snapshot.
        """
        try:
            for cell_id in range(len(grid_owners)):
                owner = grid_owners[cell_id]
                r = cell_id // GRID_SIZE
                c = cell_id % GRID_SIZE

                if self.grid[r][c] != owner:
                    self.grid[r][c] = owner
                    if self.game_frame.winfo_ismapped():
                        color = PLAYER_COLORS.get(owner, "#000000")
                        self.draw_cell(r, c, color)
        except Exception as e:
            print(f"[ERROR] Failed to process grid data: {e}")


    # --- REFACTORED: Now uses new parser and helper ---
    def handle_join_response(self, payload):
        """
        NEW: We've successfully joined! Server has assigned us an ID
        and sent us the current grid.
        """
        try:
            # Use the new protocol parser
            self.my_player_id, grid_owners = parse_join_response_payload(payload)
            print(f"[NETWORK] Server assigned us Player {self.my_player_id}")

            # Use the new helper to process the grid data
            self.handle_snapshot_data(grid_owners)

            # Now, show the game
            self.show_game()

        except Exception as e:
            print(f"[ERROR] Failed to parse JOIN_RESPONSE: {e}")
            # Reset UI if join fails
            self.show_main_menu()
            self.main_status_label.config(text="Error joining game.")


    # --- REFACTORED: Now uses new parser and helper ---
    def handle_game_snapshot(self, header, payload):
        """
        Handles a game state snapshot.
        """
        try:
            # Parse payload
            num_players, grid_owners = parse_snapshot_payload(payload)

            # Log receipt and send ACK
            snapshot_id = header['snapshot_id']
            server_ts = header['timestamp']
            recv_ts = int(time.time() * 1000)
            latency = recv_ts - server_ts
            self.csv_writer.writerow([snapshot_id, server_ts, recv_ts, latency])
            self.csv_file.flush()

            try:
                ack = build_snapshot_ack_message(snapshot_id, server_ts, recv_ts)
                self.send_message(ack)
            except Exception:
                pass

            # Use the new helper to process the grid data
            self.handle_snapshot_data(grid_owners)

        except Exception as e:
            print(f"[ERROR] Failed to parse SNAPSHOT: {e}")


    def handle_game_over(self, payload):
        """Server announced the game is over."""
        try:
            winner_id = struct.unpack("!B", payload)[0]

            winner_name = PLAYER_NAMES.get(winner_id, f"Player {winner_id}")
            message = f"Game Over!\n\nWinner is {winner_name} (Player {winner_id})!"

            print(f"[GAME OVER] {message}")
            messagebox.showinfo("Game Over!", message)

            # ✅ Clear the local grid before returning to main menu
            self.grid = [[0 for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]
            self.redraw_full_grid()

            # Go back to the main menu
            self.show_main_menu()

        except Exception as e:
            print(f"[ERROR] Failed to parse GAME_OVER: {e}")


# === Run the game ===
if __name__ == "__main__":
    root = tk.Tk()
    app = GridClash(root)
    try:
        root.mainloop()
    finally:
        try:
            app.csv_file.close()
        except Exception:
            pass

