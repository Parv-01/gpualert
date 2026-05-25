"""
Simulated ML training run. Writes a metrics CSV and a summary file.

Run with:
    gpualert run -- python examples/simple_training.py
    gpualert run --dry-run -- python examples/simple_training.py 20
"""

from __future__ import annotations

import csv
import os
import random
import sys
import time


def simulate_training(epochs: int = 10) -> None:
    print(f"Starting training — {epochs} epochs")
    print("Device: cuda:0 (simulated)")
    print("Model : ResNet-50 (simulated)\n")

    os.makedirs("outputs", exist_ok=True)
    best_acc = 0.0
    rows = [["epoch", "train_loss", "val_loss", "accuracy"]]

    for epoch in range(1, epochs + 1):
        time.sleep(0.2)
        train_loss = round(2.0 * (0.85**epoch) + random.uniform(-0.05, 0.05), 4)
        val_loss = round(train_loss + random.uniform(0.01, 0.10), 4)
        accuracy = round(min(0.99, 0.5 + epoch * 0.04 + random.uniform(-0.01, 0.01)), 4)
        best_acc = max(best_acc, accuracy)
        rows.append([epoch, train_loss, val_loss, accuracy])
        print(
            f"Epoch {epoch:2d}/{epochs} — loss: {train_loss:.4f}"
            f" — val_loss: {val_loss:.4f} — accuracy: {accuracy:.4f}"
        )

    with open("outputs/metrics.csv", "w", newline="") as f:
        csv.writer(f).writerows(rows)
    with open("outputs/summary.txt", "w") as f:
        f.write(f"Training complete\nBest accuracy: {best_acc:.4f}\nEpochs: {epochs}\n")

    print(f"\nTraining complete. Best accuracy: {best_acc:.4f}")
    print("Artifacts: outputs/metrics.csv, outputs/summary.txt")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    simulate_training(n)
