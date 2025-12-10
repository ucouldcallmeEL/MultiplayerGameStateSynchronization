"""
GridClash Protocol (GCP1.0)
---------------------------
Defines binary message structures, constants, and helper functions
for serialization/deserialization.

Architecture:
- Header: Fixed 24-byte structure containing metadata and sequencing.
- Payload: Variable length byte-data specific to the MSG_TYPE.
"""

import struct
import time
import zlib

# ==============================================================
# === 1. Protocol Constants ===
# ==============================================================

PROTOCOL_ID = b'GCP1'
VERSION = 1

# Message Types
MSG_INIT = 0x01
MSG_SNAPSHOT = 0x02
MSG_EVENT = 0x03
MSG_GAME_OVER = 0x04
MSG_JOIN_RESPONSE = 0x05
MSG_SNAPSHOT_ACK = 0x06
MSG_GENERIC_ACK = 0x07  # Generic ACK type
MSG_CELL_UPDATE = 0x08
# Grid Dimensions
GRID_SIZE = 8
TOTAL_CELLS = GRID_SIZE * GRID_SIZE

# ==============================================================
# === 2. Header Definition ===
# ==============================================================

# Format: ID(4s), Ver(B), Type(B), SnapID(I), Seq(I), Time(Q), PayLen(H), Checksum(I)
HEADER_FORMAT = "!4sBBIIQHI"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

def build_header(msg_type, snapshot_id=0, seq_num=0, payload=b""):
    """
    Constructs the standard 24-byte protocol header.
    Automatically calculates timestamp, payload length, and checksum.
    """
    timestamp = int(time.time() * 1000)
    payload_len = len(payload)
    checksum = zlib.crc32(payload) & 0xffffffff
    return struct.pack(
        HEADER_FORMAT,
        PROTOCOL_ID,
        VERSION,
        msg_type,
        snapshot_id,
        seq_num,
        timestamp,
        payload_len,
        checksum
    )   ##It calculates a CRC32 checksum for data integrity, gets the current timestamp, and packs them into binary using struct.pack.##

def parse_header(data):
    """
    Unpacks the header from raw bytes into a dictionary.
    Raises ValueError if data is too short.
    """
    if len(data) < HEADER_SIZE:
        raise ValueError("Incomplete header data")

    unpacked = struct.unpack(HEADER_FORMAT, data[:HEADER_SIZE])
    return {
        "protocol_id": unpacked[0].decode(),
        "version": unpacked[1],
        "msg_type": unpacked[2],
        "snapshot_id": unpacked[3],
        "seq_num": unpacked[4],
        "timestamp": unpacked[5],
        "payload_len": unpacked[6],
        "checksum": unpacked[7]
    }

# ==============================================================
# === 3. Message Logic (Builders & Parsers) ===
# ==============================================================

# --- A. Initialization (Client -> Server) ---

def build_init_message():
    """Client announces presence to server."""
    payload = b""
    header = build_header(MSG_INIT, payload=payload)
    return header + payload


# --- B. Join Response (Server -> Client) ---

def build_join_response_message(player_id, grid_data):
    """Server assigns Player ID and sends initial grid state."""
    if len(grid_data) != TOTAL_CELLS:
        raise ValueError(f"Grid data must be {TOTAL_CELLS} bytes")

    # Payload: [PlayerID (1B)] [GridData (64B)]
    payload = struct.pack("!B", player_id) + grid_data
    header = build_header(MSG_JOIN_RESPONSE, payload=payload)
    return header + payload

def parse_join_response_payload(payload):
    """Returns: (player_id, grid_bytes)"""
    if len(payload) < 1 + TOTAL_CELLS:
        raise struct.error("Incomplete join response payload")

    player_id = payload[0]
    grid_owners = payload[1:1 + TOTAL_CELLS]
    return player_id, grid_owners


# --- C. Game Event (Client -> Server) ---

