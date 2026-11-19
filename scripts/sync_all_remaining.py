"""Batch synchronization and re-renderer for remaining parts.

Rerenders letterbox heading styling (clean typography, Anton font, optical vertical centering,
creator badge, NO 'GTA 6 TRAILER 2') and guarantees microsecond audio-subtitle sync.
"""

import sys
import time
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.sync_part import sync_part

REMAINING_PARTS = [11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24]

def main():
    t_start = time.time()
    print(f"Starting batch sync for {len(REMAINING_PARTS)} parts: {REMAINING_PARTS}")
    for idx, p_num in enumerate(REMAINING_PARTS, 1):
        print(f"\n>>> [{idx}/{len(REMAINING_PARTS)}] Processing Part {p_num:02d}...")
        t_part = time.time()
        try:
            sync_part(p_num)
            print(f">>> Part {p_num:02d} completed in {time.time() - t_part:.1f}s")
        except Exception as e:
            print(f">>> ERROR syncing Part {p_num:02d}: {e}")
    
    print(f"\n================ ALL REMAINING PARTS COMPLETED in {time.time() - t_start:.1f}s ================")

if __name__ == "__main__":
    main()
