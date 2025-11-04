import tkinter as tk
import time
import socket
import struct
import tkinter.messagebox as messagebox

from protocol import (
    build_event_message,
    build_init_message,
    # --- Removed old imports ---
    parse_header,
    parse_grid_changes,
    HEADER_SIZE,
    MSG_SNAPSHOT,
    MSG_JOIN_RESPONSE, # NEW
    MSG_GAME_OVER
)

# === Configuration ===
GRID_SIZE = 8
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

        # === GUI Frames ===
        self.main_menu_frame = tk.Frame(root) # NEW: Main menu frame
        self.game_frame = tk.Frame(root)
        
        self.main_menu_frame.grid(row=0, column=0, sticky="nsew")
        self.game_frame.grid(row=0, column=0, sticky="nsew")

        self.build_main_menu_ui() # NEW
        self.build_game_ui()

        self.show_main_menu() # Start on the main menu
        
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

        # REMOVED: Back to Lobby button
        # You could add a "Disconnect" or "Main Menu" button here
        # that calls self.show_main_menu()

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
        self.main_menu_frame.grid_remove() # Hide main menu
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

                # NEW: Handle the server's join response
                if header["msg_type"] == MSG_JOIN_RESPONSE:
                    self.handle_join_response(payload)

                elif header["msg_type"] == MSG_SNAPSHOT:
                    self.handle_game_snapshot(payload)

                elif header["msg_type"] == MSG_GAME_OVER:
                    self.handle_game_over(payload)
                
                # --- Removed LOBBY_STATE and CLAIM_SUCCESS handlers ---

        except OSError:
            # This is the universal fix.
            # It catches BlockingIOError, ConnectionResetError,
            # ConnectionRefusedError, and WinError 10022 (Invalid Argument).
            # All are non-fatal socket-level errors, so we just pass.
            pass
        except Exception as e:
            # This will now only catch *real* application errors,
            # like a failure in our parse_header function.
            print(f"[NETWORK] Unhandled application error: {e}")

        self.root.after(15, self.network_poll)
    # ============================================================
    # === Network Handlers ===
    # ============================================================
    
    # --- handle_lobby_state removed ---
    # --- handle_claim_success removed ---

    def handle_join_response(self, payload):
        """
        NEW: We've successfully joined! Server has assigned us an ID
        and sent us the current grid.
        Payload: [Assigned_PID (B)] + [Snapshot_Payload (...)]
        """
        try:
            self.my_player_id = struct.unpack("!B", payload[:1])[0]
            grid_snapshot_payload = payload[1:] # The rest is a snapshot
            
            print(f"[NETWORK] Server assigned us Player {self.my_player_id}")
            
            # This payload is identical to a SNAPSHOT payload
            # Reuse the handler to parse the grid
            self.handle_game_snapshot(grid_snapshot_payload)
            
            # Now, show the game
            self.show_game() 
            
        except Exception as e:
            print(f"[ERROR] Failed to parse JOIN_RESPONSE: {e}")
            # Reset UI if join fails
            self.show_main_menu()
            self.main_status_label.config(text="Error joining game.")


    def handle_game_snapshot(self, payload):
        """
        Handles a game state snapshot.
        Payload: [Num_Players (B)] + [Grid_Changes (...)]
        """
        try:
            # We don't need snapshot_id from header here, but server uses it
            
            # The first byte is num_players,
            # the rest is the grid data blob
            changes_blob = payload[1:]
            expected = GRID_SIZE * GRID_SIZE
            changes = parse_grid_changes(changes_blob, expected)

            for change in changes:
                cell_id = change["cell_id"]
                owner = change["new_owner"]
                r = cell_id // GRID_SIZE
                c = cell_id % GRID_SIZE

                if self.grid[r][c] != owner:
                    self.grid[r][c] = owner
                    if self.game_frame.winfo_ismapped():
                        color = PLAYER_COLORS.get(owner, "#000000")
                        self.draw_cell(r, c, color)

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
    root = tk.Tk()
    app = GridClash(root)
    root.mainloop()