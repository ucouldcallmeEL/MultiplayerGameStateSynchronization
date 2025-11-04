#!/usr/bin/env python3
"""
Automated Baseline Local Test Script
-------------------------------------
This script automates the baseline test scenario for Project 2: Multiplayer Game State Synchronization.

It performs:
1. Cleanup of existing processes
2. Creates output directory with timestamp
3. Starts packet capture on loopback interface
4. Launches server and 4 clients
5. Runs test for 60 seconds
6. Gracefully terminates all processes
7. Verifies required output files exist
"""

import os
import sys
import time
import signal
import subprocess
import platform
import glob
import shutil
from datetime import datetime
from pathlib import Path


# Configuration
TEST_DURATION = 60  # seconds
SERVER_PORT = 9999
NUM_CLIENTS = 4
SCRIPT_DIR = Path(__file__).parent.absolute()
SERVER_SCRIPT = SCRIPT_DIR / "server.py"
CLIENT_SCRIPT = SCRIPT_DIR / "client.py"


def check_and_install_dependencies():
    """Check for required Python packages and install if missing."""
    print("[SETUP] Checking Python dependencies...")
    
    required_packages = {
        'psutil': 'psutil',  # For CPU monitoring
    }
    
    missing_packages = []
    
    for module_name, package_name in required_packages.items():
        try:
            __import__(module_name)
            print(f"[SETUP] ✓ {module_name} is installed")
        except ImportError:
            missing_packages.append(package_name)
            print(f"[SETUP] ✗ {module_name} is missing")
    
    if missing_packages:
        print(f"[SETUP] Installing missing packages: {', '.join(missing_packages)}...")
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "--user"] + missing_packages,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE
            )
            print("[SETUP] ✓ Dependencies installed successfully")
        except subprocess.CalledProcessError as e:
            print(f"[ERROR] Failed to install dependencies: {e}")
            print("[ERROR] Please install manually: pip install psutil")
            sys.exit(1)
    
    return True


def check_system_tools():
    """Check for required system tools (tcpdump/tshark)."""
    print("[SETUP] Checking for packet capture tools...")
    
    tcpdump_available = subprocess.run(
        ["which", "tcpdump"],
        capture_output=True
    ).returncode == 0
    
    tshark_available = subprocess.run(
        ["which", "tshark"],
        capture_output=True
    ).returncode == 0
    
    if tcpdump_available:
        print("[SETUP] ✓ tcpdump is available")
        return True
    elif tshark_available:
        print("[SETUP] ✓ tshark is available")
        return True
    else:
        print("[ERROR] Neither tcpdump nor tshark is available.")
        print("[ERROR] Please install one of them:")
        print("  - macOS: brew install tcpdump")
        print("  - Linux: sudo apt-get install tcpdump")
        return False


