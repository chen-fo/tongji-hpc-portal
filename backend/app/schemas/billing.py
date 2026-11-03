from datetime import date, datetime, timezone
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, StringConstraints, field_validator, model_validator

from app.schemas.accounts import StrictModel

DecimalText = Annotated[str, StringConstraints(pattern=r"^\d{1,9}(\.\d{1,6})?$")]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)]


class GPURate(StrictModel):
    tres_key: str = Field(pattern=r"^gres/gpu:[a-zA-Z0-9_.-]{1,64}$")
    price_per_hour: DecimalText


class RateCreate(StrictModel):
    cluster_name: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_.-]+$")
    partition: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_.-]+$")
    cpu_price_per_core_hour: DecimalText | None = None
    physical_cores_per_slurm_cpu: DecimalText | None = None
    gpu_rates: list[GPURate] = Field(default_factory=list, max_length=64)
    charge_cpu_with_gpu: bool | None = None
    source_url: str = Field(min_length=1, max_length=2048)
    verified_at: date
    effective_from: datetime
    confirmed: Literal[True]

    @field_validator("source_url")
    @classmethod
    def valid_source(cls, value):
        value = value.strip()
        try:
            parsed = urlsplit(value)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or any(ord(ch) < 32 for ch in value)
            ):
                raise ValueError
            _ = parsed.port  # Reject invalid ports.
        except ValueError:
            raise ValueError("费率来源必须是有效的 HTTP(S) 网址，且不能包含用户名或密码") from None
        return value

    @field_validator("effective_from")
    @classmethod
    def utc_time(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("生效时间须包含时区")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    @model_validator(mode="after")
    def supported_rules(self):
        from decimal import Decimal

        if (self.cpu_price_per_core_hour is None) != (self.physical_cores_per_slurm_cpu is None):
            raise ValueError("CPU 单价与物理核换算系数必须同时提供")
        if self.physical_cores_per_slurm_cpu is not None:
            factor = Decimal(self.physical_cores_per_slurm_cpu)
            if not Decimal("0") < factor <= Decimal("512"):
                raise ValueError("CPU 物理核换算系数应大于 0 且不超过 512")
        if not self.gpu_rates and self.cpu_price_per_core_hour is None:
            raise ValueError("至少填写一条费率")
        if len({rate.tres_key for rate in self.gpu_rates}) != len(self.gpu_rates):
            raise ValueError("GPU 资源型号重复")
        return self


class BudgetUpdate(StrictModel):
    limit_minor: int = Field(ge=0, le=10**15, strict=True)
    warning_threshold: int = Field(ge=1, le=100, strict=True)
    expected_version: int = Field(ge=0, le=2**31 - 1, strict=True)
    reason: Reason


class AdjustmentCreate(StrictModel):
    user_id: int = Field(gt=0, strict=True)
    amount_minor: int = Field(ge=-(10**15), le=10**15, strict=True)
    entry_type: Literal["manual_adjustment", "official_adjustment"]
    reason: Reason
    idempotency_key: str = Field(pattern=r"^[a-zA-Z0-9_-]{8,120}$")
    statement_reference: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def valid_adjustment(self):
        if self.amount_minor == 0:
            raise ValueError("调整金额不能为零")
        if self.entry_type == "official_adjustment" and not (
            self.statement_reference and self.statement_reference.strip()
        ):
            raise ValueError("官方账单调整必须填写账单来源")
        if self.statement_reference:
            self.statement_reference = self.statement_reference.strip() or None
        return self
