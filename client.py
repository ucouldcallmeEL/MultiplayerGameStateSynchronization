import tkinter as tk

# === Configuration ===
GRID_SIZE = 8
CELL_SIZE = 60
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
            btn.grid(row=1, column=i-1, padx=5, pady=5)

        self.status_label = tk.Label(root, text="Selected: Player 1", font=("Arial", 12))
        self.status_label.grid(row=2, column=0, columnspan=4, pady=5)

        # === Game state ===
        self.grid = [[None for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]
        self.draw_grid()

        # === Event bindings ===
        self.canvas.bind("<Button-1>", self.on_click)

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

        # Claim cell for selected player
        self.grid[row][col] = self.current_player
        color = PLAYER_COLORS[self.current_player]
        self.draw_cell(row, col, color)

        # Placeholder for sending a network message later
        self.send_acquire_request(row, col)

    def draw_cell(self, row, col, color):
        """Fill the clicked cell."""
        x1, y1 = col * CELL_SIZE + 2, row * CELL_SIZE + 2
        x2, y2 = x1 + CELL_SIZE - 4, y1 + CELL_SIZE - 4
        self.canvas.create_rectangle(x1, y1, x2, y2, fill=color, outline="")

    def send_acquire_request(self, row, col):
        """
        Placeholder for networking logic.
        Later: send ACQUIRE_REQUEST(cell_id, timestamp, player_id) to server.
        """
        print(f"[DEBUG] Player {self.current_player} clicked cell ({row}, {col})")

# === Run the game ===
if __name__ == "__main__":
    root = tk.Tk()
    app = GridClash(root)
    root.mainloop()
