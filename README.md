# GridClash - Multiplayer Game State Synchronization

A real-time multiplayer game where up to 4 players compete to claim cells on an 8×8 grid. The first player to fill the entire grid wins.

## Table of Contents

1. [Game Overview](#game-overview)
2. [Project Structure](#project-structure)
3. [Build Instructions](#build-instructions)
4. [Quick Start](#quick-start)
5. [Design Architecture](#design-architecture)
6. [Protocol Overview](#protocol-overview)
7. [Running Tests](#running-tests)
8. [Phase 1 Testing](#phase-1-testing)
9. [Phase 2 Experiments](#phase-2-experiments)
10. [Understanding Results](#understanding-results)
11. [Troubleshooting](#troubleshooting)
12. [Demo Video](#demo-video)
13. [Summary](#summary)

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
├── automated_client.py    # Headless client for automated testing
├── protocol.py            # Network protocol definitions (GCP1.0)
├── run_baseline_test.py   # Phase 1 automated test runner
├── run_experiments.py     # Phase 2 comprehensive test suite
├── run_all_tests.sh       # Master test script (requires sudo)
├── test_results/          # Generated test outputs
└── README.md              # This file
```

## Build Instructions

### Prerequisites

**Required for all platforms:**
- **Python 3.7+** (check with `python3 --version`)
- **tkinter** (usually included with Python)
  - Ubuntu/Debian: `sudo apt-get install python3-tk`
  - macOS: Included with Python
  - Windows: Included with Python

**Required for automated testing:**
- **psutil** (installed automatically by test scripts)
- **Linux only**: Network emulation tools
  - `sudo apt-get install iproute2 tcpdump`
  - Root privileges for `tc netem` network emulation

### Installation Steps

1. **Clone or download the project:**
   ```bash
   # If using git
   git clone https://github.com/ucouldcallmeEL/MultiplayerGameStateSynchronization
   cd MultiplayerGameStateSynchronization
   
   # Or extract from archive
   unzip MultiplayerGameStateSynchronization.zip
   cd MultiplayerGameStateSynchronization
   ```

2. **Verify Python installation:**
   ```bash
   python3 --version  # Should be 3.7 or higher
   python3 -c "import tkinter"  # Should not error
   ```

3. **Install Python dependencies (optional - auto-installed by tests):**
   ```bash
   pip3 install psutil
   ```

4. **For Linux testing, install system tools:**
   ```bash
   sudo apt-get update
   sudo apt-get install iproute2 tcpdump python3-tk
   ```

5. **Make scripts executable (Linux/macOS):**
   ```bash
   chmod +x run_all_tests.sh
   chmod +x run_experiments.py
   chmod +x automated_client.py
   ```

### Verification

Test your installation:
```bash
# Test basic functionality
python3 -c "from protocol import build_init_message; print('Protocol OK')"
python3 -c "import tkinter; print('GUI OK')"

# Test server startup (Ctrl+C to stop)
python3 server.py
```

Expected output:
```
[SERVER] Initializing on 0.0.0.0:9999
[SERVER] Starting threads...
[SERVER] Server is running. Press Ctrl+C to stop.
```

## Quick Start

### Running the Game

1. **Start the server:**
   ```bash
   python3 server.py
   ```
   Expected output:
   ```
   [SERVER] Initializing on 0.0.0.0:9999
   [SERVER] Starting threads...
   [SERVER] Server is running. Press Ctrl+C to stop.
   ```

2. **Start clients** (in separate terminals):
   ```bash
   python3 client.py
   ```
   Repeat this for each player (up to 4).

3. **Play the game:**
   - Click "Join Game" in the client window
   - Wait for the server to assign you a player slot
   - Click on grid cells to claim them
   - First player to fill the entire grid wins
   - Game automatically resets after victory

### Expected Behavior

- **Server Console**: Shows player connections, event processing, and game state changes
- **Client Windows**: Display 8×8 grid with colored cells representing player ownership
- **Network Traffic**: ~40 packets/second per client (snapshots + acknowledgments)
- **Performance**: <10ms latency on localhost, <25% CPU usage

## Design Architecture

### Core Design Principles

**1. Server-Authoritative Architecture**
- Server maintains the single source of truth for game state
- All client actions are validated by the server
- Prevents cheating and ensures consistency across clients

**2. Hybrid Reliability Model**
- **Critical Events** (cell claims): Reliable delivery with retries and ACKs
- **State Synchronization**: Periodic snapshots with eventual consistency
- **Rationale**: Balances performance with reliability requirements

**3. Real-Time Performance**
- 40Hz server update rate (25ms intervals)
- UDP for low-latency communication
- Client-side interpolation for smooth visuals
- Separate network and rendering threads

### System Components

#### Server Architecture (`server.py`)
```
┌─────────────────┐    ┌─────────────────┐
│   Receive Loop  │    │ Broadcast Loop  │
│   (UDP Socket)  │    │   (40Hz Timer)  │
└─────────┬───────┘    └─────────┬───────┘
          │                      │
          └──────────┬───────────┘
    ┌─────────────────────────────────┐
    │        Game State               │
    │   (Thread-Safe with Locks)      │
    │  - 8×8 Grid                     │
    │  - Player Assignments           │
    │  - Event Tracking               │
    └─────────────────────────────────┘
```

**Key Features:**
- **Multi-threaded**: Separate threads for receiving and broadcasting
- **Thread-safe**: Mutex locks protect shared game state
- **Event Arbitration**: Timestamp-based conflict resolution
- **Duplicate Detection**: Tracks last event ID per player
- **Metrics Collection**: CSV logging for performance analysis

#### Client Architecture (`client.py`)
```
┌─────────────────┐    ┌─────────────────┐
│  Network Loop   │    │   Render Loop   │
│   (10ms poll)   │    │   (16ms/60fps)  │
└─────────┬───────┘    └─────────┬───────┘
          │                      │
          └──────────┬───────────┘
    ┌─────────────────────────────────┐
    │     Logical Grid State          │
    │  ┌─────────────────────────────┐│
    │  │    Visual Grid State        ││
    │  │   (Interpolated Colors)     ││
    │  └─────────────────────────────┘│
    └─────────────────────────────────┘
```

**Key Features:**
- **Dual-State Model**: Logical state (from server) + Visual state (interpolated)
- **Event Reliability**: Retry mechanism for critical actions
- **Visual Interpolation**: Smooth color transitions between updates
- **Latency Measurement**: Round-trip time tracking via ACKs

### Design Rationale

**Why UDP over TCP?**
- **Lower Latency**: No connection overhead or acknowledgment delays
- **Real-time Suitability**: Newer data is more valuable than old data
- **Simplicity**: Direct control over reliability mechanisms
- **Performance**: Handles 40Hz updates efficiently

**Why Periodic Snapshots?**
- **Self-Correcting**: Automatically recovers from packet loss
- **Stateless**: No complex state tracking required
- **Scalable**: Same mechanism works for 1 or 100 clients
- **Debuggable**: Easy to analyze and verify correctness

**Why Hybrid Reliability?**
- **Events Need Reliability**: Cell claims must be processed exactly once
- **State Needs Freshness**: Latest game state is more important than old state
- **Performance Balance**: Avoids overhead of making everything reliable
- **Real-world Applicability**: Common pattern in networked games

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

## Running Tests

### Overview

The project includes comprehensive testing for all three phases:

- **Phase 1**: Basic functionality and baseline performance
- **Phase 2**: Network impairment scenarios (packet loss, delay)
- **Phase 3**: Full validation and reproducibility

### Test Types

1. **Manual Testing**: Interactive gameplay with GUI clients
2. **Automated Baseline**: Phase 1 acceptance criteria validation
3. **Network Emulation**: Phase 2 impairment scenarios using `tc netem`
4. **Comprehensive Suite**: All scenarios with detailed analysis

## Phase 1 Testing

### Quick Baseline Test

**Purpose**: Validate core functionality and performance under ideal conditions.

**Command**:
```bash
python3 run_baseline_test.py
```

**What it does**:
1. Checks and installs Python dependencies (`psutil`)
2. Verifies packet capture tools (`tcpdump` or `tshark`)
3. Starts server and 4 automated clients
4. Runs for 60 seconds collecting metrics
5. Analyzes results against acceptance criteria
6. Generates comprehensive output

**Expected Output**:
```
============================================================
Baseline Local Test - Automated Runner
============================================================
[SETUP] Checking Python dependencies...
[SETUP] ✓ psutil present
[SETUP] Output dir: test_results/baseline_2024-01-15_14-30-25
[TEST] Running for 60s...

============================================================
RESULTS ANALYSIS
============================================================
Client 12345: 40.12 updates/sec (2407 samples)
Client 12346: 39.98 updates/sec (2399 samples)
Client 12347: 40.05 updates/sec (2403 samples)
Client 12348: 40.01 updates/sec (2401 samples)

Average latency: 6.23 ms
Average CPU: 18.45%

✓ Update rate ≥ 20/sec per client
✓ Average latency ≤ 50 ms
✓ Average CPU < 60%

OVERALL: ✓ PASS

============================================================
Done. Results in: test_results/baseline_2024-01-15_14-30-25
```

**Generated Files**:
```
test_results/baseline_YYYY-MM-DD_HH-MM-SS/
├── metrics.csv              # Server performance metrics
├── client_*_metrics.csv     # Client-side latency measurements
├── baseline_test.pcap       # Complete packet capture
├── server.log               # Server console output
├── client_*.log             # Client console outputs
└── packet_capture.log       # Packet capture tool output
```

### Acceptance Criteria (Phase 1)

| Metric | Target | Typical Result | Status |
|--------|--------|----------------|--------|
| Update Rate | ≥20 Hz/client | 40.0 Hz | ✓ PASS |
| Average Latency | ≤50ms | 5-8ms | ✓ PASS |
| Server CPU Usage | <60% | 15-25% | ✓ PASS |

### Analyzing Phase 1 Results

**CSV File Structure**:

`metrics.csv` (server-side):
```csv
server_timestamp_ms,client_id,snapshot_id,seq_num,cpu_percent,recv_time_ms,latency_ms
1705320625801,1,0,0,18.5,,
1705320625801,2,0,0,18.5,,
1705320625826,1,1,1,18.7,,
1705320625829,,1,,,,3
```

`client_*_metrics.csv` (client-side):
```csv
snapshot_id,server_timestamp_ms,recv_time_ms,latency_ms,position_error,cell_owner,expected_owner
1,1705320625826,1705320625829,3,0.0,,
2,1705320625851,1705320625854,3,0.0,,
```

**Manual Analysis**:
```bash
# Count total packets
tcpdump -r baseline_test.pcap | wc -l

# Filter by message type (requires understanding header structure)
tshark -r baseline_test.pcap -T fields -e udp.port
```

## Phase 2 Experiments

### Comprehensive Network Impairment Testing

**Purpose**: Validate system behavior under realistic network conditions using Linux traffic control.

**Requirements**:
- Linux operating system (required for `tc netem`)
- Root privileges (`sudo`)
- Network emulation tools installed

### Running All Phase 2 Tests

**Master Command**:
```bash
sudo ./run_all_tests.sh
```

**What it does**:
1. Checks root privileges and installs dependencies
2. Runs all 4 test scenarios sequentially
3. Applies network impairments using `tc netem`
4. Collects comprehensive evidence (logs, pcaps, metrics)
5. Analyzes results against acceptance criteria
6. Generates detailed reports

**Expected Output**:
```
=========================================================
 Setting up Environment...
=========================================================
✓ Environment Ready.
=========================================================
 Starting Python Experiment Runner...
=========================================================

############################################################
 STARTING Phase 2 SCENARIO: Baseline (no impairment)
############################################################
[NETEM] No impairment (Baseline).
[OUTPUT] Results will be saved to: test_results/baseline_2024-01-15_15-45-30
[PCAP] Starting packet capture on lo...
[SERVER] Starting server...
[CLIENTS] Starting 4 automated clients...
[RUN] Running test for 60 seconds...
[RUN] Clients are actively playing the game (sending click events)...
[RUN] 10/60 seconds elapsed...
[RUN] 20/60 seconds elapsed...
...
[ANALYSIS] Results saved to analysis_results.txt
[RESULT] Scenario ✓ PASSED

############################################################
 STARTING Phase 2 SCENARIO: Loss 2% (LAN-like)
############################################################
[NETEM] Applying: sudo tc qdisc add dev lo root netem loss 2%
...

============================================================
 Final Test Summary
============================================================
  Baseline (no impairment): ✓ PASSED
  Loss 2% (LAN-like): ✓ PASSED
  Loss 5% (WAN-like): ✓ PASSED
  Delay 100ms (WAN delay): ✓ PASSED
============================================================

[DONE] All tests completed. Results stored in: test_results/
```

### Experiment Configuration

Key parameters in `run_experiments.py`:

```python
TEST_DURATION = 60        # Seconds per scenario
NUM_CLIENTS = 4          # Number of automated clients
INTERFACE = "lo"         # Network interface (use 'lo' for localhost)
SERVER_PORT = 9999       # Server port
```

### Individual Test Scenarios

#### 1. Baseline (No Impairment)

**Purpose**: Establish performance baseline
**Network**: No impairment
**Acceptance Criteria**:
- ≥20 updates/sec per client
- ≤50ms average latency
- <60% server CPU usage

#### 2. Loss 2% (LAN-like Conditions)
**Network Command**: `sudo tc qdisc add dev lo root netem loss 2%`
**Purpose**: Test interpolation under minor packet loss
**Acceptance Criteria**:
- Mean position error ≤0.5 units
- 95th percentile error ≤1.5 units
- Graceful interpolation implemented

#### 3. Loss 5% (WAN-like Conditions)
**Network Command**: `sudo tc qdisc add dev lo root netem loss 5%`
**Purpose**: Test critical event reliability
**Acceptance Criteria**:
- Critical events reliability ≥99%
- Critical events delivered within 200ms
- System remains stable

#### 4. Delay 100ms (WAN Delay)
**Network Command**: `sudo tc qdisc add dev lo root netem delay 100ms`
**Purpose**: Test high-latency functionality
**Acceptance Criteria**:
- Game remains functional
- Clients continue receiving updates

### Generated Evidence Files

Each scenario creates a timestamped directory:
```
test_results/[scenario]_YYYY-MM-DD_HH-MM-SS/
├── trace.pcap               # Complete packet capture
├── server.log               # Server console output
├── client_1.log             # Client console outputs
├── client_2.log
├── client_3.log
├── client_4.log
├── metrics.csv              # Server performance data
├── client_*_metrics.csv     # Client latency/error data
├── netem_commands.txt       # Applied network commands
├── evidence_summary.txt     # Test overview
└── analysis_results.txt     # Acceptance criteria validation
```

## Understanding Results

### Phase 2 Results Analysis

**Metrics Analysis**:
```bash
# View test summary
cat test_results/loss_2pct_*/analysis_results.txt

# Analyze packet capture
tcpdump -r test_results/loss_2pct_*/trace.pcap | head -20
wireshark test_results/loss_2pct_*/trace.pcap  # GUI analysis

# Check applied network conditions
cat test_results/loss_2pct_*/netem_commands.txt
```

### Advanced Analysis

**Custom PCAP Analysis**:
```bash
# Protocol-specific analysis
python3 clean_pcap_analyzer.py test_results/*/trace.pcap
```

### Automated Clients for Testing

Phase 2 tests use `automated_client.py` instead of GUI clients:

**Features**:
- Headless operation (no GUI)
- Automatic game joining
- Simulated player behavior (random cell clicks)
- Detailed metrics collection
- Position error tracking for interpolation validation
- Configurable test duration

**Usage**:
```bash
# Manual automated client testing
python3 automated_client.py --server 127.0.0.1:9999 --id 1

# With custom duration
TEST_DURATION=30 python3 automated_client.py
```

## Troubleshooting

### Common Issues and Solutions

**Server won't start:**
```bash
# Check if port 9999 is already in use
lsof -i :9999

# Kill existing processes
pkill -f "server.py"

# Verify Python version
python3 --version  # Should be 3.7+
```

**Clients can't connect:**
- Verify server is running first
- Check firewall settings (allow port 9999)
- Ensure you're using `127.0.0.1` (localhost)
- Check server logs for error messages

**Game feels laggy:**
- Check system resources: `top` or `htop`
- Close other applications
- Check server CPU usage in `metrics.csv`
- Verify network interface: `ping 127.0.0.1`

**Automated test fails:**
```bash
# Verify packet capture tools
which tcpdump || which tshark

# Check port availability
lsof -i :9999

# Review logs for errors
tail -f test_results/*/server.log
```

**Empty CSV files:**
- Verify clients are joining (check server logs)
- Ensure clients receive snapshots (check client logs)
- Confirm test ran for full duration
- Check file permissions in test_results/

**Phase 2 experiment errors:**
```bash
# Must run with sudo for network emulation
sudo ./run_all_tests.sh

# Verify tc command availability
which tc

# Check network interface exists
ip link show lo

# Clean existing traffic control rules
sudo tc qdisc del dev lo root

# Review applied network commands
cat test_results/*/netem_commands.txt
```

**Permission Issues:**
```bash
# Make scripts executable
chmod +x run_all_tests.sh run_experiments.py

# Fix Python module permissions
chmod 644 *.py

# Ensure test results directory is writable
chmod 755 test_results
```

**Python Import Errors:**
```bash
# Install missing dependencies
pip3 install psutil

# Verify tkinter installation
python3 -c "import tkinter; print('OK')"

# For Ubuntu/Debian
sudo apt-get install python3-tk
```


## Demo Video

**Demo Link**: [Watch GridClash in Action](https://1drv.ms/v/c/a1fdbc4b4f6599b5/IQB3c8fO9n8zRIXCDWzx31U7AYgGtRJjScKNmIpj3uxGM-s?e=rthvNP)

The demo video showcases:
- Real-time multiplayer gameplay
- Server-client synchronization
- Network protocol in action
- Complete test suite execution

---

## Summary

GridClash demonstrates a complete real-time multiplayer game implementation with:

✓ **Custom UDP Protocol**: Efficient binary protocol (GCP1.0) with 28-byte headers
✓ **Hybrid Reliability**: Critical events use retries, state uses periodic snapshots
✓ **Real-time Performance**: 40Hz updates with <10ms latency
✓ **Network Resilience**: Handles packet loss, reordering, and high latency
✓ **Comprehensive Testing**: Automated test suite with network emulation
✓ **Evidence Collection**: Detailed packet captures and performance metrics

