import pandas as pd
import matplotlib.pyplot as plt
import glob
import os


def generate_report_plots(experiment_dir):
    print(f"Processing directory: {experiment_dir}")
    plt.style.use('ggplot')

    # 1. Load Server Metrics
    metrics_path = os.path.join(experiment_dir, 'metrics.csv')
    try:
        server_df = pd.read_csv(metrics_path)
        server_df = server_df.dropna(subset=['snapshot_id'])
    except FileNotFoundError:
        print(f"  metrics.csv not found in {experiment_dir}. Skipping.")
        return

    # 2. Load Client Metrics (Find the first client file)
    # Search for client_*.csv files inside the experiment directory
    client_files = glob.glob(os.path.join(experiment_dir, 'client_*_metrics.csv'))
    if not client_files:
        print(f"  No client metrics found in {experiment_dir}.")
        return

    # Sort to ensure consistent selection (e.g. always client_1 if available)
    client_files.sort()
    selected_client_file = client_files[0]
    client_id = os.path.basename(selected_client_file).split('_')[1]  # extract ID from filename

    print(f"  Selected client file: {os.path.basename(selected_client_file)}")
    client_df = pd.read_csv(selected_client_file)

    # === Figure 1: Network Latency Distribution ===
    plt.figure(figsize=(10, 6))
    plt.hist(client_df['latency_ms'], bins=30, color='#4CAF50', alpha=0.7, edgecolor='black')
    plt.title(f'Client {client_id} Latency Distribution - {os.path.basename(experiment_dir)}')
    plt.xlabel('Latency (ms)')
    plt.ylabel('Frequency (Packets)')
    plt.axvline(client_df['latency_ms'].mean(), color='red', linestyle='dashed', linewidth=1,
                label=f"Mean: {client_df['latency_ms'].mean():.2f}ms")
    plt.legend()
    # Save to the experiment directory
    plt.savefig(os.path.join(experiment_dir, 'plot_latency_dist.png'))
    plt.close()  # Close to free memory

    # === Figure 2: Server Tick Stability ===
    # Deduplicate by snapshot_id (since server logs one row per client per snapshot)
    unique_server_df = server_df.drop_duplicates(subset=['snapshot_id']).copy()
    unique_server_df = unique_server_df.sort_values('snapshot_id')

    # Calculate time difference between consecutive snapshots
    unique_server_df['delta_ts'] = unique_server_df['server_timestamp_ms'].diff()

    plt.figure(figsize=(10, 6))
    plt.bar(unique_server_df['snapshot_id'], unique_server_df['delta_ts'], color='#2196F3', width=1.0, edgecolor='none',
            alpha=0.7)
    plt.title(f'Server Tick Rate Stability - {os.path.basename(experiment_dir)}')
    plt.xlabel('Snapshot Sequence ID')
    plt.ylabel('Inter-Arrival Time (ms)')
    plt.ylim(0, 100)  # Limit to see outliers
    plt.axhline(25, color='red', linestyle='--', label='Target (40Hz)')
    plt.legend()
    # Save to the experiment directory
    plt.savefig(os.path.join(experiment_dir, 'plot_server_tick.png'))
    plt.close()

    print(f"  Plots generated in {experiment_dir}")


if __name__ == "__main__":
    # Define patterns to search for types of experiments
    # We want exactly ONE folder for 'baseline', ONE for 'delay', ONE for 'loss_2pct', ONE for 'loss_5pct'.
    # We will pick the latest folder (alphabetically last) for each category.

    categories = ['baseline', 'delay', 'loss_2pct', 'loss_5pct']

    selected_dirs = []

    for category in categories:
        # Match any folder starting with category name (e.g. baseline_*)
        pattern = f"{category}_*"
        candidates = glob.glob(pattern)
        # Filter for directories only
        candidates = [d for d in candidates if os.path.isdir(d)]

        if candidates:
            # Sort to find the latest (assuming timestamp in name sorts correctly, which YYYY-MM-DD_HH-MM-SS does)
            candidates.sort()
            latest = candidates[-1]
            selected_dirs.append(latest)
            print(f"Selected for '{category}': {latest}")
        else:
            print(f"No directories found for '{category}'")

    if not selected_dirs:
        print("No valid experiment directories found.")
    else:
        print(f"Starting plot generation for {len(selected_dirs)} directories...")
        for d in selected_dirs:
            generate_report_plots(d)
        print("Done.")