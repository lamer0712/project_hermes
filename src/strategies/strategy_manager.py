import re
import json
from typing import Optional
from src.utils.logger import logger
from src.strategies.base import BaseStrategy
from src.strategies.pullback_trend import PullbackTrendStrategy
from src.strategies.mean_reversion import MeanReversionStrategy
from src.strategies.breakout import BreakoutStrategy
from src.strategies.bearish import BearishStrategy
from src.strategies.panic import PanicStrategy
from src.strategies.vwap_reversion import VWAPReversionStrategy
from src.strategies.opening_scalp import OpeningScalpStrategy
from src.strategies.bollinger_squeeze import BollingerSqueezeStrategy
import os

class StrategyManager:
    """
    전략 등록, 검색, strategy.md 파싱을 담당하는 매니저 클래스.

    - 전략 클래스를 키(key)로 등록/조회
    - 새로운 전략 추가 시 _register_defaults()에 한 줄만 추가하면 됨
    """

    def _register_defaults(self):
        """기본 제공 전략들을 일괄 등록합니다."""
        self.register("PullbackTrend", PullbackTrendStrategy)
        self.register("MeanReversion", MeanReversionStrategy)
        self.register("Breakout", BreakoutStrategy)
        self.register("Bearish", BearishStrategy)
        self.register("Panic", PanicStrategy)
        self.register("VWAPReversion", VWAPReversionStrategy)
        self.register("OpeningScalp", OpeningScalpStrategy)
        self.register("BollingerSqueeze", BollingerSqueezeStrategy)


    def __init__(self, config_path: str = "data/optimized_params.json"):
        self._registry: dict[str, tuple[type, dict]] = {}
        self.config_path = config_path
        self.optimized_params = {}
        self.optimized_strategy_map = {}
        
        self._register_defaults()
        self.load_optimized_config()

    def load_optimized_config(self):
        """저장된 최적화 파라미터 및 전략 맵을 로드합니다."""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r") as f:
                    config = json.load(f)
                    self.optimized_params = config.get("strategy_params", {})
                    self.optimized_strategy_map = config.get("strategy_map", {})
                    logger.info(f"[StrategyManager] 최적화 설정 로드 완료: {len(self.optimized_params)}건, 맵 {len(self.optimized_strategy_map)}건")
            except Exception as e:
                logger.error(f"[StrategyManager] 설정 로드 실패: {e}")

    def register(self, key: str, strategy_cls: type, default_params: dict = None):
        """
        전략을 키로 등록합니다.

        Args:
            key: 전략 식별 키 (예: "rsi_momentum")
            strategy_cls: BaseStrategy를 상속한 전략 클래스
            default_params: 기본 파라미터 (None이면 전략 클래스 자체의 기본값 사용)
        """
        self._registry[key] = (strategy_cls, default_params or {})
        logger.info(f"[StrategyManager] 전략 등록: {key}")

    def get_strategy_class(self, key: str) -> Optional[type]:
        """키로 전략 클래스를 조회합니다."""
        entry = self._registry.get(key)
        return entry[0] if entry else None

    def get_strategy(self, key: str, params: dict = None) -> Optional[BaseStrategy]:
        """
        키와 파라미터로 전략 인스턴스를 생성합니다.

        Args:
            key: 등록된 전략 키
            params: 전략 파라미터 (None이면 기본 파라미터 사용)

        Returns:
            BaseStrategy 인스턴스 또는 None (키가 없을 경우)
        """
        entry = self._registry.get(key)
        if not entry:
            logger.error(f"[StrategyManager] 등록되지 않은 전략 키: {key}")
            return None

        strategy_cls, default_params = entry
        
        # 최적화된 파라미터가 있으면 우선 사용
        opt_params = self.optimized_params.get(key)
        if params is None and opt_params:
            final_params = opt_params
            # logger.info(f"[StrategyManager] {key} 전략에 최적화된 파라미터 적용 중...")
        else:
            final_params = params if params is not None else default_params
            
        return strategy_cls(final_params) if final_params else strategy_cls()

    def list_strategies(self) -> list[str]:
        """등록된 전략 키 목록을 반환합니다."""
        return list(self._registry.keys())