class TestRunner:
    def __init__(self):
        self.processes = []
        self.output_dir = None
        self.pcap_file = None
        self.csv_file = None
        
    def find_loopback_interface(self):
        """Find the loopback interface name for the current OS."""
        system = platform.system()
        if system == "Darwin":  # macOS
            return "lo0"
        elif system == "Linux":
            return "lo"
        else:
            print(f"[WARN] Unknown OS: {system}. Defaulting to 'lo'")
            return "lo"
    
    def kill_existing_processes(self):
        """Kill any existing server or client processes."""
        print("[SETUP] Checking for existing processes...")
        
        # Find processes running server.py or client.py
        try:
            if platform.system() == "Darwin":
                # macOS
                server_procs = subprocess.run(
                    ["pgrep", "-f", "server.py"],
                    capture_output=True,
                    text=True
                )
                client_procs = subprocess.run(
                    ["pgrep", "-f", "client.py"],
                    capture_output=True,
                    text=True
                )
            else:
                # Linux
                server_procs = subprocess.run(
                    ["pgrep", "-f", "server.py"],
                    capture_output=True,
                    text=True
                )
                client_procs = subprocess.run(
                    ["pgrep", "-f", "client.py"],
                    capture_output=True,
                    text=True
                )
            
            # Kill server processes
            if server_procs.stdout.strip():
                pids = server_procs.stdout.strip().split('\n')
                for pid in pids:
                    if pid:
                        try:
                            os.kill(int(pid), signal.SIGTERM)
                            print(f"[SETUP] Killed existing server process (PID: {pid})")
                        except ProcessLookupError:
                            pass
                        except ValueError:
                            pass
            
            # Kill client processes
            if client_procs.stdout.strip():
                pids = client_procs.stdout.strip().split('\n')
                for pid in pids:
                    if pid:
                        try:
                            os.kill(int(pid), signal.SIGTERM)
                            print(f"[SETUP] Killed existing client process (PID: {pid})")
                        except ProcessLookupError:
                            pass
                        except ValueError:
                            pass
            
            # Give processes time to terminate
            time.sleep(1)
            
        except Exception as e:
            print(f"[WARN] Error checking for existing processes: {e}")
    
    def create_output_directory(self):
        """Create a timestamped output directory for test results."""
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.output_dir = SCRIPT_DIR / "test_results" / f"baseline_{timestamp}"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        print(f"[SETUP] Created output directory: {self.output_dir}")
        return self.output_dir
    
    def start_packet_capture(self):
        """Start tcpdump/tshark to capture UDP traffic on the server port."""
        loopback = self.find_loopback_interface()
        self.pcap_file = self.output_dir / "baseline_test.pcap"
        
        # Check which tool is available
        tcpdump_available = subprocess.run(
            ["which", "tcpdump"],
            capture_output=True
        ).returncode == 0
        
        tshark_available = subprocess.run(
            ["which", "tshark"],
            capture_output=True
        ).returncode == 0
        
        # Prefer tcpdump as it's more commonly available
        if tcpdump_available:
            cmd = [
                "tcpdump",
                "-i", loopback,
                "-w", str(self.pcap_file),
                f"udp port {SERVER_PORT}",
                "-q"  # Quiet mode
            ]
        else:
            cmd = [
                "tshark",
                "-i", loopback,
                "-w", str(self.pcap_file),
                "-f", f"udp port {SERVER_PORT}",
                "-q"  # Quiet mode
            ]
        
        print(f"[SETUP] Starting packet capture on {loopback} (port {SERVER_PORT})...")
        try:
            # Redirect stderr to suppress tcpdump/tshark output
            with open(self.output_dir / "packet_capture.log", "w") as log_file:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=log_file,
                    preexec_fn=os.setsid if os.name != 'nt' else None
                )
                self.processes.append(("packet_capture", proc))
                print(f"[SETUP] Packet capture started (PID: {proc.pid})")
                time.sleep(1)  # Give it time to start
                return proc
        except Exception as e:
            print(f"[ERROR] Failed to start packet capture: {e}")
            sys.exit(1)
    
    def start_server(self):
        """Start the server process."""
        server_log = self.output_dir / "server.log"
        print("[SETUP] Starting server...")
        
        try:
            with open(server_log, "w") as log_file:
                proc = subprocess.Popen(
                    [sys.executable, str(SERVER_SCRIPT)],
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    cwd=SCRIPT_DIR
                )
                self.processes.append(("server", proc))
                print(f"[SETUP] Server started (PID: {proc.pid})")
                time.sleep(2)  # Give server time to initialize
                return proc
        except Exception as e:
            print(f"[ERROR] Failed to start server: {e}")
            sys.exit(1)
    
    def start_clients(self):
        """Start NUM_CLIENTS client processes."""
        print(f"[SETUP] Starting {NUM_CLIENTS} clients...")
        client_procs = []
        
        for i in range(1, NUM_CLIENTS + 1):
            client_log = self.output_dir / f"client_{i}.log"
            try:
                with open(client_log, "w") as log_file:
                    # Pass player ID as argument for auto-join
                    proc = subprocess.Popen(
                        [sys.executable, str(CLIENT_SCRIPT), str(i)],
                        stdout=log_file,
                        stderr=subprocess.STDOUT,
                        cwd=SCRIPT_DIR
                    )
                    self.processes.append((f"client_{i}", proc))
                    client_procs.append(proc)
                    print(f"[SETUP] Client {i} started (PID: {proc.pid}) - will auto-join as Player {i}")
                    time.sleep(0.5)  # Stagger client starts slightly
            except Exception as e:
                print(f"[ERROR] Failed to start client {i}: {e}")
                # Continue with other clients
        
        return client_procs
    
    def run_test(self):
        """Run the test for the specified duration."""
        print(f"\n[TEST] Running baseline test for {TEST_DURATION} seconds...")
        print("[TEST] Server and clients are communicating...")
        
        start_time = time.time()
        elapsed = 0
        
        # Progress indicator
        while elapsed < TEST_DURATION:
            time.sleep(5)
            elapsed = time.time() - start_time
            remaining = int(TEST_DURATION - elapsed)
            if remaining > 0:
                print(f"[TEST] {remaining} seconds remaining...")
        
        print("[TEST] Test duration completed.")
    
    def terminate_processes(self):
        """Gracefully terminate all processes."""
        print("\n[TEARDOWN] Terminating processes...")
        
        # Terminate in reverse order (clients first, then server, then packet capture)
        for name, proc in reversed(self.processes):
            try:
                if proc.poll() is None:  # Process is still running
                    print(f"[TEARDOWN] Terminating {name} (PID: {proc.pid})...")
                    proc.terminate()
                else:
                    print(f"[TEARDOWN] {name} already terminated")
            except Exception as e:
                print(f"[WARN] Error terminating {name}: {e}")
        
        # Wait for processes to terminate
        time.sleep(2)
        
        # Force kill if still running
        for name, proc in self.processes:
            try:
                if proc.poll() is None:
                    print(f"[TEARDOWN] Force killing {name} (PID: {proc.pid})...")
                    proc.kill()
            except Exception as e:
                print(f"[WARN] Error force killing {name}: {e}")
        
        # Wait a bit more
        time.sleep(1)
        
        # Clean up process group for packet capture (Unix only)
        for name, proc in self.processes:
            if name == "packet_capture" and proc.poll() is None:
                try:
                    if os.name != 'nt':
                        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                except Exception:
                    pass
    
    def verify_outputs(self):
        """Verify that required output files exist."""
        print("\n[VERIFY] Checking for required output files...")
        
        all_good = True
        
        # Check for .pcap file
        if self.pcap_file and self.pcap_file.exists():
            size = self.pcap_file.stat().st_size
            print(f"[VERIFY] ✓ Packet capture file exists: {self.pcap_file} ({size} bytes)")
        else:
            print(f"[VERIFY] ✗ Packet capture file missing: {self.pcap_file}")
            all_good = False
        
        # Check for CSV file (server should generate metrics.csv)
        csv_candidates = [
            self.output_dir / "metrics.csv",
            SCRIPT_DIR / "metrics.csv",  # Server might write to current directory
        ]
        
        csv_found = False
        for csv_path in csv_candidates:
            if csv_path.exists():
                # Move it to output directory if it's in the wrong place
                if csv_path.parent != self.output_dir:
                    target = self.output_dir / csv_path.name
                    import shutil
                    shutil.move(str(csv_path), str(target))
                    csv_path = target
                
                size = csv_path.stat().st_size
                print(f"[VERIFY] ✓ Metrics CSV file exists: {csv_path} ({size} bytes)")
                self.csv_file = csv_path
                csv_found = True
                break
        
        if not csv_found:
            print(f"[VERIFY] ⚠ Metrics CSV file not found (server may not have CSV logging implemented yet)")
            # Don't fail the test for this, as it's a warning
        
        # Collect client CSV files
        client_csv_pattern = str(SCRIPT_DIR / "client_*_metrics.csv")
        client_csv_files = glob.glob(client_csv_pattern)
        if client_csv_files:
            for csv_file in client_csv_files:
                target = self.output_dir / Path(csv_file).name
                shutil.move(csv_file, str(target))
                size = target.stat().st_size
                print(f"[VERIFY] ✓ Client CSV file collected: {target.name} ({size} bytes)")
        
        # Check for log files
        log_files = ["server.log"] + [f"client_{i}.log" for i in range(1, NUM_CLIENTS + 1)]
        for log_file in log_files:
            log_path = self.output_dir / log_file
            if log_path.exists():
                size = log_path.stat().st_size
                print(f"[VERIFY] ✓ Log file exists: {log_file} ({size} bytes)")
            else:
                print(f"[VERIFY] ✗ Log file missing: {log_file}")
                all_good = False
        
        return all_good
    
    def run(self):
        """Main test execution flow."""
        print("=" * 60)
        print("Baseline Local Test - Automated Runner")
        print("=" * 60)
        
        try:
            # Step 0: Check dependencies and tools
            if not check_and_install_dependencies():
                return 1
            if not check_system_tools():
                return 1
            
            # Step 1: Setup
            self.kill_existing_processes()
            self.create_output_directory()
            
            # Step 2: Start packet capture
            self.start_packet_capture()
            
            # Step 3: Start server
            self.start_server()
            
            # Step 4: Start clients
            self.start_clients()
            
            # Step 5: Run test
            self.run_test()
            
            # Step 6: Teardown
            self.terminate_processes()
            
            # Step 7: Verify outputs
            success = self.verify_outputs()
            
            print("\n" + "=" * 60)
            if success:
                print("✓ Baseline test completed successfully!")
                print(f"Results saved to: {self.output_dir}")
            else:
                print("⚠ Baseline test completed with warnings.")
                print(f"Check results in: {self.output_dir}")
            print("=" * 60)
            
            return 0 if success else 1
            
        except KeyboardInterrupt:
            print("\n[ERROR] Test interrupted by user")
            self.terminate_processes()
            return 1
        except Exception as e:
            print(f"\n[ERROR] Test failed with exception: {e}")
            import traceback
            traceback.print_exc()
            self.terminate_processes()
            return 1


if __name__ == "__main__":
    runner = TestRunner()
    sys.exit(runner.run())

