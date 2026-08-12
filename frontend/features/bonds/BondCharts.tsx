"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { EmptyState, Skeleton } from "@/components/ui/Stat";
import { useCashflows, useHistory } from "@/hooks/useBonds";
import { formatDate, formatMoney, formatNumber } from "@/utils/format";

const AXIS = { fontSize: 11, fill: "#94a3b8" };

export function CashflowChart({
  ticker,
  currency,
}: {
  ticker: string;
  currency: string;
}) {
  const { data, isLoading } = useCashflows(ticker);

  const rows = (data ?? []).map((flow) => ({
    date: formatDate(flow.payment_date),
    coupon: flow.coupon_amount ?? 0,
    principal: flow.principal_amount ?? 0,
  }));

  return (
    <Card>
      <CardHeader
        title="Когда и сколько платят"
        subtitle="График выплат на одну облигацию"
      />
      <CardBody>
        {isLoading ? (
          <Skeleton className="h-56 w-full" />
        ) : rows.length === 0 ? (
          <EmptyState title="Нет предстоящих выплат" />
        ) : (
          <div className="h-56 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={rows} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
                <XAxis dataKey="date" tick={AXIS} tickLine={false} axisLine={false} />
                <YAxis
                  tick={AXIS}
                  tickLine={false}
                  axisLine={false}
                  width={60}
                  tickFormatter={(v: number) => formatNumber(v, 0)}
                />
                <Tooltip
                  formatter={(value: number, name) => [
                    formatMoney(value, currency, 2),
                    name === "coupon" ? "Купон" : "Номинал",
                  ]}
                  contentStyle={{ fontSize: 12, borderRadius: 12 }}
                />
                <Bar dataKey="coupon" stackId="a" fill="#14b8a6" radius={[0, 0, 0, 0]} />
                <Bar dataKey="principal" stackId="a" fill="#6366f1" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </CardBody>
    </Card>
  );
}

export function PriceHistoryChart({ ticker }: { ticker: string }) {
  const { data, isLoading } = useHistory(ticker, 365);

  const rows = (data ?? [])
    .filter((point) => point.clean_price !== null)
    .map((point) => ({
      date: formatDate(point.timestamp),
      price: point.clean_price,
      ytm: point.ytm === null ? null : point.ytm * 100,
    }));

  return (
    <Card>
      <CardHeader title="История цены" subtitle="Чистая цена, % от номинала" />
      <CardBody>
        {isLoading ? (
          <Skeleton className="h-56 w-full" />
        ) : rows.length < 2 ? (
          <EmptyState
            title="Истории пока нет"
            description="Данные появятся после нескольких обновлений котировок."
          />
        ) : (
          <div className="h-56 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={rows} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
                <XAxis dataKey="date" tick={AXIS} tickLine={false} axisLine={false} />
                <YAxis
                  tick={AXIS}
                  tickLine={false}
                  axisLine={false}
                  width={50}
                  domain={["auto", "auto"]}
                />
                <Tooltip
                  formatter={(value: number) => formatNumber(value, 2)}
                  contentStyle={{ fontSize: 12, borderRadius: 12 }}
                />
                <Line
                  type="monotone"
                  dataKey="price"
                  stroke="#0ea5e9"
                  strokeWidth={2}
                  dot={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </CardBody>
    </Card>
  );
}
