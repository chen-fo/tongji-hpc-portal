export interface Budget {
  limit_minor: number;
  warning_threshold: number;
  version: number;
  control_mode: 'observe';
  updated_at: string;
}

export interface BillingSummary {
  user_id: number;
  username: string;
  currency: string;
  budget: Budget | null;
  recorded_minor: number;
  reserved_minor: number | null;
  available_minor: number | null;
  running_estimate_minor: number | null;
  notice: string;
}

export interface BillingEntry {
  id: number;
  official_account_id: number;
  user_id: number | null;
  slurm_job_id: number | null;
  rate_snapshot_id: number | null;
  entry_type: string;
  amount_minor: number;
  currency: string;
  source: string;
  reconciliation_status: string;
  reason: string | null;
  statement_reference: string | null;
  created_at: string;
}

export interface GpuRate {
  tres_key: string;
  price_per_hour: string;
}

export interface RateSnapshot {
  id: number;
  version: number;
  cluster_name: string;
  partition: string;
  cpu_price_per_core_hour: string | null;
  physical_cores_per_slurm_cpu: string | null;
  gpu_rates: GpuRate[];
  charge_cpu_with_gpu: boolean | null;
  source_url: string;
  verified_at: string;
  effective_from: string;
  created_at: string;
}

// Money is exchanged with the API in integer fen; avoid parsing decimal currency as floats.
export function toMinor(value: string, signed = false): number {
  const amount = value.trim();
  if (!(signed ? /^-?\d{1,9}(?:\.\d{1,2})?$/ : /^\d{1,9}(?:\.\d{1,2})?$/).test(amount)) {
    throw new Error('请输入金额，最多两位小数');
  }
  const negative = amount.startsWith('-');
  const [yuan, cents = ''] = (negative ? amount.slice(1) : amount).split('.');
  const minor = Number(yuan) * 100 + Number(cents.padEnd(2, '0'));
  return negative ? -minor : minor;
}

export function formatMoney(minor: number | null | undefined): string {
  if (minor == null || !Number.isSafeInteger(minor)) return '待核算';
  const value = Math.abs(minor);
  return `${minor < 0 ? '-' : ''}¥${Math.floor(value / 100)}.${String(value % 100).padStart(2, '0')}`;
}

export function budgetWarning(summary: BillingSummary): boolean {
  if (!summary.budget || !Number.isSafeInteger(summary.recorded_minor) || !Number.isSafeInteger(summary.budget.limit_minor)) return false;
  const { limit_minor, warning_threshold } = summary.budget;
  return BigInt(summary.recorded_minor) * 100n >= BigInt(limit_minor) * BigInt(warning_threshold);
}

export function showTime(value: string | null | undefined): string {
  if (!value) return '—';
  const utc = /(?:Z|[+-]\d\d:\d\d)$/.test(value) ? value : value + 'Z';
  const date = new Date(utc);
  return Number.isNaN(date.valueOf()) ? '—' : date.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' });
}
