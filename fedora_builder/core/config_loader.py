import json
from pathlib import Path
from typing import Optional, Dict, Any, List
from fedora_builder.core.path_utils import resolve_from_project


class ConfigLoaderError(Exception):
    """Exception raised for configuration loading errors."""
    pass


class ConfigLoader:
    def __init__(self, config_root: Optional[Path] = None):
        self.config_root = config_root or resolve_from_project("configs")

    def load_json(self, path: Path) -> Dict[str, Any]:
        if not path.exists():
            raise ConfigLoaderError(f"Configuration file not found: {path}")
        try:
            with open(path, "r") as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            raise ConfigLoaderError(f"Invalid JSON in {path}: {e}")
        except Exception as e:
            raise ConfigLoaderError(f"Error loading {path}: {e}")

    def load_profile(self, category: str, profile_name: str) -> Dict[str, Any]:
        path = self.config_root / category / f"{profile_name}.json"
        try:
            return self.load_json(path)
        except ConfigLoaderError:
            return {}

    def _merge_dicts(self, base: Dict[str, Any], update: Dict[str, Any]) -> Dict[str, Any]:
        result = base.copy()
        for key, value in update.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._merge_dicts(result[key], value)
            elif key in result and isinstance(result[key], list) and isinstance(value, list):
                existing = list(result[key])
                extras = [item for item in value if item not in existing]
                result[key] = existing + extras
            else:
                result[key] = value
        return result

    def assemble_build_config(
        self,
        global_config_path: Path | str,
        architecture: str,
        release: str,
        desktop: Optional[str] = None,
        kernel: Optional[str] = None,
        bootloader: Optional[str] = None,
        variant: Optional[str] = None,
        package_profiles: Optional[List[str]] = None,
        service_profiles: Optional[List[str]] = None,
        repo_profiles: Optional[List[str]] = None,
        live_profile: Optional[str] = None,
    ) -> Dict[str, Any]:

        global_config_path = Path(global_config_path) if isinstance(global_config_path, str) else global_config_path

        config = {
            "packages": [],
            "groups": [],
            "services": {"enable": [], "disable": []},
            "copy_files": [],
            "repos": [],
            "kernel_packages": [],
            "bootloader": {},
            "live_user": {},
            "release_info": {},
            "arch_info": {},
            "system": {},
            "boot": {},
            "variant_info": {},
        }

        if global_config_path.exists():
            config = self._merge_dicts(config, self.load_json(global_config_path))

        base_customizations_path = self.config_root / "base_customizations.json"
        if base_customizations_path.exists():
            base_customizations = self.load_json(base_customizations_path)
            for entry in base_customizations.get("base_copy_files", []):
                if isinstance(entry, dict) and entry not in config["copy_files"]:
                    config["copy_files"].append(entry)

        config = self._merge_dicts(config, self.load_profile("releases", release))
        config = self._merge_dicts(config, self.load_profile("architectures", architecture))

        if variant:
            config = self._merge_dicts(config, self.load_profile("variants", variant))

        if desktop:
            desktop_data = self.load_profile("desktops", desktop)
            config = self._merge_dicts(config, desktop_data)
            for entry in desktop_data.get("desktop_environment", {}).get("copy_files", []):
                if entry not in config["copy_files"]:
                    config["copy_files"].append(entry)

        if kernel:
            config = self._merge_dicts(config, self.load_profile("kernels", kernel))

        if bootloader:
            config = self._merge_dicts(config, self.load_profile("bootloaders", bootloader))

        config = self._merge_dicts(config, self.load_profile("packages", "base"))

        if package_profiles:
            for profile in package_profiles:
                profile_data = self.load_profile("packages", profile)
                config = self._merge_dicts(config, profile_data)
                for entry in profile_data.get("copy_files", []):
                    if entry not in config["copy_files"]:
                        config["copy_files"].append(entry)

        if service_profiles:
            for profile in service_profiles:
                service_profile = self.load_profile("services", profile)
                service_enable = service_profile.pop("enable", [])
                service_disable = service_profile.pop("disable", [])
                if isinstance(service_enable, list) or isinstance(service_disable, list):
                    nested_services = service_profile.get("services", {})
                    if not isinstance(nested_services, dict):
                        nested_services = {"enable": [], "disable": []}
                    nested_services = self._merge_dicts(
                        {"enable": [], "disable": []},
                        nested_services,
                    )
                    if isinstance(service_enable, list):
                        nested_services["enable"] = nested_services.get("enable", []) + service_enable
                    if isinstance(service_disable, list):
                        nested_services["disable"] = nested_services.get("disable", []) + service_disable
                    service_profile["services"] = nested_services
                config = self._merge_dicts(config, service_profile)

        if repo_profiles:
            for profile in repo_profiles:
                config = self._merge_dicts(config, self.load_profile("repos", profile))

        if live_profile:
            config["live_user"] = self._merge_dicts(
                config.get("live_user", {}),
                self.load_profile("live-users", live_profile),
            )

        if not isinstance(config.get("services"), dict):
            config["services"] = {"enable": [], "disable": []}

        for legacy_key, target_key in (("services_enable", "enable"), ("services_disable", "disable")):
            legacy_services = config.pop(legacy_key, [])
            if isinstance(legacy_services, list):
                config["services"][target_key] = config["services"].get(target_key, []) + legacy_services

        for key in ["packages", "groups", "repos", "kernel_packages", "copy_files"]:
            if key in config and isinstance(config[key], list):
                if key == "copy_files":
                    deduped = []
                    for entry in config[key]:
                        if entry not in deduped:
                            deduped.append(entry)
                    config[key] = deduped
                else:
                    config[key] = list(dict.fromkeys(config[key]))

        if "services" in config:
            for state in ["enable", "disable"]:
                if state in config["services"] and isinstance(config["services"][state], list):
                    config["services"][state] = list(dict.fromkeys(config["services"][state]))

        return config
