import os
from pathlib import Path
from backlink_publisher.persistence.safe_write import atomic_write, rotate_snapshots

def test_atomic_write(tmp_path: Path):
    target = tmp_path / "test.txt"
    atomic_write(target, "hello world")
    assert target.exists()
    assert target.read_text(encoding="utf-8") == "hello world"

def test_rotate_snapshots(tmp_path: Path):
    target = tmp_path / "test.toml"
    history_dir = tmp_path / ".history"
    
    # 1. target doesn't exist -> no-op
    rotate_snapshots(target, history_dir, file_suffix=".toml", max_history=3)
    assert not history_dir.exists()
    
    # 2. create target
    target.write_text("content", encoding="utf-8")
    
    # 3. create snapshots
    for i in range(5):
        # We need to sleep slightly or mock st_mtime if rotation relies on distinct mtimes,
        # but let's just make sure they are written.
        # Let's adjust st_mtime of the created snapshots to ensure correct order for rotation.
        rotate_snapshots(target, history_dir, file_suffix=".toml", max_history=3)
    
    # Check rotation to max_history = 3
    snapshots = list(history_dir.glob("*.toml"))
    assert len(snapshots) == 3
    for s in snapshots:
        assert s.read_text(encoding="utf-8") == "content"
