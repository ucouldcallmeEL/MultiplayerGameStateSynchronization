# GridClash - Multiplayer Game State Synchronization

A real-time multiplayer game where up to 4 players compete to claim cells on an 8×8 grid. The first player to fill the entire grid wins.

## Table of Contents

1. [Game Overview](#game-overview)
2. [Project Structure](#project-structure)
3. [Required Dependencies](#required-dependencies)
4. [Quick Start](#quick-start)
5. [Phase 1 Scope](#phase-1-scope)
6. [Protocol Overview](#protocol-overview)
7. [Technical Details](#technical-details)
8. [Running Automated Tests](#running-automated-tests)
9. [Verifying Test Results](#verifying-test-results)
10. [Phase 2 Experiments](#phase-2-experiments)
11. [Troubleshooting](#troubleshooting)

## Game Overview

GridClash is a networked multiplayer game built with Python that demonstrates real-time game state synchronization over UDP. Players click on grid cells to claim them, and the server broadcasts periodic game state snapshots to keep all clients in sync.

**Game Features:**
- Real-time multiplayer
- Server-authoritative game state
- Periodic state snapshots over UDP
- Win condition detection
- Color-coded players (Green, Red, Blue, Orange)

**Game Rules:**
1. **Objective:** Fill the entire 8×8 grid with your color
2. **Players:** Up to 4 players can join
3. **Turns:** Click any empty cell at any time to claim it
4. **Winner:** First player to claim the grid wins
5. **Reset:** Game automatically resets after a win

## Project Structure

```
MultiplayerGameStateSynchronization/
├── server.py              # Game server (manages state, broadcasts updates)
├── client.py              # Game client (GUI, connects to server)
├── protocol.py            # Network protocol definitions (GCP1.0)
├── run_baseline_test.py   # Automated test runner
└── README.md              # This file
```

## Required Dependencies

- **Python 3.7+** (check with `python3 --version`)
- **tkinter** (usually included with Python)

For automated testing (Linux only):
- **psutil** (installed automatically by the script)
- **tcpdump** or **tshark** (packet capture)
  - Linux install example: `sudo apt-get install tcpdump`

## Quick Start

### Running the Game

1. **Start the server:**
   ```bash
   python3 server.py
   ```
   You should see: `[SERVER] Running on 0.0.0.0:9999`

2. **Start clients** (in separate terminals):
   ```bash
   python3 client.py
   ```
   Repeat this for each player (up to 4).

3. **Play:**
   - Click "Find Game" in the client window
   - Wait for the server to assign you a player slot
   - Click on grid cells to claim them
   - First player to fill the entire grid wins

## Phase 1 Scope

Phase 1 focuses on a working prototype and a baseline local test under ideal conditions. Specifically:

- **Game**: 8×8 grid, up to 4 players. Players click cells to claim them. First to claim the grid wins; server resets the round.
- **Protocol**: UDP-based with these messages:
  - `MSG_INIT` (client → server)
  - `MSG_JOIN_RESPONSE` (server → client)
  - `MSG_SNAPSHOT` (server → clients, periodic state)
  - `MSG_EVENT` (client → server, cell claim)
  - `MSG_SNAPSHOT_ACK` (client → server, for latency measurement)
  - `MSG_GAME_OVER` (server → clients)
- **Implementation**: `server.py`, `client.py`, and `protocol.py` implement INIT/DATA exchanges and periodic state synchronization.
- **Automation**: `run_baseline_test.py` runs a local baseline test (Linux-only), starts the server and multiple clients, captures packets, and logs metrics to CSV.
- **Acceptance Criteria** (evaluated by the test):
  - ≥20 updates/sec per client
  - ≤50ms average end-to-end latency
  - <60% average server CPU utilization

## Protocol Overview

GridClash uses a custom UDP protocol (GCP1.0) for communication.

### Message Types

- `MSG_INIT (0x01)` - Client requests to join the game
- `MSG_SNAPSHOT (0x02)` - Server broadcasts current game state
- `MSG_EVENT (0x03)` - Client sends cell claim action
- `MSG_GAME_OVER (0x04)` - Server announces game completion
- `MSG_JOIN_RESPONSE (0x05)` - Server assigns player ID and sends initial grid state
- `MSG_SNAPSHOT_ACK (0x06)` - Client acknowledges snapshot receipt (for latency measurement)

### Header Structure (common to all messages)

All messages share a common 28-byte header:

```
Field            Type    Size    Description
protocol_id      bytes   4       Protocol identifier ("GCP1")
version          uint8   1       Protocol version (1)
msg_type         uint8   1       Message type code
snapshot_id      uint32  4       Snapshot identifier
seq_num          uint32  4       Sequence number
server_timestamp uint64  8       Server timestamp (ms since epoch)
payload_len      uint16  2       Payload length in bytes
checksum         uint32  4       CRC32 checksum of payload
```

### Message Flow

1. **Client Initiation:** Client sends `MSG_INIT` to server
2. **Server Response:** Server responds with `MSG_JOIN_RESPONSE` containing player ID and initial grid state
3. **Game Loop:**
   - Server broadcasts `MSG_SNAPSHOT` at 40Hz (25ms intervals)
   - Clients send `MSG_EVENT` when claiming cells
   - Clients send `MSG_SNAPSHOT_ACK` to acknowledge snapshots (for latency measurement)
4. **Game End:** Server sends `MSG_GAME_OVER` when a player wins

## Technical Details

- **Protocol:** Custom UDP protocol (GCP1.0)
- **Updates:** Periodic server snapshots over UDP
- **Network:** UDP on port 9999
- **Architecture:** Server-authoritative
- **Threading:** Multi-threaded server (receive loop + broadcast loop)
- **Grid Size:** 8×8 (64 cells total)
- **Player Colors:** Green (1), Red (2), Blue (3), Orange (4)

## Running Automated Tests

The project includes an automated baseline test script to verify performance:

```bash
python3 run_baseline_test.py
```

This script will check/install required packages, verify packet-capture tools, start the server and four clients, run for ~60 seconds, collect metrics and a packet capture, and print PASS/FAIL against the Phase 1 criteria.

**Test Output:**
Results are saved to `test_results/baseline_YYYY-MM-DD_HH-MM-SS/` containing:
- `metrics.csv` - Server performance metrics (timestamp, client_id, seq_num, CPU usage)
- `client_*_metrics.csv` - Client-side metrics (snapshot_id, server_timestamp, receive_timestamp)
- `baseline_test.pcap` - Network packet capture for analysis
- `server.log` - Server stdout/stderr logs
- `packet_capture.log` - Packet capture tool logs

**Test Duration:** 60 seconds (configurable in the script)

## Verifying Test Results

After running the automated test, verify the results:

### Check CSV Files

The `metrics.csv` file contains server-side metrics:
- `server_timestamp_ms` - When snapshot was sent
- `client_id` - Target client ID
- `seq_num` - Sequence number
- `cpu_percent` - Server CPU usage at send time

The `client_*_metrics.csv` files contain client-side metrics:
- `snapshot_id` - Snapshot identifier
- `server_timestamp_ms` - Server timestamp from header
- `recv_time_ms` - When client received the snapshot

### Calculate Metrics

**Update Rate:** Count snapshot sends per client per second (should be ≥20)

**Latency:** Calculate `recv_time_ms - server_timestamp_ms` for each ACK (should average ≤50ms)

**CPU Usage:** Average the `cpu_percent` column (should be <60%)

### Analyze Packet Capture

Use `tcpdump` or `tshark` to analyze the `.pcap` file:

```bash
# Count packets
tcpdump -r baseline_test.pcap | wc -l

# Filter by message type (requires understanding header structure)
tshark -r baseline_test.pcap -T fields -e udp.port
```

### Automated Verification

The test script automatically calculates and displays:
- Update rate per client
- Average latency
- Average CPU usage
- PASS/FAIL status for each criterion

## Phase 2 Experiments

The project includes a comprehensive test suite (`run_experiments.py`) that validates game performance under various network conditions using Linux traffic control (`tc netem`).

### Running Phase 2 Experiments

```bash
sudo  ./run_all_tests.sh
```

**Note:** Root privileges are required for network emulation.

### Test Scenarios

The experiment runner automatically executes the following scenarios:

#### 1. Baseline (No Impairment)
- **Description:** Ideal network conditions with no packet loss or delay
- **Purpose:** Establish performance baseline for comparison
- **Acceptance Criteria:**
  - ≥20 updates/sec per client
  - ≤50ms average end-to-end latency
  - <60% average server CPU utilization

#### 2. Loss 2% (LAN-like)
- **Description:** 2% packet loss simulating local area network conditions
- **Network Command:** `sudo tc qdisc add dev lo root netem loss 2%`
- **Purpose:** Validate interpolation and smooth gameplay under minor packet loss
- **Acceptance Criteria:**
  - Mean position error ≤0.5 units
  - 95th percentile position error ≤1.5 units
  - Graceful interpolation implemented

#### 3. Loss 5% (WAN-like)
- **Description:** 5% packet loss simulating wide area network conditions
- **Network Command:** `sudo tc qdisc add dev lo root netem loss 5%`
- **Purpose:** Test critical event reliability under moderate packet loss
- **Acceptance Criteria:**
  - Critical events reliability ≥99%
  - Critical events delivered within 200ms
  - System remains stable

#### 4. Delay 100ms (WAN Delay)
- **Description:** 100ms network delay simulating long-distance connections
- **Network Command:** `sudo tc qdisc add dev lo root netem delay 100ms`
- **Purpose:** Validate functionality and reliability under high latency
- **Acceptance Criteria:**
  - Game remains functional
  - Clients continue receiving updates reliably

### Experiment Output

Each test scenario generates a timestamped directory in `test_results/` containing:

**Evidence Files:**
- `trace.pcap` - Complete packet capture for network analysis
- `server.log` - Server stdout/stderr logs
- `client_1.log` through `client_4.log` - Individual client logs
- `netem_commands.txt` - Network emulation commands used
- `evidence_summary.txt` - Overview of collected evidence

**Metrics Files:**
- `metrics.csv` - Server-side performance metrics
  - `server_timestamp_ms` - When snapshot was sent
  - `client_id` - Target client ID
  - `seq_num` - Sequence number
  - `cpu_percent` - Server CPU usage at send time
- `client_*_metrics.csv` - Client-side metrics for each client
  - `snapshot_id` - Snapshot identifier
  - `server_timestamp_ms` - Server timestamp from header
  - `recv_time_ms` - When client received the snapshot
  - `latency_ms` - Calculated end-to-end latency
  - `position_error` - Position error for interpolation validation (where applicable)

**Analysis Files:**
- `analysis_results.txt` - Detailed acceptance criteria validation with pass/fail status

### Experiment Configuration

Key parameters in `run_experiments.py`:

```python
TEST_DURATION = 60        # Seconds per scenario
NUM_CLIENTS = 4          # Number of automated clients
INTERFACE = "lo"         # Network interface (use 'lo' for localhost)
SERVER_PORT = 9999       # Server port
```

### Understanding Results

After running experiments, check the final summary:

```
Final Test Summary
==================
Baseline (no impairment): ✅ PASSED
Loss 2% (LAN-like): ✅ PASSED
Loss 5% (WAN-like): ✅ PASSED
Delay 100ms (WAN delay): ✅ PASSED
```

Each scenario directory contains detailed metrics and analysis. Use the `.pcap` files with Wireshark or `tcpdump` for deep network analysis:

```bash
# View packet capture summary
tcpdump -r test_results/baseline_*/trace.pcap | head -20

# Analyze with Wireshark (GUI)
wireshark test_results/baseline_*/trace.pcap
```

### Requirements for Phase 2 Tests

- **Operating System:** Linux (required for `tc netem`)
- **Root Access:** Sudo privileges for network emulation
- **Tools:**
  - `tc` (traffic control) - part of `iproute2` package
  - `tcpdump` - packet capture utility
  - `psutil` (Python package) - system monitoring

Install required tools:
```bash
sudo apt-get install iproute2 tcpdump
pip3 install psutil
```

### Automated Clients

The experiments use `automated_client.py` instead of the GUI client. These automated clients:
- Connect to the server automatically
- Send periodic cell claim events to simulate real gameplay
- Collect detailed metrics (latency, position errors, etc.)
- Run headless (no GUI) for reproducible testing

## Troubleshooting

**Server won't start:**
- Check if port 9999 is already in use: `lsof -i :9999`
- Make sure no other server instances are running
- Verify Python version: `python3 --version`

**Clients can't connect:**
- Verify server is running first
- Check firewall settings
- Ensure you're using `127.0.0.1` (localhost)
- Check server logs for error messages

**Game feels laggy:**
- Check system resources (CPU, memory)
- Close other applications
- Check server CPU usage in `metrics.csv`

**Automated test fails:**
- Ensure you're running on Linux
- Verify `tcpdump` or `tshark` is installed
- Check that port 9999 is available
- Review `server.log` and `packet_capture.log` for errors
- Ensure no other processes are using the port

**Empty CSV files:**
- Verify clients are actually joining (check server logs)
- Ensure clients are receiving snapshots (check client logs)
- Check that the test ran for the full duration

**Phase 2 experiment errors:**
- Must run with `sudo` for network emulation
- Verify `tc` command is available: `which tc`
- Check that `lo` interface exists: `ip link show lo`
- Ensure no existing `tc` rules conflict: `sudo tc qdisc del dev lo root`
- Review netem_commands.txt in test output for applied rules


Demo Link: [Watch GridClash in Action](https://1drv.ms/v/c/a1fdbc4b4f6599b5/IQB3c8fO9n8zRIXCDWzx31U7AYgGtRJjScKNmIpj3uxGM-s?e=rthvNP)

