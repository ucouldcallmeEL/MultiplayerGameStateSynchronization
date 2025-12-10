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
MSG_SNAPSHOT = 0x02  # <--- UNRELIABLE HEARTBEAT
MSG_EVENT = 0x03     # <--- CRITICAL (RELIABLE)
MSG_GAME_OVER = 0x04 # <--- CRITICAL (RELIABLE)
MSG_JOIN_RESPONSE = 0x05
MSG_GENERIC_ACK = 0x07
MSG_CELL_UPDATE = 0x08

# Grid Dimensions
GRID_SIZE = 8
TOTAL_CELLS = GRID_SIZE * GRID_SIZE

# ==============================================================
# === 2. Header Definition ===
# ==============================================================

HEADER_FORMAT = "!4sBBIIQHI"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

def build_header(msg_type, snapshot_id=0, seq_num=0, payload=b""):
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
        "msg_type": unpacked[2],
        "snapshot_id": unpacked[3],
        "seq_num": unpacked[4],
        "timestamp": unpacked[5],
        "checksum": unpacked[7]
    }

# ==============================================================
# === 3. Message Builders ===
# ==============================================================

def build_init_message():
    payload = b""
    return build_header(MSG_INIT, payload=payload) + payload

def build_join_response_message(player_id, grid_data):
    payload = struct.pack("!B", player_id) + grid_data
    return build_header(MSG_JOIN_RESPONSE, payload=payload) + payload

def parse_join_response_payload(payload):
    player_id = payload[0]
    grid_owners = payload[1:1 + TOTAL_CELLS]
    return player_id, grid_owners

def build_event_message(player_id, cell_id, timestamp, seq_num):
    payload = struct.pack("!BHQ", player_id, cell_id, timestamp)
    return build_header(MSG_EVENT, seq_num=seq_num, payload=payload) + payload

def parse_event_payload(data):
    player_id, cell_id, timestamp = struct.unpack("!BHQ", data)
    return {"player_id": player_id, "cell_id": cell_id, "timestamp": timestamp}

# --- SNAPSHOT (UNRELIABLE HEARTBEAT) ---
def build_snapshot_message(grid_data, snapshot_id):
    # Payload: [GridData (64B)]
    return build_header(MSG_SNAPSHOT, snapshot_id=snapshot_id, payload=grid_data) + grid_data

def parse_snapshot_payload(payload):
    return payload[:TOTAL_CELLS]

def build_cell_update_message(seq_num, row, col, owner_id):
    payload = struct.pack("!BBB", row, col, owner_id)
    return build_header(MSG_CELL_UPDATE, seq_num=seq_num, payload=payload) + payload

def parse_cell_update_payload(payload):
    return struct.unpack("!BBB", payload)

def build_game_over_message(winner_id, seq_num):
    payload = struct.pack("!B", winner_id)
    return build_header(MSG_GAME_OVER, seq_num=seq_num, payload=payload) + payload

def build_ack_message(ack_seq_num):
    payload = struct.pack("!I", ack_seq_num)
    return build_header(MSG_GENERIC_ACK, 0, payload=payload) + payload

def parse_ack_payload(payload):
    return struct.unpack("!I", payload)[0]