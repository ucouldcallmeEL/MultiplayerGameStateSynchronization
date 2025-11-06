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

PROTOCOL_ID = b'GCP1'  # 4-byte ASCII identifier
VERSION = 1            # Protocol version number

# ==============================================================
# === Message Type Codes ===
# ==============================================================

MSG_INIT = 0x01
MSG_SNAPSHOT = 0x02
MSG_EVENT = 0x03
MSG_GAME_OVER = 0x04
MSG_JOIN_RESPONSE = 0x05
MSG_SNAPSHOT_ACK = 0x06

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

GRID_SIZE = 8
TOTAL_CELLS = GRID_SIZE * GRID_SIZE

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


def build_snapshot_message(grid_data, num_players, snapshot_id, seq_num):
    """
    Constructs a full SNAPSHOT message.
    Payload: [num_players (B)] + [grid_data (64B)]

    Args:
        grid_data (bytes): A 64-byte object representing the grid.
        num_players (int): Number of players (usually 4).
    """
    if len(grid_data) != TOTAL_CELLS:
        raise ValueError(f"Grid data must be {TOTAL_CELLS} bytes long")

    payload = struct.pack("!B", num_players) + grid_data
    header = build_header(MSG_SNAPSHOT, snapshot_id, seq_num, payload=payload)
    return header + payload


# --- NEW: Snapshot Payload Parser ---
def parse_snapshot_payload(payload):
    """
    Parses a SNAPSHOT payload.
    Payload: [num_players (B)] + [grid_data (64B)]

    Returns:
        tuple: (num_players, grid_owners_list)
    """
    # Unpack num_players (1 byte) and grid_data (64 bytes)
    num_players = payload[0]
    # The rest of the payload is the grid data
    grid_owners = payload[1:1 + TOTAL_CELLS]

    if len(grid_owners) != TOTAL_CELLS:
        raise struct.error("Incomplete snapshot payload")

    # grid_owners is already a bytes object of 64 unsigned chars,
    # which can be iterated over directly.
    return num_players, grid_owners


def build_join_response_message(player_id, grid_data):
    """
    Constructs a full JOIN_RESPONSE message.
    Payload: [player_id (B)] + [grid_data (64B)]

    Args:
        player_id (int): The player ID to assign.
        grid_data (bytes): A 64-byte object representing the grid.
    """
    if len(grid_data) != TOTAL_CELLS:
        raise ValueError(f"Grid data must be {TOTAL_CELLS} bytes long")

    payload = struct.pack("!B", player_id) + grid_data
    header = build_header(MSG_JOIN_RESPONSE, payload=payload)
    return header + payload


# --- NEW: Join Response Payload Parser ---
def parse_join_response_payload(payload):
    """
    Parses a JOIN_RESPONSE payload.
    Payload: [player_id (B)] + [grid_data (64B)]

    Returns:
        tuple: (player_id, grid_owners_list)
    """
    # Unpack player_id (1 byte) and grid_data (64 bytes)
    player_id = payload[0]
    grid_owners = payload[1:1 + TOTAL_CELLS]

    if len(grid_owners) != TOTAL_CELLS:
        raise struct.error("Incomplete join response payload")

    return player_id, grid_owners


# --- NEW: Snapshot ACK builder & parser ---
def build_snapshot_ack_message(snapshot_id, server_timestamp_ms, recv_time_ms):
    """
    Constructs a SNAPSHOT_ACK message for the client to send to the server.
    Payload: [snapshot_id (I)] [server_timestamp_ms (Q)] [recv_time_ms (Q)]
    """
    payload = struct.pack("!IQQ", snapshot_id, server_timestamp_ms, recv_time_ms)
    header = build_header(MSG_SNAPSHOT_ACK, snapshot_id=snapshot_id, seq_num=0, payload=payload)
    return header + payload


def parse_snapshot_ack_payload(payload):
    """Parses a SNAPSHOT_ACK payload into its components."""
    snapshot_id, server_ts, recv_ts = struct.unpack("!IQQ", payload)
    return {
        "snapshot_id": snapshot_id,
        "server_timestamp_ms": server_ts,
        "recv_time_ms": recv_ts,
    }


# ==============================================================
# === Example Packet Builders ===
# ==============================================================

def build_event_payload(player_id, action_type, cell_id, timestamp):
    """
    Build an EVENT payload.
    """
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

# ==============================================================
# === Complete Message Builder (for clients) ===
# ==============================================================

def build_event_message(player_id, action_type, cell_id, timestamp, snapshot_id=0, seq_num=0):
    """
    Construct a full EVENT message (header + payload) according to GCP1.0.

    Args:
        player_id (int): ID of the player sending the event.
        action_type (int): Type of event/action (e.g., move, claim, etc.)
        cell_id (int): Grid cell affected.
        timestamp (int): Client event timestamp in ms since epoch.
        snapshot_id (int): Snapshot ID reference (default 0 for standalone).
        seq_num (int): Optional sequence number.

    Returns:
        bytes: Complete binary message ready to send.
    """
    payload = build_event_payload(player_id, action_type, cell_id, timestamp)
    header = build_header(MSG_EVENT, snapshot_id, seq_num, payload)
    return header + payload 

def build_init_message():
    """
    Constructs a full INIT message (header + empty payload) to announce
    a client's presence to the server.
    """
    payload = b""
    # We can use 0 for snapshot_id and seq_num
    header = build_header(MSG_INIT, 0, 0, payload)
    return header + payload
