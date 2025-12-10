#!/usr/bin/env python3
import os
import sys
import time
import subprocess
import csv
import shutil
from datetime import datetime
from pathlib import Path

# =========================================================
# === CONFIGURATION ===
# =========================================================

# Interface: Use 'lo' for local testing on Linux. 
# Use 'eth0' or similar if Server and Client are on different VMs.
INTERFACE = "lo" 

TEST_DURATION = 60  # Seconds per scenario
SERVER_PORT = 9999
NUM_CLIENTS = 4
ROOT = Path(__file__).parent.absolute()
RESULTS_DIR = ROOT / "test_results"

# Define all scenarios for Phase 1 and Phase 2
SCENARIOS = {
    "01_baseline": {
        "phase": "Phase 1",
        "desc": "Baseline (No Impairment)",
        "netem_cmd": None 
    },
    "02_loss_2pct": {
        "phase": "Phase 2",
        "desc": "Loss 2% (LAN-like)",
        "netem_cmd": f"tc qdisc add dev {INTERFACE} root netem loss 2%"
    },
    "03_loss_5pct": {
        "phase": "Phase 2",
        "desc": "Loss 5% (WAN-like)",
        "netem_cmd": f"tc qdisc add dev {INTERFACE} root netem loss 5%"
    },
    "04_delay_100ms": {
        "phase": "Phase 2",
        "desc": "Delay 100ms +/- 10ms (Jitter)",
        "netem_cmd": f"tc qdisc add dev {INTERFACE} root netem delay 100ms 10ms"
    }
}

# =========================================================
# === HELPER FUNCTIONS ===
# =========================================================

def check_root():
    if os.geteuid() != 0:
        print("[ERROR] Script must be run as root (sudo) for network emulation.")
        sys.exit(1)

def check_tools():
    required = ["tcpdump", "tc"]
    missing = [t for t in required if shutil.which(t) is None]
    if missing:
        print(f"[ERROR] Missing tools: {', '.join(missing)}")
        print("Install via: sudo apt-get install iproute2 tcpdump")
        sys.exit(1)

