"""
GridClash Protocol (GCP1.0)
---------------------------
Implements the binary message format and header structure described
in the Mini-RFC specification.

Authoritative game state is maintained server-side.
Clients send EVENT and INIT messages; server broadcasts SNAPSHOTs.
"""

import struct
import time
import zlib

# ==============================================================
# === Protocol Metadata ===
# ==============================================================

PROTOCOL_ID = b'GCP1'   # 4-byte ASCII identifier
VERSION = 1             # Protocol version number

# ==============================================================
# === Message Type Codes ===
# ==============================================================

MSG_INIT = 0x01
MSG_SNAPSHOT = 0x02
MSG_EVENT = 0x03
MSG_GAME_OVER = 0x04

# ==============================================================
# === Header Structure ===
# ==============================================================
# Field sizes and order:
# protocol_id (4s)
# version (B)
# msg_type (B)
# snapshot_id (I)
# seq_num (I)
# server_timestamp (Q)
# payload_len (H)
# checksum (I)

HEADER_FORMAT = "!4sBBIIQHI"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

# ==============================================================
# === Header Construction & Parsing ===
# ==============================================================

def build_header(msg_type, snapshot_id=0, seq_num=0, payload=b""):
    """
    Build and return a binary header for a packet.

    Args:
        msg_type (int): One of the defined message type constants.
        snapshot_id (int): Snapshot identifier.
        seq_num (int): Packet sequence number.
        payload (bytes): Optional payload data (used to compute checksum).

    Returns:
        bytes: Packed header ready to be concatenated with payload.
    """
    timestamp = int(time.time() * 1000)  # ms since epoch
    payload_len = len(payload)
    checksum = zlib.crc32(payload) & 0xffffffff  # CRC32 checksum of payload

    header = struct.pack(
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
    return header


def parse_header(data):
    """
    Unpack a binary header into a dictionary.

    Args:
        data (bytes): Raw UDP packet bytes.

    Returns:
        dict: Parsed header fields.
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
# === Payload Helpers (Structures) ===
# ==============================================================

# Snapshot payload example format:
# [num_players (B)] [player_states...] [grid_changes...] [redundant_snapshots]

# Player state struct: player_id (B), x (f), y (f)
PLAYER_STATE_FORMAT = "!Bff"
PLAYER_STATE_SIZE = struct.calcsize(PLAYER_STATE_FORMAT)

# Grid change struct: cell_id (H), new_owner (B)
GRID_CHANGE_FORMAT = "!HB"
GRID_CHANGE_SIZE = struct.calcsize(GRID_CHANGE_FORMAT)


def build_player_state(player_id, x, y):
    """Pack a single player state entry."""
    return struct.pack(PLAYER_STATE_FORMAT, player_id, x, y)


def parse_player_states(data, count):
    """Unpack multiple player states."""
    states = []
    offset = 0
    for _ in range(count):
        chunk = data[offset:offset + PLAYER_STATE_SIZE]
        pid, x, y = struct.unpack(PLAYER_STATE_FORMAT, chunk)
        states.append({"player_id": pid, "x": x, "y": y})
        offset += PLAYER_STATE_SIZE
    return states


def build_grid_change(cell_id, new_owner):
    """Pack a single grid cell update."""
    return struct.pack(GRID_CHANGE_FORMAT, cell_id, new_owner)


def parse_grid_changes(data, count):
    """Unpack multiple grid changes."""
    changes = []
    offset = 0
    for _ in range(count):
        chunk = data[offset:offset + GRID_CHANGE_SIZE]
        cell_id, new_owner = struct.unpack(GRID_CHANGE_FORMAT, chunk)
        changes.append({"cell_id": cell_id, "new_owner": new_owner})
        offset += GRID_CHANGE_SIZE
    return changes


# ==============================================================
# === Example Packet Builders ===
# ==============================================================

def build_event_payload(player_id, action_type, cell_id, timestamp=None):
    """
    Build an EVENT payload.
    """
    if timestamp is None:
        timestamp = int(time.time() * 1000)
    return struct.pack("!BBHQ", player_id, action_type, cell_id, timestamp)


def parse_event_payload(data):
    """Parse an EVENT message payload."""
    player_id, action_type, cell_id, timestamp = struct.unpack("!BBHQ", data)
    return {
        "player_id": player_id,
        "action_type": action_type,
        "cell_id": cell_id,
        "timestamp": timestamp
    }


# ==============================================================
# === Utility Functions ===
# ==============================================================

def validate_checksum(header_info, payload):
    """Verify that the CRC32 checksum matches the payload."""
    computed = zlib.crc32(payload) & 0xffffffff
    return computed == header_info["checksum"]
