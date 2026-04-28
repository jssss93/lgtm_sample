"""YAML 기반 프롬프트 매니저 — Hot-Reload + 버전 관리 + A/B 테스트."""
import asyncio
import hashlib
import logging
import random
import threading
from pathlib import Path
from typing import Optional

import yaml

from domain.ports import PromptProvider, MetricsRecorder
from domain.prompt import PromptResolution

logger = logging.getLogger("agent")


class YamlPromptManager(PromptProvider):
    """YAML 파일 기반 프롬프트 제공자.

    기능:
    - prompts/ 디렉토리의 YAML 파일에서 프롬프트 로드
    - active 버전 또는 A/B 테스트 weight 기반 변이 선택
    - watchfiles awatch를 통한 무중단 Hot-Reload
    - YAML 파싱 실패 시 last-known-good 유지
    - YAML 파일 미발견 시 fallback_profiles로 폴백
    """

    def __init__(
        self,
        prompts_dir: str,
        fallback_profiles: dict,
        metrics: MetricsRecorder,
    ):
        self._dir = Path(prompts_dir)
        self._fallback = fallback_profiles
        self._metrics = metrics
        self._configs: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._watcher_task: Optional[asyncio.Task] = None
        self._load_all()

    def _load_all(self) -> None:
        """prompts/ 디렉토리의 모든 YAML 파일 로드."""
        if not self._dir.exists():
            logger.warning(
                "Prompts directory not found, using fallback profiles",
                extra={"dir": str(self._dir)},
            )
            return
        for path in sorted(self._dir.glob("*.yaml")):
            self._load_file(path)

    def _load_file(self, path: Path) -> bool:
        """단일 YAML 파일 로드. 성공 시 True, 실패 시 False (last-known-good 유지)."""
        agent_type = path.stem
        try:
            with open(path) as f:
                data = yaml.safe_load(f)
            if not isinstance(data, dict) or "versions" not in data:
                logger.warning(
                    "Invalid prompt YAML schema — keeping previous config",
                    extra={"file": str(path)},
                )
                return False
            # active 버전이 versions에 존재하는지 검증
            active = data.get("active")
            if active and active not in data["versions"]:
                logger.warning(
                    "Active version not found in versions — keeping previous config",
                    extra={"file": str(path), "active": active, "versions": list(data["versions"].keys())},
                )
                return False
            with self._lock:
                self._configs[agent_type] = data
            logger.info(
                "Prompt loaded",
                extra={
                    "agent_type": agent_type,
                    "version": active or "unknown",
                    "versions": list(data["versions"].keys()),
                    "source": "yaml",
                },
            )
            return True
        except Exception as e:
            logger.error(
                "Failed to load prompt YAML — keeping previous config",
                extra={"file": str(path), "error": str(e)},
            )
            return False

    # ─── PromptProvider 인터페이스 ───────────────────────────────

    def resolve(self, agent_type: str, subject_id: Optional[str] = None) -> PromptResolution:
        with self._lock:
            config = self._configs.get(agent_type)

        if not config:
            return self._fallback_resolve(agent_type)

        versions = config["versions"]
        ab_test = config.get("ab_test", {})

        if ab_test.get("enabled") and ab_test.get("variants"):
            version = self._select_variant(ab_test["variants"], subject_id)
            variant = f"ab-{version}"
        else:
            version = config.get("active", list(versions.keys())[-1])
            variant = "active"

        prompt_data = versions.get(version)
        if not prompt_data:
            return self._fallback_resolve(agent_type)

        system_prompt = prompt_data.get("system_prompt", "").strip()
        content_hash = hashlib.md5(system_prompt.encode()).hexdigest()[:8]

        self._metrics.record_prompt_selection(agent_type, version, variant)

        return PromptResolution(
            system_prompt=system_prompt,
            version=version,
            variant=variant,
            source="yaml",
            content_hash=content_hash,
        )

    def get_info(self) -> dict[str, dict]:
        with self._lock:
            configs_snapshot = dict(self._configs)

        result: dict[str, dict] = {}
        for agent_type, config in configs_snapshot.items():
            active = config.get("active", "unknown")
            versions = list(config.get("versions", {}).keys())
            ab = config.get("ab_test", {})
            result[agent_type] = {
                "active_version": active,
                "available_versions": versions,
                "ab_test_enabled": ab.get("enabled", False),
                "ab_variants": ab.get("variants", []) if ab.get("enabled") else [],
                "source": "yaml",
            }
        # fallback 프로필 중 YAML이 없는 에이전트 표시
        for agent_type in self._fallback:
            if agent_type not in result and agent_type != "scorer":
                result[agent_type] = {
                    "active_version": "fallback",
                    "available_versions": ["fallback"],
                    "ab_test_enabled": False,
                    "ab_variants": [],
                    "source": "fallback",
                }
        return result

    # ─── A/B Sticky Assignment ───────────────────────────────────

    def _select_variant(self, variants: list, subject_id: Optional[str]) -> str:
        """weight 기반 변이 선택. subject_id가 있으면 해시로 sticky assignment."""
        total = sum(v["weight"] for v in variants)
        if total <= 0:
            return variants[0]["version"]

        if subject_id:
            h = int(hashlib.md5(subject_id.encode()).hexdigest(), 16) % total
        else:
            h = random.randint(0, total - 1)

        cumulative = 0
        for v in variants:
            cumulative += v["weight"]
            if h < cumulative:
                return v["version"]
        return variants[-1]["version"]

    # ─── Fallback ────────────────────────────────────────────────

    def _fallback_resolve(self, agent_type: str) -> PromptResolution:
        profile = self._fallback.get(agent_type, {})
        prompt = profile.get("system_prompt", "")
        return PromptResolution(
            system_prompt=prompt,
            version="fallback",
            variant="fallback",
            source="fallback",
            content_hash=hashlib.md5(prompt.encode()).hexdigest()[:8],
        )

    # ─── Hot-Reload Watcher ──────────────────────────────────────

    async def start_watcher(self) -> None:
        if self._watcher_task is not None:
            return
        self._watcher_task = asyncio.create_task(self._watch())

    async def stop_watcher(self) -> None:
        if self._watcher_task:
            self._watcher_task.cancel()
            try:
                await self._watcher_task
            except asyncio.CancelledError:
                pass
            self._watcher_task = None

    async def _watch(self) -> None:
        """prompts/ 디렉토리를 감시하여 변경 시 자동 리로드."""
        try:
            from watchfiles import awatch, Change
        except ImportError:
            logger.warning("watchfiles not installed — hot-reload disabled")
            return

        if not self._dir.exists():
            logger.warning("Prompts directory not found — hot-reload disabled")
            return

        logger.info("Prompt hot-reload watcher started", extra={"dir": str(self._dir)})
        try:
            async for changes in awatch(self._dir):
                for change_type, path_str in changes:
                    path = Path(path_str)
                    if path.suffix not in (".yaml", ".yml"):
                        continue

                    agent_type = path.stem
                    if change_type in (Change.modified, Change.added):
                        with self._lock:
                            old_config = self._configs.get(agent_type)
                        old_version = old_config.get("active") if old_config else None

                        success = self._load_file(path)
                        if success:
                            with self._lock:
                                new_config = self._configs.get(agent_type)
                            new_version = new_config.get("active") if new_config else None
                            self._metrics.record_prompt_reload(agent_type, new_version or "unknown")
                            logger.info(
                                "Prompt hot-reloaded",
                                extra={
                                    "agent_type": agent_type,
                                    "old_version": old_version,
                                    "new_version": new_version,
                                },
                            )

                    elif change_type == Change.deleted:
                        with self._lock:
                            self._configs.pop(agent_type, None)
                        logger.warning(
                            "Prompt file deleted — falling back to config.py",
                            extra={"agent_type": agent_type},
                        )
        except asyncio.CancelledError:
            logger.info("Prompt hot-reload watcher stopped")
        except Exception as e:
            logger.error("Prompt watcher error", extra={"error": str(e)})
