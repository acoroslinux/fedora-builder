import subprocess
from pathlib import Path
from typing import Dict, Any
import logging

logger = logging.getLogger("disk_engine")

class DiskEngine:
    def __init__(self, workdir: Path, target_root: Path, output_name: str, config: Dict[str, Any], mode: str):
        self.workdir = Path(workdir)
        self.target_root = Path(target_root)
        self.output_name = output_name
        self.config = config
        self.mode = mode

    def _calculate_image_size(self, rootfs: Path) -> int:
        if self.mode == "mock":
            return 1024
        out = subprocess.check_output(["du", "-sm", str(rootfs)])
        return int(out.split()[0]) + 500

    def _create_partition_table(self, img_file: Path, size_mb: int):
        subprocess.run(["dd", "if=/dev/zero", f"of={img_file}", "bs=1M", f"count={size_mb}"], check=True)
        subprocess.run(["parted", "-s", str(img_file), "mktable", "gpt"], check=True)
        subprocess.run(["parted", "-s", str(img_file), "mkpart", "ESP", "fat32", "1MiB", "513MiB"], check=True)
        subprocess.run(["parted", "-s", str(img_file), "set", "1", "esp", "on"], check=True)
        subprocess.run(["parted", "-s", str(img_file), "mkpart", "primary", "ext4", "513MiB", "100%"], check=True)

    def _format_partitions(self, img_file: Path):
        pass

    def _install_grub(self, img_file: Path, rootfs: Path):
        pass

    def build_disk_image(self) -> Path:
        out_path = Path(f"output/{self.output_name}.img")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if self.mode == "mock":
            out_path.touch()
            return out_path
            
        size = self._calculate_image_size(self.target_root)
        self._create_partition_table(out_path, size)
        
        logger.warning("Formatting and GRUB installation on disk image is incomplete (requires root loop devices).")
        return out_path
