#!/usr/bin/env python3
import os
import sys
import time
import signal
import subprocess
import platform
from datetime import datetime
from pathlib import Path

TEST_DURATION = 60
SERVER_PORT = 9999
NUM_CLIENTS = 4
ROOT = Path(__file__).parent.absolute()


def check_and_install_deps():
    print("[SETUP] Checking Python dependencies...")
    missing = []
    for mod in ("psutil",):
        try:
            __import__(mod)
            print(f"[SETUP] ✓ {mod} present")
        except Exception:
            missing.append(mod)
            print(f"[SETUP] ✗ {mod} missing")
    if missing:
        print(f"[SETUP] Installing: {', '.join(missing)}")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "--user", *missing])
            print("[SETUP] ✓ Installation complete")
        except Exception as e:
            print(f"[ERROR] Failed to install deps: {e}")
            sys.exit(1)


def check_tools():
    for tool in ("tcpdump", "tshark"):
        if subprocess.run(["which", tool], capture_output=True).returncode == 0:
            return tool
    print("[ERROR] Neither tcpdump nor tshark is available. Install one.")
    sys.exit(1)


def kill_old():
    for name in ("server.py", "client.py"):
        out = subprocess.run(["pgrep", "-f", name], capture_output=True, text=True)
        if out.returncode == 0 and out.stdout.strip():
            for pid in out.stdout.strip().split("\n"):
                try:
                    os.kill(int(pid), signal.SIGTERM)
                except Exception:
                    pass
    time.sleep(1)


def main():
    print("=" * 60)
    print("Baseline Local Test - Automated Runner")
    print("=" * 60)

    check_and_install_deps()
    kill_old()

    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    outdir = ROOT / "test_results" / f"baseline_{ts}"
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"[SETUP] Output dir: {outdir}")

    tool = check_tools()
    pcap = outdir / "baseline_test.pcap"
    pc_log = outdir / "packet_capture.log"

    if tool == "tcpdump":
        cmd = ["tcpdump", "-i", "lo0" if platform.system()=="Darwin" else "lo",
               "-w", str(pcap), f"udp port {SERVER_PORT}", "-q"]
    else:
        cmd = ["tshark", "-i", "lo0" if platform.system()=="Darwin" else "lo",
               "-w", str(pcap), "-f", f"udp port {SERVER_PORT}", "-q"]

    with open(pc_log, "w") as lf:
        cap = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=lf)

    # Start server
    srv_log = open(outdir / "server.log", "w")
    server = subprocess.Popen([sys.executable, str(ROOT / "server.py")], stdout=srv_log, stderr=subprocess.STDOUT, cwd=ROOT)
    time.sleep(2)

    # Start clients (auto mode)
    clients = []
    for i in range(1, NUM_CLIENTS+1):
        c_log = open(outdir / f"client_{i}.log", "w")
        env = os.environ.copy()
        env["AUTO_JOIN"] = "1"
        client = subprocess.Popen([sys.executable, str(ROOT / "client.py"), "--auto"], stdout=c_log, stderr=subprocess.STDOUT, cwd=ROOT, env=env)
        clients.append((client, c_log))
        time.sleep(0.3)

    print(f"[TEST] Running for {TEST_DURATION}s...")
    time.sleep(TEST_DURATION)

    # Teardown
    for proc, _ in clients:
        try:
            proc.terminate()
        except Exception:
            pass
    try:
        server.terminate()
    except Exception:
        pass
    try:
        cap.terminate()
    except Exception:
        pass
    time.sleep(2)

    # Collect metrics.csv and client CSVs
    # Move server metrics if in root
    srv_csv = ROOT / "metrics.csv"
    if srv_csv.exists():
        srv_csv.rename(outdir / "metrics.csv")

    for csv_file in ROOT.glob("client_*_metrics.csv"):
        csv_file.rename(outdir / csv_file.name)

    # Analyze results
    print("\n" + "=" * 60)
    print("RESULTS ANALYSIS")
    print("=" * 60)
    metrics = outdir / "metrics.csv"
    if not metrics.exists():
        print("❌ metrics.csv not found - cannot analyze")
    else:
        import csv as _csv
        per_client = {}
        latencies = []
        cpu_vals = []
        with open(metrics, 'r') as f:
            reader = _csv.DictReader(f)
            for row in reader:
                # snapshot send rows
                if row['client_id'] and row['snapshot_id'] and row['server_timestamp_ms']:
                    cid = row['client_id']
                    per_client.setdefault(cid, []).append(int(row['server_timestamp_ms']))
                    if row['cpu_percent']:
                        try:
                            cpu_vals.append(float(row['cpu_percent']))
                        except Exception:
                            pass
                # ACK rows
                if row['latency_ms']:
                    try:
                        lat = float(row['latency_ms'])
                        if lat >= 0:
                            latencies.append(lat)
                    except Exception:
                        pass

        # Compute update rates
        rates = []
        for cid, ts_list in per_client.items():
            ts_list.sort()
            if len(ts_list) >= 2:
                duration = (ts_list[-1] - ts_list[0]) / 1000.0
                rate = len(ts_list) / duration if duration > 0 else 0
                rates.append(rate)
                print(f"Client {cid}: {rate:.2f} updates/sec ({len(ts_list)} samples)")

        avg_rate = min(rates) if rates else 0.0
        avg_latency = (sum(latencies)/len(latencies)) if latencies else 9999.0
        avg_cpu = (sum(cpu_vals)/len(cpu_vals)) if cpu_vals else 0.0

        print(f"\nAverage latency: {avg_latency:.2f} ms")
        print(f"Average CPU: {avg_cpu:.2f}%")

        # Acceptance criteria
        rate_ok = (len(rates) >= 1) and min(rates) >= 20.0
        latency_ok = avg_latency <= 50.0
        cpu_ok = avg_cpu < 60.0

        print("\n" + ("✅" if rate_ok else "❌"), "Update rate ≥ 20/sec per client")
        print(("✅" if latency_ok else "❌"), "Average latency ≤ 50 ms")
        print(("✅" if cpu_ok else "❌"), "Average CPU < 60%")

        overall = rate_ok and latency_ok and cpu_ok
        print("\nOVERALL:", "✅ PASS" if overall else "❌ FAIL")

    print("\n" + "=" * 60)
    print("Done. Results in:", outdir)
    return 0


if __name__ == "__main__":
    sys.exit(main())


