"""
GridClash Protocol (GCP1.0) - Selective Reliability Update
---------------------------
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
MSG_SNAPSHOT = 0x02  # Kept for backward compat, but unused in logic
MSG_EVENT = 0x03
MSG_GAME_OVER = 0x04
MSG_JOIN_RESPONSE = 0x05
MSG_SNAPSHOT_ACK = 0x06
MSG_GENERIC_ACK = 0x07
MSG_CELL_UPDATE = 0x08
MSG_HEARTBEAT = 0x09  # <--- NEW: For non-critical connection checks

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
    )

def parse_header(data):
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
# === 3. Message Builders ===
# ==============================================================

# --- A. Initialization ---
def build_init_message():
    payload = b""
    header = build_header(MSG_INIT, payload=payload)
    return header + payload

# --- B. Join Response ---
def build_join_response_message(player_id, grid_data):
    if len(grid_data) != TOTAL_CELLS:
        raise ValueError(f"Grid data must be {TOTAL_CELLS} bytes")
    payload = struct.pack("!B", player_id) + grid_data
    header = build_header(MSG_JOIN_RESPONSE, payload=payload)
    return header + payload

def parse_join_response_payload(payload):
    if len(payload) < 1 + TOTAL_CELLS:
        raise struct.error("Incomplete join response payload")
    player_id = payload[0]
    grid_owners = payload[1:1 + TOTAL_CELLS]
    return player_id, grid_owners

# --- C. Game Event (Critical) ---
def build_event_message(player_id, cell_id, timestamp, seq_num):
    payload = struct.pack("!BHQ", player_id, cell_id, timestamp)
    header = build_header(MSG_EVENT, seq_num=seq_num, payload=payload)
    return header + payload

def parse_event_payload(data):
    player_id, cell_id, timestamp = struct.unpack("!BHQ", data)
    return {
        "player_id": player_id,
        "cell_id": cell_id,
        "timestamp": timestamp
    }

# --- D. Heartbeat (Non-Critical) ---
def build_heartbeat_message():
    """Lightweight keep-alive message."""
    payload = b""
    header = build_header(MSG_HEARTBEAT, payload=payload)
    return header + payload

# --- E. Cell Update (Critical) ---
def build_cell_update_message(seq_num, row, col, owner_id):
    """Server tells clients a single cell has changed."""
    payload = struct.pack("!BBB", row, col, owner_id)
    header = build_header(MSG_CELL_UPDATE, seq_num=seq_num, payload=payload)
    return header + payload

def parse_cell_update_payload(payload):
    return struct.unpack("!BBB", payload)

# --- F. Game Over (Critical) ---
def build_game_over_message(winner_id, seq_num):
    payload = struct.pack("!B", winner_id)
    header = build_header(MSG_GAME_OVER, seq_num=seq_num, payload=payload)
    return header + payload

# --- G. Generic ACK ---
def build_ack_message(ack_seq_num):
    payload = struct.pack("!I", ack_seq_num)
    header = build_header(MSG_GENERIC_ACK, 0, 0, payload=payload)
    return header + payload

def parse_ack_payload(payload):
    return struct.unpack("!I", payload)[0]