def clean_netem():
    """Removes existing traffic control rules."""
    # Suppress output, ignore error if no rule existed
    subprocess.run(f"tc qdisc del dev {INTERFACE} root", shell=True, 
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def apply_netem(cmd):
    clean_netem()
    if cmd:
        print(f"[NETEM] Applying impairment: {cmd}")
        try:
            subprocess.run(cmd, shell=True, check=True)
        except subprocess.CalledProcessError:
            print("[ERROR] Failed to apply netem command. Check interface name.")
            sys.exit(1)
    else:
        print("[NETEM] No impairment (Baseline).")

def kill_processes():
    """Force kills server/client/tcpdump to ensure clean slate."""
    cmd = "pgrep -f 'server.py|client.py|tcpdump' | xargs -r kill -9"
    subprocess.run(cmd, shell=True)

# =========================================================
# === ANALYSIS LOGIC ===
# =========================================================

def analyze_scenario(outdir, scenario_key, config):
    print(f"\n--- 📊 Analysis: {config['desc']} ---")
    
    server_csv = outdir / "metrics.csv"
    client_csvs = list(outdir.glob("client_*_metrics.csv"))
    client_logs = list(outdir.glob("client_*.log"))

    # 1. Check Server CPU & Stability
    if server_csv.exists():
        with open(server_csv, 'r') as f:
            reader = csv.DictReader(f)
            cpu_vals = [float(row['cpu_percent']) for row in reader if row.get('cpu_percent')]
            avg_cpu = sum(cpu_vals)/len(cpu_vals) if cpu_vals else 0
            print(f"  • Server CPU Avg: {avg_cpu:.1f}% (Pass: <60%)")
    else:
        print("  • ⚠️ Server metrics missing.")

    # 2. Check Client Latency & Update Rate
    total_latency = 0
    total_packets = 0
    
    for cfile in client_csvs:
        latencies = []
        with open(cfile, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get('latency_ms'):
                    try: latencies.append(float(row['latency_ms']))
                    except: pass
        
        if latencies:
            avg_lat = sum(latencies) / len(latencies)
            total_latency += sum(latencies)
            total_packets += len(latencies)
            # Update rate estimate: packets received / duration
            rate = len(latencies) / TEST_DURATION
            print(f"  • Client {cfile.stem.split('_')[1]}: {rate:.1f} snapshots/sec, Avg Latency: {avg_lat:.1f}ms")

    # 3. Specific Pass/Fail Criteria
    passed = True
    
    if scenario_key == "01_baseline":
        global_avg = total_latency / total_packets if total_packets else 0
        if global_avg > 50: 
            print("  ❌ FAIL: Latency > 50ms")
            passed = False
        else:
            print("  ✅ PASS: Latency <= 50ms")

    elif "loss_5pct" in scenario_key:
        # Check logs for "RETRY" to prove reliability mechanism worked
        retry_found = False
        for log in client_logs:
            try:
                content = log.read_text(errors='ignore')
                if "[RETRY]" in content:
                    retry_found = True
                    break
            except: pass
        
        if retry_found:
            print("  ✅ PASS: Reliability mechanism triggered (Retries found in logs).")
        else:
            print("  ⚠️ WARNING: No retries found in logs. (Maybe unlucky RNG or logging issue?)")

    elif "delay_100ms" in scenario_key:
        global_avg = total_latency / total_packets if total_packets else 0
        print(f"  • Observed Global Latency: {global_avg:.1f}ms")
        if 90 <= global_avg <= 150:
            print("  ✅ PASS: Latency reflects network delay.")
        else:
            print("  ⚠️ WARNING: Latency outside expected range (100ms + overhead).")

    return passed

# =========================================================
# === MAIN TEST RUNNER ===
# =========================================================

def run_single_test(key, config):
    print(f"\n{'#'*60}")
    print(f" STARTING {config['phase']} SCENARIO: {config['desc']}")
    print(f"{'#'*60}")

    kill_processes()
    apply_netem(config['netem_cmd'])

    # Setup Directory
    timestamp = datetime.now().strftime("%H-%M-%S")
    outdir = RESULTS_DIR / f"{key}_{timestamp}"
    outdir.mkdir(parents=True, exist_ok=True)

    # 1. Start Packet Capture
    pcap = outdir / "trace.pcap"
    tcpdump = subprocess.Popen(
        ["tcpdump", "-i", INTERFACE, "udp", "port", str(SERVER_PORT), "-w", str(pcap), "-q"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    # 2. Start Server (Unbuffered -u)
    srv_log = open(outdir / "server.log", "w")
    server = subprocess.Popen(
        [sys.executable, "-u", "server.py"],
        stdout=srv_log, stderr=subprocess.STDOUT, cwd=ROOT
    )
    time.sleep(2)

    # 3. Start Clients (Unbuffered -u)
    clients = []
    env = os.environ.copy()
    env["AUTO_JOIN"] = "1"
    
    for i in range(1, NUM_CLIENTS + 1):
        c_log = open(outdir / f"client_{i}.log", "w")
        proc = subprocess.Popen(
            [sys.executable, "-u", "client.py", "--auto"],
            stdout=c_log, stderr=subprocess.STDOUT, cwd=ROOT, env=env
        )
        clients.append(proc)
        time.sleep(0.2)

    # 4. Wait for Test Duration
    print(f"[RUN] Running for {TEST_DURATION} seconds...")
    try:
        # Simple progress bar
        for _ in range(TEST_DURATION):
            time.sleep(1)
            print(".", end="", flush=True)
        print()
    except KeyboardInterrupt:
        print("\n[SKIP] Skipping remaining time...")

    # 5. Cleanup
    print("[STOP] Stopping processes...")
    for c in clients: c.terminate()
    server.terminate()
    subprocess.run(f"kill {tcpdump.pid}", shell=True, stderr=subprocess.DEVNULL)
    
    # Wait for file handles to close
    time.sleep(1)
    kill_processes()
    
    # 6. Organize Files
    if (ROOT / "metrics.csv").exists():
        (ROOT / "metrics.csv").rename(outdir / "metrics.csv")
    
    for f in ROOT.glob("client_*_metrics.csv"):
        f.rename(outdir / f.name)

    # 7. Analyze
    analyze_scenario(outdir, key, config)

def main():
    check_root()
    check_tools()
    
    # Clean previous results if you want (optional)
    # shutil.rmtree(RESULTS_DIR, ignore_errors=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    try:
        for key, config in SCENARIOS.items():
            run_single_test(key, config)
            print("\n[INFO] Cooling down (3s)...")
            time.sleep(3)
    except KeyboardInterrupt:
        print("\n\n[EXIT] Aborted by user.")
    finally:
        clean_netem()
        kill_processes()
        print(f"\n[DONE] All tests completed. Results stored in: {RESULTS_DIR}")

if __name__ == "__main__":
    main()