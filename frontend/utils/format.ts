/** Display formatting only.
 *
 *  These helpers never derive a new financial quantity - they render what the
 *  backend already computed. A null stays a visible dash, never a zero.
 */

const NO_DATA = "—";

export function formatPercent(
  value: number | null | undefined,
  digits = 2,
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return NO_DATA;
  return `${value.toFixed(digits).replace(".", ",")}%`;
}

/** For decimals coming from the pro payload (0.145 -> 14,50%). */
export function formatRate(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return NO_DATA;
  return formatPercent(value * 100, digits);
}

export function formatMoney(
  value: number | null | undefined,
  currency = "KZT",
  digits = 0,
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return NO_DATA;
  const symbol = currency === "KZT" ? "₸" : currency;
  return `${new Intl.NumberFormat("ru-RU", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value)} ${symbol}`;
}

export function formatNumber(
  value: number | null | undefined,
  digits = 2,
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return NO_DATA;
  return new Intl.NumberFormat("ru-RU", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value);
}

export function formatCompact(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return NO_DATA;
  return new Intl.NumberFormat("ru-RU", {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value);
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return NO_DATA;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return NO_DATA;
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  }).format(date);
}

export function formatYears(value: number | null | undefined): string {
  if (value === null || value === undefined) return NO_DATA;
  const rounded = Math.round(value * 10) / 10;
  const plural =
    rounded >= 5 || Math.floor(rounded) === 0
      ? "лет"
      : rounded < 2
        ? "года"
        : "лет";
  return `${formatNumber(rounded, 1)} ${plural}`;
}

export function formatAge(hours: number | null | undefined): string {
  if (hours === null || hours === undefined) return "неизвестно";
  if (hours < 1) return "меньше часа назад";
  if (hours < 24) return `${Math.round(hours)} ч назад`;
  const days = Math.round(hours / 24);
  return `${days} дн назад`;
}

export { NO_DATA };
