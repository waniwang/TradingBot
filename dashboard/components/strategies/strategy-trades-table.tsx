"use client";

import type { ClosedPosition } from "@/lib/types";
import { VariationBadge } from "./variation-badge";

export function StrategyTradesTable({ trades }: { trades: ClosedPosition[] }) {
  if (trades.length === 0) {
    return (
      <p className="py-8 text-center text-sm text-muted-foreground">
        No closed trades for this strategy yet.
      </p>
    );
  }

  // Only EP strategies carry variation — hide the column entirely when all rows are null.
  const showVariation = trades.some((t) => t.variation);

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-xs text-muted-foreground">
            <th className="pb-2 pr-4 text-left font-medium">Date</th>
            <th className="pb-2 pr-4 text-left font-medium">Ticker</th>
            {showVariation && (
              <th className="pb-2 pr-4 text-left font-medium">Variation</th>
            )}
            <th className="pb-2 pr-4 text-right font-medium">Entry</th>
            <th className="pb-2 pr-4 text-right font-medium">Exit</th>
            <th className="pb-2 pr-4 text-right font-medium">P&L</th>
            <th className="pb-2 pr-4 text-right font-medium">Days</th>
            <th className="pb-2 text-left font-medium">Reason</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t) => (
            <tr key={t.id} className="border-b border-border/50">
              <td className="py-2 pr-4 text-xs tabular-nums text-muted-foreground">
                {t.date
                  ? new Date(t.date).toLocaleDateString("en-US", {
                      month: "short",
                      day: "numeric",
                    })
                  : "-"}
              </td>
              <td className="py-2 pr-4 font-medium">{t.ticker}</td>
              {showVariation && (
                <td className="py-2 pr-4">
                  {t.variation ? (
                    <VariationBadge value={t.variation} />
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  )}
                </td>
              )}
              <td className="py-2 pr-4 text-right tabular-nums">
                ${t.entry.toFixed(2)}
              </td>
              <td className="py-2 pr-4 text-right tabular-nums">
                {t.exit != null ? `$${t.exit.toFixed(2)}` : "-"}
              </td>
              <td
                className={`py-2 pr-4 text-right font-medium tabular-nums ${
                  t.pnl >= 0 ? "text-profit" : "text-loss"
                }`}
              >
                ${t.pnl.toFixed(2)}
              </td>
              <td className="py-2 pr-4 text-right tabular-nums">{t.days}</td>
              <td className="py-2 text-xs text-muted-foreground">{t.reason}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
