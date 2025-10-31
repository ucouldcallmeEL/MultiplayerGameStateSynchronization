import tkinter as tk
import time
import socket
from protocol import build_event_message, parse_header, parse_grid_changes, HEADER_SIZE, MSG_SNAPSHOT

# === Configuration ===
GRID_SIZE = 8
CELL_SIZE = 60
SERVER_IP = "127.0.0.1"   # Change to server IP if remote
SERVER_PORT = 9999        # Must match server.py
PLAYER_COLORS = {
    1: "#4CAF50",  # Green
    2: "#F44336",  # Red
    3: "#2196F3",  # Blue
    4: "#FF9800",  # Orange
}


class GridClash:
    def __init__(self, root):
        self.root = root
        self.root.title("Grid Clash — Local Prototype")

        # === Canvas setup ===
        self.canvas = tk.Canvas(
            root,
            width=GRID_SIZE * CELL_SIZE,
            height=GRID_SIZE * CELL_SIZE,
            bg="white"
        )
        self.canvas.grid(row=0, column=0, columnspan=4, padx=10, pady=10)

        # === Create player buttons ===
        self.current_player = 1
        for i in range(1, 5):
            btn = tk.Button(
                root,
                text=f"Player {i}",
                width=10,
                bg=PLAYER_COLORS[i],
                fg="white",
                command=lambda p=i: self.select_player(p)
            )
            btn.grid(row=1, column=i - 1, padx=5, pady=5)

        self.status_label = tk.Label(root, text="Selected: Player 1", font=("Arial", 12))
        self.status_label.grid(row=2, column=0, columnspan=4, pady=5)

        # === Game state ===
        self.grid = [[None for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]
        self.draw_grid()

        # === Event bindings ===
        self.canvas.bind("<Button-1>", self.on_click)

        # === Networking ===
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setblocking(False)
        self.server_addr = (SERVER_IP, SERVER_PORT)
        self.latest_snapshot_id = 0  # will be updated when receiving snapshots later
        
        # Start network polling loop
        self.root.after(15, self.network_poll)

    def draw_grid(self):
        """Draws the grid lines."""
        for i in range(GRID_SIZE):
            for j in range(GRID_SIZE):
                x1, y1 = j * CELL_SIZE, i * CELL_SIZE
                x2, y2 = x1 + CELL_SIZE, y1 + CELL_SIZE
                self.canvas.create_rectangle(x1, y1, x2, y2, outline="gray")

    def select_player(self, player_id):
        """Switch between players locally."""
        self.current_player = player_id
        self.status_label.config(text=f"Selected: Player {player_id}")

    def on_click(self, event):
        """Handle cell clicks."""
        row = event.y // CELL_SIZE
        col = event.x // CELL_SIZE

        # Ignore out-of-bounds
        if not (0 <= row < GRID_SIZE and 0 <= col < GRID_SIZE):
            return

        # Ignore if already claimed
        if self.grid[row][col] is not None:
            return

        # Claim cell locally (for visual feedback)
        self.grid[row][col] = self.current_player
        color = PLAYER_COLORS[self.current_player]
        self.draw_cell(row, col, color)

        # Send acquire request to server
        self.send_acquire_request(row, col)

    def draw_cell(self, row, col, color):
        """Fill the clicked cell."""
        x1, y1 = col * CELL_SIZE + 2, row * CELL_SIZE + 2
        x2, y2 = x1 + CELL_SIZE - 4, y1 + CELL_SIZE - 4
        self.canvas.create_rectangle(x1, y1, x2, y2, fill=color, outline="")

    def send_acquire_request(self, row, col):
        """
        Build and send an EVENT message to the server.
        """
        print(f"[DEBUG] Player {self.current_player} clicked cell ({row}, {col})")

        cell_id = row * GRID_SIZE + col
        timestamp = int(time.time() * 1000)

        msg = build_event_message(
            player_id=self.current_player,
            action_type=1,  # ACQUIRE
            cell_id=cell_id,
            timestamp=timestamp,
            snapshot_id=self.latest_snapshot_id
        )

        self.send_message(msg)

    def send_message(self, msg):
        """
        Send the UDP message to the server.
        """
        try:
            self.sock.sendto(msg, self.server_addr)
            print(f"[NETWORK] Sent {len(msg)} bytes to {self.server_addr}")
        except Exception as e:
            print(f"[ERROR] Failed to send: {e}")

    def network_poll(self):
        """Poll for incoming snapshot messages from the server."""
        try:
            while True:
                data, _ = self.sock.recvfrom(4096)
                if len(data) < HEADER_SIZE:
                    break
                header = parse_header(data)
                payload = data[HEADER_SIZE:]

                if header["msg_type"] == MSG_SNAPSHOT:
                    self.latest_snapshot_id = header["snapshot_id"]

                    if not payload:
                        continue
                    num_players = payload[0]
                    changes_blob = payload[1:]

                    expected = GRID_SIZE * GRID_SIZE
                    changes = parse_grid_changes(changes_blob, expected)

                    for change in changes:
                        cell_id = change["cell_id"]
                        owner = change["new_owner"]
                        r = cell_id // GRID_SIZE
                        c = cell_id % GRID_SIZE
                        if owner == 0:
                            self.grid[r][c] = None
                        else:
                            if self.grid[r][c] != owner:
                                self.grid[r][c] = owner
                                color = PLAYER_COLORS.get(owner, "#000000")
                                self.draw_cell(r, c, color)
        except BlockingIOError:
            pass
        except Exception as e:
            print(f"[NETWORK] recv error: {e}")

        self.root.after(15, self.network_poll)


# === Run the game ===
if __name__ == "__main__":
    root = tk.Tk()
    app = GridClash(root)
    root.mainloop()
