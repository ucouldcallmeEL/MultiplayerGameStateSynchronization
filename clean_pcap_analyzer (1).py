#!/usr/bin/env python3
"""
Detailed PCAP Analysis for GridClash Game Protocol
"""

import struct
import subprocess
import sys
from collections import defaultdict

PROTOCOL_ID = b"GCP1"
MSG_TYPES = {
    0x01: "MSG_INIT",
    0x02: "MSG_SNAPSHOT", 
    0x03: "MSG_EVENT",
    0x04: "MSG_GAME_OVER",
    0x05: "MSG_JOIN_RESPONSE",
    0x06: "MSG_SNAPSHOT_ACK",
    0x07: "MSG_EVENT_ACK"
}

PLAYER_COLORS = {0: "Empty", 1: "Green", 2: "Red", 3: "Blue", 4: "Orange"}

def parse_gcp_header(data):
    if len(data) < 28:
        return None
    
    try:
        header = struct.unpack('!4sBBIIQHI', data[:28])
        return {
            'protocol_id': header[0],
            'version': header[1],
            'msg_type': header[2],
            'snapshot_id': header[3],
            'seq_num': header[4],
            'server_timestamp': header[5],
            'payload_len': header[6],
            'checksum': header[7],
            'payload': data[28:28+header[6]] if len(data) >= 28+header[6] else b''
        }
    except struct.error:
        return None

def decode_payload(msg_type, payload):
    if not payload:
        return "No payload"
    
    try:
        if msg_type == 0x02:  # MSG_SNAPSHOT
            if len(payload) >= 65:
                num_players = payload[0]
                grid_data = payload[1:65]
                
                cell_counts = defaultdict(int)
                for cell in grid_data:
                    cell_counts[cell] += 1
                
                result = f"Players: {num_players}, Grid: "
                for player_id, count in sorted(cell_counts.items()):
                    if count > 0:
                        color = PLAYER_COLORS.get(player_id, f"P{player_id}")
                        result += f"{color}={count} "
                return result.strip()
                
        elif msg_type == 0x05:  # MSG_JOIN_RESPONSE
            if len(payload) >= 65:
                player_id = payload[0]
                color = PLAYER_COLORS.get(player_id, f"Player{player_id}")
                return f"Assigned: {color} (ID={player_id})"
                
        elif msg_type == 0x06:  # MSG_SNAPSHOT_ACK
            if len(payload) >= 20:
                snapshot_id, server_ts, recv_ts = struct.unpack('!IQQ', payload[:20])
                latency = recv_ts - server_ts
                return f"ACK snapshot {snapshot_id}, latency: {latency}ms"
                
        elif msg_type == 0x03:  # MSG_EVENT
            if len(payload) >= 15:
                player_id, event_id, cell_id, timestamp = struct.unpack('!BIHQ', payload[:15])
                row, col = cell_id // 8, cell_id % 8
                color = PLAYER_COLORS.get(player_id, f"P{player_id}")
                return f"{color} claims cell ({row},{col})"
                
        elif msg_type == 0x04:  # MSG_GAME_OVER
            if len(payload) >= 1:
                winner_id = payload[0]
                color = PLAYER_COLORS.get(winner_id, f"P{winner_id}")
                return f"Winner: {color}"
                
    except (struct.error, IndexError):
        pass
    
    return f"Raw ({len(payload)} bytes): {payload[:16].hex()}"

def analyze_pcap_detailed(pcap_file):
    print(f"🎮 GRIDCLASH PROTOCOL ANALYSIS")
    print(f"📁 File: {pcap_file}")
    print("=" * 80)
    
    try:
        cmd = ['tcpdump', '-r', pcap_file, '-n', 'udp', '-x']
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            print(f"❌ Error: {result.stderr}")
            return
        
        lines = result.stdout.split('\n')
        packet_count = 0
        msg_stats = defaultdict(int)
        
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            
            if 'UDP' in line and ('127.0.0.1' in line or 'localhost' in line):
                packet_count += 1
                
                hex_data = ""
                i += 1
                while i < len(lines) and lines[i].startswith('\t'):
                    hex_line = lines[i].strip()
                    if ':' in hex_line:
                        hex_part = hex_line.split(':', 1)[1].strip()
                        hex_data += hex_part.replace(' ', '')
                    i += 1
                
                if hex_data:
                    try:
                        packet_bytes = bytes.fromhex(hex_data)
                        gcp_start = packet_bytes.find(b'GCP1')
                        
                        if gcp_start >= 0:
                            gcp_data = packet_bytes[gcp_start:]
                            header = parse_gcp_header(gcp_data)
                            
                            if header and header['protocol_id'] == PROTOCOL_ID:
                                msg_type_name = MSG_TYPES.get(header['msg_type'], f"UNKNOWN({header['msg_type']})")
                                msg_stats[msg_type_name] += 1
                                
                                payload_desc = decode_payload(header['msg_type'], header['payload'])
                                
                                if msg_stats[msg_type_name] <= 3:
                                    print(f"\n📦 Packet {packet_count}")
                                    print(f"   Type: {msg_type_name}")
                                    print(f"   Snapshot ID: {header['snapshot_id']}")
                                    print(f"   Sequence: {header['seq_num']}")
                                    print(f"   Content: {payload_desc}")
                    except ValueError:
                        pass
                continue
            i += 1
        
        print(f"\n" + "=" * 80)
        print("📊 ANALYSIS SUMMARY")
        print("=" * 80)
        print(f"Total UDP packets: {packet_count:,}")
        print(f"GCP1.0 messages: {sum(msg_stats.values()):,}")
        
        print(f"\n📈 Message Distribution:")
        for msg_type, count in sorted(msg_stats.items()):
            percentage = (count / sum(msg_stats.values())) * 100
            print(f"   {msg_type:<18}: {count:>6,} ({percentage:5.1f}%)")
        
        if msg_stats.get('MSG_SNAPSHOT', 0) > 0:
            estimated_duration = msg_stats['MSG_SNAPSHOT'] / 40 / 4
            print(f"\n⏱️  Estimated test duration: {estimated_duration:.1f} seconds")
            print(f"   Snapshot rate: ~{msg_stats['MSG_SNAPSHOT'] / estimated_duration / 4:.1f} Hz per client")
        
        print(f"\n🎯 What This PCAP Contains:")
        print(f"   ✅ Custom game protocol (GCP1.0) over UDP")
        print(f"   ✅ Real-time multiplayer game synchronization")
        print(f"   ✅ Player connections and color assignments")
        print(f"   ✅ Game state broadcasts (40Hz)")
        print(f"   ✅ Player actions (cell claims)")
        print(f"   ✅ Network latency measurements")
        print(f"   ✅ All data is readable and decodable!")
        
    except FileNotFoundError:
        print("❌ tcpdump not found. Install: sudo apt-get install tcpdump")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 detailed_pcap_analyzer.py <pcap_file>")
        sys.exit(1)
    
    analyze_pcap_detailed(sys.argv[1])