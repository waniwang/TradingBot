"use client";

import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { DailyPnl } from "@/lib/types";

export function EquityChart({ data }: { data: DailyPnl[] }) {
  if (data.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Equity Curve</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex h-48 items-center justify-center text-sm text-muted-foreground">
            No P&L data yet
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Cumulative P&L</CardTitle>
        </CardHeader>
        <CardContent>
          <ResponsiveContainer width="100%" height={200}>
            <AreaChart data={data}>
              <defs>
                <linearGradient id="cumGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#22c55e" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#22c55e" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(0 0% 20%)" />
              <XAxis
                dataKey="date"
                tick={{ fontSize: 11, fill: "hsl(0 0% 50%)" }}
                tickFormatter={(v) => v.slice(5)}
              />
              <YAxis
                tick={{ fontSize: 11, fill: "hsl(0 0% 50%)" }}
                tickFormatter={(v) => `$${v}`}
              />
              <Tooltip
                contentStyle={{
                  backgroundColor: "hsl(260 10% 15%)",
                  border: "1px solid hsl(260 10% 25%)",
                  borderRadius: "6px",
                  fontSize: 12,
                }}
                formatter={(value) => [`$${Number(value).toFixed(2)}`, "Cumulative"]}
              />
              <Area
                type="monotone"
                dataKey="cumulative"
                stroke="#22c55e"
                fill="url(#cumGrad)"
                strokeWidth={2}
              />
            </AreaChart>
          </ResponsiveContainer>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Daily P&L</CardTitle>
        </CardHeader>
        <CardContent>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={data}>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(0 0% 20%)" />
              <XAxis
                dataKey="date"
                tick={{ fontSize: 11, fill: "hsl(0 0% 50%)" }}
                tickFormatter={(v) => v.slice(5)}
              />
              <YAxis
                tick={{ fontSize: 11, fill: "hsl(0 0% 50%)" }}
                tickFormatter={(v) => `$${v}`}
              />
              <Tooltip
                contentStyle={{
                  backgroundColor: "hsl(260 10% 15%)",
                  border: "1px solid hsl(260 10% 25%)",
                  borderRadius: "6px",
                  fontSize: 12,
                }}
                formatter={(value) => [`$${Number(value).toFixed(2)}`, "Daily"]}
              />
              <Bar
                dataKey="daily_pnl"
                fill="#22c55e"
                radius={[2, 2, 0, 0]}
                // Color bars by value
                shape={(props: any) => {
                  const { x, y, width, height, payload } = props;
                  const fill = payload.daily_pnl >= 0 ? "#22c55e" : "#ef4444";
                  return (
                    <rect x={x} y={y} width={width} height={height} fill={fill} rx={2} />
                  );
                }}
              />
            </BarChart>
          </ResponsiveContainer>
        </CardContent>
      </Card>
    </div>
  );
}
