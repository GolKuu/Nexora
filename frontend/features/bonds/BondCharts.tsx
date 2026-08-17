"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { EmptyState, Skeleton } from "@/components/ui/Stat";
import { useCashflows } from "@/hooks/useBonds";
import { formatDate, formatMoney, formatNumber } from "@/utils/format";

const AXIS = { fontSize: 11, fill: "var(--viz-ink-muted)" };

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

  const LABELS: Record<string, string> = { coupon: "Купон", principal: "Номинал" };

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
          <div className="viz h-64 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={rows} margin={{ top: 8, right: 8, left: 0, bottom: 0 }} accessibilityLayer>
                <CartesianGrid stroke="var(--viz-grid)" vertical={false} />
                <XAxis
                  dataKey="date"
                  tick={AXIS}
                  tickLine={false}
                  axisLine={{ stroke: "var(--viz-axis)" }}
                  minTickGap={24}
                />
                <YAxis
                  tick={AXIS}
                  tickLine={false}
                  axisLine={false}
                  width={60}
                  tickFormatter={(v: number) => formatNumber(v, 0)}
                />
                <Tooltip
                  cursor={{ fill: "var(--viz-grid)", fillOpacity: 0.4 }}
                  formatter={(value: number, name) => [
                    formatMoney(value, currency, 2),
                    LABELS[String(name)] ?? String(name),
                  ]}
                  contentStyle={{ fontSize: 12, borderRadius: 12 }}
                />
                <Legend
                  formatter={(name) => LABELS[String(name)] ?? String(name)}
                  wrapperStyle={{ fontSize: 12 }}
                />
                {/* The 2px stroke in the surface colour is the gap that keeps
                    the two stacked segments apart without a border. */}
                <Bar
                  dataKey="coupon"
                  stackId="a"
                  fill="var(--viz-series-1)"
                  stroke="var(--viz-surface)"
                  strokeWidth={2}
                  maxBarSize={24}
                />
                <Bar
                  dataKey="principal"
                  stackId="a"
                  fill="var(--viz-series-2)"
                  stroke="var(--viz-surface)"
                  strokeWidth={2}
                  maxBarSize={24}
                  radius={[4, 4, 0, 0]}
                />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </CardBody>
    </Card>
  );
}
