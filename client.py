import tkinter as tk
import time
import socket
import struct
import csv
import os
import sys
import tkinter.messagebox as messagebox  # NEW: For the winner popup


from protocol import (
    build_event_message,
    build_init_message,
    # --- UPDATED Imports ---
    build_snapshot_ack_message,
    parse_header,
    parse_join_response_payload,
    parse_snapshot_payload,
    # ---
    HEADER_SIZE,
    MSG_SNAPSHOT,
    MSG_JOIN_RESPONSE,  # NEW
    MSG_GAME_OVER,
    GRID_SIZE, # Import grid size
    MSG_LOBBY_STATE,
    MSG_CLAIM_SUCCESS,
    MSG_SNAPSHOT_ACK
)

# === Configuration ===
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
    def __init__(self, root, auto_join_player_id=None):
        self.root = root
        self.root.title("Grid Clash")

        # === Auto-join configuration ===
        self.auto_join_player_id = auto_join_player_id
        self.auto_join_attempted = False
        self.lobby_state_received = False

        # === Game State ===
        self.grid = [[0 for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]
        self.my_player_id = None
        self.latest_snapshot_id = 0

        # === Networking ===
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setblocking(False)
        self.server_addr = (SERVER_IP, SERVER_PORT)

        # === CSV Logging Setup ===
        self.csv_file = None
        self.csv_writer = None
        self.init_csv_logging()

        # === GUI Frames ===
        self.main_menu_frame = tk.Frame(root)  # NEW: Main menu frame
        self.game_frame = tk.Frame(root)

        self.main_menu_frame.grid(row=0, column=0, sticky="nsew")
        self.game_frame.grid(row=0, column=0, sticky="nsew")

        self.build_main_menu_ui()  # NEW
        self.build_game_ui()

        self.show_main_menu()  # Start on the main menu

        # Start polling for network messages
        self.root.after(15, self.network_poll)

    def init_csv_logging(self):
        """Initialize CSV logging for client metrics."""
        csv_filename = f'client_{os.getpid()}_metrics.csv'
        self.csv_file = open(csv_filename, 'w', newline='')
        self.csv_writer = csv.writer(self.csv_file)
        # Write header
        self.csv_writer.writerow([
            'snapshot_id',
            'server_timestamp_ms',
            'recv_time_ms',
            'latency_ms'
        ])
        self.csv_file.flush()
        print(f"[METRICS] Client CSV logging initialized: {csv_filename}")

    def log_snapshot_received(self, snapshot_id, server_timestamp_ms, recv_time_ms):
        """Log when a snapshot is received."""
        if self.csv_writer is None:
            return

        latency_ms = recv_time_ms - server_timestamp_ms if server_timestamp_ms else ''

        self.csv_writer.writerow([
            snapshot_id,
            server_timestamp_ms,
            recv_time_ms,
            latency_ms
        ])
        self.csv_file.flush()

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
            action_type=1,
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
            p1, p2, p3, p4 = struct.unpack("!BBBB", payload)
            states = {1: p1, 2: p2, 3: p3, 4: p4}
            self.lobby_state_received = True

            # Auto-join logic: if auto_join_player_id is set and we haven't joined yet
            if self.auto_join_player_id and not self.my_player_id and not self.auto_join_attempted:
                target_id = self.auto_join_player_id
                if target_id in states and states[target_id] == 0:
                    # Slot is available, claim it
                    print(f"[AUTO-JOIN] Automatically claiming Player {target_id}...")
                    self.auto_join_attempted = True
                    msg = build_claim_color_message(target_id)
                    self.send_message(msg)
                elif self.auto_join_player_id not in states:
                    print(f"[AUTO-JOIN] Invalid player ID {target_id}, trying first available...")
                    # Try to find first available slot
                    for pid in [1, 2, 3, 4]:
                        if pid in states and states[pid] == 0:
                            print(f"[AUTO-JOIN] Claiming Player {pid} instead...")
                            self.auto_join_attempted = True
                            msg = build_claim_color_message(pid)
                            self.send_message(msg)
                            break
                else:
                    # Slot is taken, try next available
                    for pid in [1, 2, 3, 4]:
                        if pid in states and states[pid] == 0:
                            print(f"[AUTO-JOIN] Player {target_id} taken, claiming Player {pid} instead...")
                            self.auto_join_attempted = True
                            msg = build_claim_color_message(pid)
                            self.send_message(msg)
                            break

            for player_id, btn in self.lobby_buttons.items():
                is_taken = states[player_id] == 1

                if player_id == self.my_player_id:
                    btn.config(text=f"You (Player {player_id})", state="normal", relief="sunken")
                else:
                    btn.config(
                        text=f"Player {player_id}",
                        state="disabled" if is_taken else "normal",
                        relief="raised"
                    )

            self.lobby_status_label.config(text="Select an available color.")

        except Exception as e:
            print(f"[ERROR] Failed to parse LOBBY_STATE: {e}")

    def handle_claim_success(self, payload):
        try:
            self.my_player_id = struct.unpack("!B", payload)[0]
            print(f"[NETWORK] Server confirmed our slot: Player {self.my_player_id}")
            self.show_game()
        except Exception as e:
            print(f"[ERROR] Failed to parse CLAIM_SUCCESS: {e}")

    def handle_game_snapshot(self, header, payload):
        try:
            snapshot_id = header["snapshot_id"]
            server_timestamp_ms = header["timestamp"]
            recv_time_ms = int(time.time() * 1000)

            # Log the snapshot receipt
            self.log_snapshot_received(snapshot_id, server_timestamp_ms, recv_time_ms)

            # Send ACK back to server
            ack_msg = build_snapshot_ack_message(snapshot_id, server_timestamp_ms, recv_time_ms)
            self.send_message(ack_msg)

            changes_blob = payload[1:]
            expected = GRID_SIZE * GRID_SIZE
            changes = parse_grid_changes(changes_blob, expected)

            for change in changes:
                cell_id = change["cell_id"]
                owner = change["new_owner"]
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
    def handle_game_snapshot(self, payload):
        """
        Handles a game state snapshot.
        """
        try:
            # Use the new protocol parser
            num_players, grid_owners = parse_snapshot_payload(payload)

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

            # Go back to the main menu
            self.show_main_menu()

        except Exception as e:
            print(f"[ERROR] Failed to parse GAME_OVER: {e}")


# === Run the game ===
if __name__ == "__main__":
    # Check for command-line argument for auto-join player ID
    auto_join_id = None
    if len(sys.argv) > 1:
        try:
            auto_join_id = int(sys.argv[1])
            if auto_join_id < 1 or auto_join_id > 4:
                print(f"[WARN] Invalid player ID {auto_join_id}, must be 1-4. Ignoring.")
                auto_join_id = None
            else:
                print(f"[AUTO-JOIN] Will auto-join as Player {auto_join_id}")
        except ValueError:
            print(f"[WARN] Invalid player ID argument: {sys.argv[1]}. Ignoring.")

    root = tk.Tk()
    app = GridClash(root, auto_join_player_id=auto_join_id)
    try:
        root.mainloop()
    finally:
        # Close CSV file on exit
        if hasattr(app, 'csv_file') and app.csv_file:
            app.csv_file.close()