"""
Config API — GET/PUT /config (JSON) and PUT /config/raw (raw YAML with validation).
Changes take effect immediately; no restart needed because config proxy reads _raw on every access.
"""
import logging
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

from goalforge import config as config_module
from goalforge.config import config

logger = logging.getLogger(__name__)
router = APIRouter()
bearer = HTTPBearer()

_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"


def _auth(credentials: HTTPAuthorizationCredentials = Depends(bearer)):
    if credentials.credentials != config.api.secret_token:
        raise HTTPException(status_code=401, detail="Invalid token")
    return credentials.credentials


def _read_raw_yaml() -> str:
    return _CONFIG_PATH.read_text(encoding="utf-8")


def _write_raw_yaml(content: str):
    _CONFIG_PATH.write_text(content, encoding="utf-8")
    config_module.reload()
    # Clear LLM provider cache so new provider config takes effect
    try:
        from goalforge.llm.factory import clear_cache
        clear_cache()
    except Exception:
        pass


@router.get("/config")
def get_config(token: str = Depends(_auth)):
    """Return current config as JSON + raw YAML string."""
    return {
        "config": config_module.get_raw(),
        "raw_yaml": _read_raw_yaml(),
    }


class ConfigUpdateRequest(BaseModel):
    config: dict


@router.put("/config")
def put_config(req: ConfigUpdateRequest, token: str = Depends(_auth)):
    """Accept updated config as a JSON dict, write to config.yaml, reload."""
    try:
        yaml_str = yaml.dump(req.config, default_flow_style=False, allow_unicode=True)
        _write_raw_yaml(yaml_str)
        return {"status": "saved"}
    except Exception as e:
        logger.error("Config save failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


class RawYamlRequest(BaseModel):
    yaml: str


@router.put("/config/raw")
def put_config_raw(req: RawYamlRequest, token: str = Depends(_auth)):
    """Accept raw YAML string; validate before writing."""
    try:
        parsed = yaml.safe_load(req.yaml)
        if not isinstance(parsed, dict):
            raise ValueError("YAML must be a mapping at the top level")
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {e}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        _write_raw_yaml(req.yaml)
        return {"status": "saved"}
    except Exception as e:
        logger.error("Config raw save failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
