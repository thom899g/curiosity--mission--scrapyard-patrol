"""
Configuration management for the Micro-Acquisition Core.
All configuration is validated via Pydantic and synced with Firebase.
"""
import os
from typing import Dict, List, Optional
from pydantic import BaseModel, Field, validator
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class SystemMode(str, Enum):
    NORMAL = "NORMAL"
    CIRCUIT_BREAKER = "CIRCUIT_BREAKER"
    MAINTENANCE = "MAINTENANCE"
    REPLICATING = "REPLICATING"


class ExchangeConfig(BaseModel):
    name: str
    enabled: bool = True
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    testnet: bool = True  # Start with testnets
    rate_limit_ms: int = 1000


class DexConfig(BaseModel):
    name: str
    chain: str
    rpc_url: str
    factory_address: str
    router_address: str
    weth_address: str
    enabled: bool = True
    testnet: bool = True
    max_slippage_percent: float = 2.0


class ScannerConfig(BaseModel):
    max_position_usd: float = Field(default=10.0, ge=1.0, le=100.0)
    min_profit_threshold_usd: float = Field(default=0.5, ge=0.1, le=5.0)
    scan_interval_seconds: int = Field(default=30, ge=10, le=300)
    max_concurrent_scans: int = Field(default=3, ge=1, le=10)
    
    @validator('min_profit_threshold_usd')
    def validate_profit_threshold(cls, v, values):
        if 'max_position_usd' in values and v >= values['max_position_usd']:
            raise ValueError('min_profit_threshold must be less than max_position')
        return v


class VaultConfig(BaseModel):
    url: str = "http://vault-service:5000"
    timeout_seconds: int = 10
    max_retries: int = 3


class FirebaseConfig(BaseModel):
    project_id: str
    service_account_path: str = "serviceAccountKey.json"
    collections: Dict[str, str] = {
        "system_state": "system/state",
        "opportunities": "opportunities",
        "executions": "executions",
        "config": "config",
        "sensory": "sensory",
        "replication": "replication_queue"
    }


class AppConfig(BaseModel):
    instance_id: str
    system_mode: SystemMode = SystemMode.NORMAL
    exchanges: List[ExchangeConfig]
    dexes: List[DexConfig]
    scanner: ScannerConfig
    vault: VaultConfig
    firebase: FirebaseConfig
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    
    # Adaptive parameters (updated by brainstem)
    adaptive_params: Dict[str, float] = Field(default_factory=lambda: {
        "min_profit_threshold_usd": 0.5,
        "api_backoff_sec": 1.0,
        "gas_price_multiplier": 1.2
    })


def load_config() -> AppConfig:
    """Load configuration from environment variables with defaults."""
    try:
        instance_id = os.getenv("INSTANCE_ID", "instance_1_0")
        
        # Exchange configurations
        exchanges = [
            ExchangeConfig(
                name="binance",
                api_key=os.getenv("BINANCE_API_KEY"),
                api_secret=os.getenv("BINANCE_API_SECRET"),
                testnet=os.getenv("BINANCE_TESTNET", "true").lower() == "true"
            ),
            ExchangeConfig(
                name="kraken",
                api_key=os.getenv("KRAKEN_API_KEY"),
                api_secret=os.getenv("KRAKEN_API_SECRET")
            )
        ]
        
        # DEX configurations (starting with testnets)
        dexes = [
            DexConfig(
                name="uniswap_v3",
                chain="sepolia",
                rpc_url=os.getenv("SEPOLIA_RPC_URL", "https://rpc.sepolia.org"),
                factory_address="0x0227628f3F023bb0B980b67D528571c95c6DaC1c",
                router_address="0x3bFA4769FB09eefC5a80d6E87c3B9C650f7Ae48E",
                weth_address="0xfFf9976782d46CC05630D1f6eBAb18b2324d6B14",
                testnet=True
            )
        ]
        
        config = AppConfig(
            instance_id=instance_id,
            exchanges=[e for e in exchanges if e.enabled],
            dexes=[d for d in dexes if d.enabled],
            scanner=ScannerConfig(),
            vault=VaultConfig(),
            firebase=FirebaseConfig(
                project_id=os.getenv("FIREBASE_PROJECT_ID", "micro-acquisition-core")
            ),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID")
        )
        
        logger.info(f"Configuration loaded successfully for instance: {instance_id}")
        return config
        
    except Exception as e:
        logger.error(f"Failed to load configuration: {e}")
        raise