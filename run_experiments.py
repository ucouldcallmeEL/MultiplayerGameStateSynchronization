#!/usr/bin/env python3
import os
import sys
import time
import subprocess
import signal
from pathlib import Path

# === CONFIGURATION ===
INTERFACE = "lo" # Use 'lo' for local testing on Linux, or 'eth0' if across VMs
SERVER_PORT = 9999
NUM_CLIENTS = 4
TEST_DURATION = 60 # Seconds per scenario

ROOT = Path(__file__).parent.absolute()

SCENARIOS = {
    "baseline": {
        "desc": "Baseline (No Impairment)",
        "netem": None 
    },
    "loss_2": {
        "desc": "LAN Loss (2%)",
        "netem": f"sudo tc qdisc add dev {INTERFACE} root netem loss 2%"
    },
    "loss_5": {
        "desc": "WAN Loss (5%)",
        "netem": f"sudo tc qdisc add dev {INTERFACE} root netem loss 5%"
    },
    "delay_100": {
        "desc": "WAN Delay (100ms)",
        "netem": f"sudo tc qdisc add dev {INTERFACE} root netem delay 100ms 10ms"
    }
}

def clean_netem():
    """Removes any existing Traffic Control rules."""
    print(f"[NETEM] Cleaning rules on {INTERFACE}...")
    subprocess.run(f"sudo tc qdisc del dev {INTERFACE} root", shell=True, stderr=subprocess.DEVNULL)

def apply_netem(command):
    if command:
        print(f"[NETEM] Applying: {command}")
        subprocess.run(command, shell=True, check=True)

def run_scenario(scenario_name, config):
    print(f"\n{'='*60}")
    print(f"STARTING SCENARIO: {config['desc']}")
    print(f"{'='*60}")

    # 1. Setup Output Directory
    outdir = ROOT / "results" / scenario_name
    outdir.mkdir(parents=True, exist_ok=True)

    # 2. Network Setup
    clean_netem()
    try:
        apply_netem(config['netem'])
    except Exception as e:
        print(f"[ERROR] Failed to apply netem: {e}")
        return

    # 3. Start Packet Capture
    pcap_file = outdir / "trace.pcap"
    # Using tcpdump (needs sudo usually, or capable user)
    # capturing strictly UDP port 9999
    tcpdump_cmd = ["sudo", "tcpdump", "-i", INTERFACE, "udp", "port", str(SERVER_PORT), "-w", str(pcap_file), "-q"]
    pcap_proc = subprocess.Popen(tcpdump_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    # 4. Start Server
    print("[EXEC] Starting Server...")
    srv_log = open(outdir / "server.log", "w")
    # server_proc = subprocess.Popen([sys.executable, "server.py"], stdout=srv_log, stderr=subprocess.STDOUT, cwd=ROOT)
    server_proc = subprocess.Popen([sys.executable, "-u", "server.py"], stdout=srv_log, stderr=subprocess.STDOUT, cwd=ROOT)
    time.sleep(1) # Let server bind

    # 5. Start Clients
    print(f"[EXEC] Starting {NUM_CLIENTS} Clients...")
    client_procs = []
    env = os.environ.copy()
    env["AUTO_JOIN"] = "1" # Ensure your client.py reads this env var
    
    for i in range(1, NUM_CLIENTS + 1):
        c_log = open(outdir / f"client_{i}.log", "w")
        # Clients need to run in background
        proc = subprocess.Popen([sys.executable, "-u", "client.py", "--auto"], 
                                stdout=c_log, stderr=subprocess.STDOUT, cwd=ROOT, env=env)
        client_procs.append(proc)
        time.sleep(0.2)

    # 6. Run Test Duration
    print(f"[TEST] Running for {TEST_DURATION} seconds...")
    try:
        # Create a progress bar
        for _ in range(TEST_DURATION):
            time.sleep(1)
            print(".", end="", flush=True)
    except KeyboardInterrupt:
        print("\n[STOP] Interrupted by user.")

    print("\n[STOP] Stopping processes...")

    # 7. Teardown
    for p in client_procs: p.terminate()
    server_proc.terminate()
    server_proc.wait()
    
    # Stop tcpdump
    subprocess.run(["sudo", "kill", str(pcap_proc.pid)], stderr=subprocess.DEVNULL)
    pcap_proc.wait()

    clean_netem()

    # 8. Move Metrics
    # Assumes server.py produces 'metrics.csv' in CWD
    if (ROOT / "metrics.csv").exists():
        (ROOT / "metrics.csv").rename(outdir / "server_metrics.csv")
    
    # Move Client CSVs
    for f in ROOT.glob("client_*_metrics.csv"):
        f.rename(outdir / f.name)

    print(f"[DONE] Results saved to {outdir}")

if __name__ == "__main__":
    # Ensure we are root for netem
    if os.geteuid() != 0:
        print("Error: This script must be run as root (sudo) to control network traffic.")
        sys.exit(1)

    # Run all scenarios
    for key, conf in SCENARIOS.items():
        run_scenario(key, conf)
        time.sleep(2) # Cooldown