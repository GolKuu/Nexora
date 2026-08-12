"use client";

import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { formatCompact, formatDate, formatNumber, formatRate } from "@/utils/format";
import type { BondReference, ProView } from "@/types/api";

const NO_DATA = "—";

function Row({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-slate-100 py-2 last:border-0 dark:border-slate-800">
      <span className="text-sm text-slate-500 dark:text-slate-400">
        {label}
        {note ? (
          <span className="ml-1 text-xs text-slate-400">({note})</span>
        ) : null}
      </span>
      <span className="tabular text-sm font-medium text-slate-900 dark:text-slate-100">
        {value}
      </span>
    </div>
  );
}

/** Only rendered in Pro mode. Everything here comes straight from the backend
 *  calculation engine; the browser computes nothing. */
export function ProDetails({ pro, bond }: { pro: ProView; bond: BondReference }) {
  if (!pro.available) {
    return (
      <Card>
        <CardHeader title="Подробные показатели" />
        <CardBody>
          <p className="text-sm text-slate-500">
            Технические показатели недоступны: нет актуальной рыночной цены.
          </p>
        </CardBody>
      </Card>
    );
  }

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader title="Доходность и цена" />
        <CardBody className="py-1">
          <Row
            label="YTM"
            value={formatRate(pro.ytm)}
            note={pro.ytm_source === "market" ? "биржа" : "расчет"}
          />
          <Row label="Текущая доходность" value={formatRate(pro.current_yield)} />
          <Row label="Clean price" value={formatNumber(pro.clean_price, 4)} />
          <Row label="Dirty price" value={formatNumber(pro.dirty_price, 4)} />
          <Row label="НКД" value={formatNumber(pro.accrued_interest, 4)} />
          <Row label="Pull to par" value={formatRate(pro.pull_to_par)} note="год" />
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Риск" />
        <CardBody className="py-1">
          <Row label="Macaulay duration" value={formatNumber(pro.macaulay_duration, 3)} />
          <Row label="Modified duration" value={formatNumber(pro.modified_duration, 3)} />
          <Row label="Convexity" value={formatNumber(pro.convexity, 2)} />
          <Row label="Credit spread" value={formatRate(pro.credit_spread)} />
          <Row label="Безрисковая ставка" value={formatRate(pro.risk_free_rate)} />
          <Row
            label="Волатильность цены (90д)"
            value={formatRate(pro.price_volatility_90d)}
          />
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Рынок" />
        <CardBody className="py-1">
          <Row label="Bid" value={formatNumber(pro.bid, 4)} />
          <Row label="Ask" value={formatNumber(pro.ask, 4)} />
          <Row label="Спред" value={formatNumber(pro.bid_ask_spread, 4)} />
          <Row label="Спред, % от mid" value={formatRate(pro.bid_ask_spread_pct)} />
          <Row label="Объем" value={formatCompact(pro.volume)} />
          <Row label="Оборот" value={formatCompact(pro.turnover)} />
          <Row
            label="Сделок"
            value={pro.number_of_trades === null || pro.number_of_trades === undefined
              ? NO_DATA
              : String(pro.number_of_trades)}
          />
          <Row
            label="Средний оборот (30д)"
            value={formatCompact(pro.avg_daily_turnover_30d)}
          />
          <Row
            label="Дней с торгами (30д)"
            value={formatNumber(pro.trading_days_30d, 0)}
          />
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Параметры выпуска" />
        <CardBody className="py-1">
          <Row label="ISIN" value={bond.isin ?? NO_DATA} />
          <Row label="Номинал" value={formatNumber(bond.nominal, 0)} />
          <Row label="Купон" value={formatRate(bond.coupon_rate)} note={bond.coupon_type ?? undefined} />
          <Row
            label="Выплат в год"
            value={bond.coupon_frequency ? String(bond.coupon_frequency) : NO_DATA}
          />
          <Row label="Следующий купон" value={formatDate(bond.next_coupon_date)} />
          <Row label="Погашение" value={formatDate(bond.maturity_date)} />
          <Row label="Объем выпуска" value={formatCompact(bond.issue_size)} />
          <Row label="В обращении" value={formatCompact(bond.outstanding_amount)} />
          <Row label="Обеспечен" value={bond.secured === null ? NO_DATA : bond.secured ? "да" : "нет"} />
          <Row
            label="Субординированный"
            value={bond.subordinated === null ? NO_DATA : bond.subordinated ? "да" : "нет"}
          />
          <Row label="Формула" value={pro.formula_version ?? NO_DATA} />
        </CardBody>
      </Card>
    </div>
  );
}
