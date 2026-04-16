"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PhaseBadge } from "@/components/strategies/phase-badge";
import type {
  ConfigParamRow,
  ParamPhase,
  ParamVariation,
  PhaseLabel,
} from "@/lib/types";

/**
 * Renders a strategy's config parameters grouped by variation (Universal / A / B / C).
 * Each row shows the key, live value, description, and a phase badge that tells
 * the user when the value is consulted — at scan, execute, or day-2 confirm time.
 */

const VARIATION_ORDER: ParamVariation[] = ["base", "A", "B", "C"];

const VARIATION_META: Record<
  ParamVariation,
  { title: string; subtitle: string; accent: string }
> = {
  base: {
    title: "Universal Filters",
    subtitle: "Applied to every variation (A, B, and C).",
    accent: "text-muted-foreground",
  },
  A: {
    title: "Strategy A — Tight",
    subtitle: "Strict entry filters; highest win-rate in backtest.",
    accent: "text-blue-400",
  },
  B: {
    title: "Strategy B — Relaxed",
    subtitle: "Looser filters; more entries, slightly lower win-rate.",
    accent: "text-amber-400",
  },
  C: {
    title: "Strategy C — Day-2 Confirm",
    subtitle: "Bear-market setup; requires day-2 price > gap day close.",
    accent: "text-violet-400",
  },
};

function formatValue(v: unknown): string {
  if (v === true) return "true";
  if (v === false) return "false";
  if (v == null) return "-";
  if (typeof v === "number") {
    return Number.isInteger(v) ? v.toLocaleString() : String(v);
  }
  return String(v);
}

export function ParametersCard({
  params,
  phaseLabels,
}: {
  params: ConfigParamRow[];
  phaseLabels: Record<ParamPhase, PhaseLabel> | undefined;
}) {
  // Bucket by variation; drop empty groups so non-EP strategies show a single "base" section.
  const grouped = new Map<ParamVariation, ConfigParamRow[]>();
  for (const p of params) {
    const v = (p.variation ?? "base") as ParamVariation;
    if (!grouped.has(v)) grouped.set(v, []);
    grouped.get(v)!.push(p);
  }

  const orderedGroups = VARIATION_ORDER.flatMap((v) => {
    const rows = grouped.get(v);
    return rows && rows.length > 0 ? [[v, rows] as const] : [];
  });

  // Collapse a single "base"-only group (e.g. breakout) into a flat card.
  const isFlat = orderedGroups.length === 1 && orderedGroups[0][0] === "base";

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">Parameters</CardTitle>
        <p className="text-xs text-muted-foreground">
          Grouped by variation. Each row shows when the value is used — at{" "}
          <span className="text-blue-400">scan</span>,{" "}
          <span className="text-profit">execute</span>, or{" "}
          <span className="text-violet-400">day-2 confirm</span>.
        </p>
      </CardHeader>
      <CardContent className="space-y-5">
        {orderedGroups.map(([variation, rows]) => {
          const meta = VARIATION_META[variation];
          return (
            <div key={variation}>
              {!isFlat && (
                <div className="mb-2 flex items-baseline gap-2">
                  <h4 className={`text-xs font-semibold ${meta.accent}`}>
                    {meta.title}
                  </h4>
                  <span className="text-[10px] text-muted-foreground">
                    {meta.subtitle}
                  </span>
                </div>
              )}
              <div className="grid gap-x-6 gap-y-1.5 sm:grid-cols-2">
                {rows.map((row) => (
                  <ParameterRow
                    key={row.key}
                    row={row}
                    phaseLabels={phaseLabels}
                  />
                ))}
              </div>
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}

function ParameterRow({
  row,
  phaseLabels,
}: {
  row: ConfigParamRow;
  phaseLabels: Record<ParamPhase, PhaseLabel> | undefined;
}) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-border/30 py-1.5">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className="font-mono text-xs text-foreground">{row.key}</span>
          <PhaseBadge phase={row.phase} labels={phaseLabels} />
        </div>
        {row.description && (
          <p className="mt-0.5 text-[11px] leading-snug text-muted-foreground">
            {row.description}
          </p>
        )}
      </div>
      <span className="shrink-0 font-mono text-xs font-medium tabular-nums text-foreground">
        {formatValue(row.value)}
      </span>
    </div>
  );
}