def build_event_message(player_id, cell_id, timestamp):
    """Client attempts to claim a cell."""
    # Payload: [PlayerID (1B)] [CellID (2B)] [Timestamp (8B)]
    payload = struct.pack("!BHQ", player_id, cell_id, timestamp)
    header = build_header(MSG_EVENT, payload=payload)
    return header + payload

def parse_event_payload(data):
    """Returns: dict with player_id, cell_id, timestamp"""
    player_id, cell_id, timestamp = struct.unpack("!BHQ", data)
    return {
        "player_id": player_id,
        "cell_id": cell_id,
        "timestamp": timestamp
    }


# --- D. World Snapshot (Server -> Client) ---

def build_snapshot_message(grid_data, num_players, snapshot_id, seq_num):
    """Server broadcasts full authoritative grid state."""
    if len(grid_data) != TOTAL_CELLS:
        raise ValueError(f"Grid data must be {TOTAL_CELLS} bytes")

    # Payload: [NumPlayers (1B)] [GridData (64B)]
    payload = struct.pack("!B", num_players) + grid_data
    header = build_header(MSG_SNAPSHOT, snapshot_id, seq_num, payload=payload)
    return header + payload

def parse_snapshot_payload(payload):
    """Returns: (num_players, grid_bytes)"""
    if len(payload) < 1 + TOTAL_CELLS:
        raise struct.error("Incomplete snapshot payload")

    num_players = payload[0]
    grid_owners = payload[1:1 + TOTAL_CELLS]
    return num_players, grid_owners


# --- E. Snapshot ACK (Client -> Server) ---

def build_snapshot_ack_message(snapshot_id, server_ts, recv_ts):
    """Client acknowledges snapshot (used for latency calculation)."""
    # Payload: [SnapshotID (4B)] [ServerTime (8B)] [RecvTime (8B)]
    payload = struct.pack("!IQQ", snapshot_id, server_ts, recv_ts)
    header = build_header(MSG_SNAPSHOT_ACK, snapshot_id=snapshot_id, payload=payload)
    return header + payload

def parse_snapshot_ack_payload(payload):
    """Returns: dict with snapshot_id, server_timestamp_ms, recv_time_ms"""
    snapshot_id, server_ts, recv_ts = struct.unpack("!IQQ", payload)
    return {
        "snapshot_id": snapshot_id,
        "server_timestamp_ms": server_ts,
        "recv_time_ms": recv_ts,
    }
# --- H. Cell Update (Server -> Client) ---
def build_cell_update_message(seq_num, row, col, owner_id):
    """Server tells clients a single cell has changed."""
    # Payload: [Row(1B)] [Col(1B)] [OwnerID(1B)]
    payload = struct.pack("!BBB", row, col, owner_id)
    header = build_header(MSG_CELL_UPDATE, seq_num=seq_num, payload=payload)
    return header + payload

def parse_cell_update_payload(payload):
    return struct.unpack("!BBB", payload)


# --- F. Game Over (Server -> Client) ---

def build_game_over_message(winner_id):
    """Server announces the winner."""
    # Payload: [WinnerID (1B)]
    payload = struct.pack("!B", winner_id)
    header = build_header(MSG_GAME_OVER, payload=payload)
    return header + payload


# --- G. Generic ACK (Reliability Layer) ---

def build_ack_message(ack_seq_num):
    """Acks a specific sequence number."""
    # Payload: [AckedSeqNum (4B)]
    payload = struct.pack("!I", ack_seq_num)
    # We use snapshot_id=0, seq_num=0 for ACKs themselves to avoid loops
    header = build_header(MSG_GENERIC_ACK, 0, 0, payload=payload)
    return header + payload

def parse_ack_payload(payload):
    """Returns: acked_seq_num"""
    return struct.unpack("!I", payload)[0]

# ==============================================================
# === 4. Utilities ===
# ==============================================================

def validate_checksum(header_info, payload):
    """Verify that the CRC32 checksum matches the payload."""
    computed = zlib.crc32(payload) & 0xffffffff
    return computed == header_info["checksum"]